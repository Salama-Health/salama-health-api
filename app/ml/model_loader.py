"""
ML model loading and flood-probability inference.

Concurrency model (important for the 100-concurrent-user requirement)
---------------------------------------------------------------------
* Models are loaded ONCE per worker process at startup (`ModelManager.load`).
  Gunicorn runs N worker processes; each holds its own in-memory copy, so there
  is no shared mutable state and no cross-process lock contention.
* Inference (`predict_flood_proba`) is READ-ONLY on the fitted estimators, which
  is safe to call concurrently from FastAPI's threadpool. We do not hold a lock,
  so requests are not serialised.
* User-facing endpoints do NOT run inference per request — CDI/risk scores are
  precomputed daily and cached in PostgreSQL (see services/scoring.py). Inference
  only runs during the scheduled refresh, keeping request latency at DB-read
  speed regardless of concurrency.

Models are loaded LOCALLY from `settings.models_dir`. If a file is missing and
`settings.hf_model_repo` is set, we fall back to downloading from Hugging Face
Hub. If nothing loads, prediction degrades to a transparent rule-based heuristic.
"""
from __future__ import annotations

import json
import logging
import os
import pickle
from pathlib import Path
from typing import Optional

import numpy as np

from app.config import settings
from app.ml.features import FEATURE_NAMES, NUM_FEATURES, RF_FEATURE_NAMES

# Models trained on a named subset of the features rather than the flood
# vector's order. The list comes from the training notebook, not from the
# artifact, so it cannot be verified against the file - see _validate_features.
NAMED_FEATURE_SETS = {"rf": RF_FEATURE_NAMES}

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {"xgb": 0.341, "rf": 0.326, "lstm": 0.333}


class ModelManager:
    """Holds the loaded models for one process."""

    def __init__(self) -> None:
        self.xgb = None
        self.rf = None
        self.lstm = None
        self.weights = dict(DEFAULT_WEIGHTS)
        self.loaded = False
        self.source = "none"          # local | huggingface | mixed | none
        self.issues: list[str] = []   # human-readable reasons a model is unusable
        self._models_dir = Path(settings.models_dir)
        self._origins: dict[str, str] = {}   # filename -> local | huggingface
        self._warned: set[str] = set()       # inference failures already logged
        self._widths: dict[str, int] = {}    # model key -> feature width to send
        self._named: dict[str, list] = {}    # model key -> named feature order

    # ── loading ──────────────────────────────────────────────────────────────
    def _local_path(self, filename: str) -> Optional[Path]:
        p = self._models_dir / filename
        return p if p.exists() else None

    def _resolve(self, filename: str) -> Optional[Path]:
        """Return a local path, downloading from HF Hub if configured & missing."""
        local = self._local_path(filename)
        if local:
            self._origins[filename] = "local"
            return local
        if settings.hf_model_repo:
            try:
                import tempfile

                from huggingface_hub import hf_hub_download

                # Cache to a writable temp dir: models_dir is often mounted
                # read-only, so we must not write the HF cache under it.
                cache_dir = os.path.join(tempfile.gettempdir(), "salama_hf_cache")
                os.makedirs(cache_dir, exist_ok=True)
                logger.info("Fetching %s from HF Hub %s", filename, settings.hf_model_repo)
                downloaded = hf_hub_download(
                    settings.hf_model_repo,
                    filename,
                    cache_dir=cache_dir,
                )
                self._origins[filename] = "huggingface"
                return Path(downloaded)
            except Exception as exc:  # noqa: BLE001
                logger.warning("HF download failed for %s: %s", filename, exc)
        return None

    @staticmethod
    def _load_pickle(path: Path):
        # SECURITY: pickle executes arbitrary code on load. This is acceptable
        # here ONLY because the files are first-party model artifacts the team
        # trained and placed in `models_dir` (or pushed to its own HF repo) — a
        # trusted source under our control, never user-supplied input. Do not
        # point models_dir / hf_model_repo at third-party content.
        with open(path, "rb") as fh:
            return pickle.load(fh)  # noqa: S301 (trusted first-party artifact)

    def _validate_features(self, model, name: str, key: str) -> bool:
        """
        Check a model against the feature vector we actually send.

        A width mismatch does NOT fail at unpickle time - it fails later inside
        `predict_proba`, where the exception is caught per-model and the
        ensemble quietly renormalises over whatever survived. The result looks
        like a perfectly plausible probability, so the drop is invisible in the
        output. Resolving it here turns a silent wrong answer into either a
        correct one or a loud failure.

        Three outcomes:
        * exact width match -> use the full vector;
        * narrower, but `feature_names_in_` matches a PREFIX of FEATURE_NAMES
          in order -> trim the vector to that width for this model. The Phase 2
          XGBoost artifact is this case: trained on the first 29 features,
          before `flood_affected_norm` was appended;
        * anything else -> refuse the model. Without names we cannot know which
          columns it wants, and guessing would silently feed it wrong data.
        """
        n = getattr(model, "n_features_in_", None)
        if n is None:
            # Not all estimators expose it (e.g. a raw xgboost.Booster). We
            # cannot verify, so we keep the model rather than drop it blindly.
            logger.warning(
                "%s exposes no n_features_in_ - feature contract unverified", name
            )
            self._widths[key] = NUM_FEATURES
            return True

        n = int(n)
        if n == NUM_FEATURES:
            logger.info("%s feature contract OK (%d features)", name, n)
            self._widths[key] = n
            return True

        names = getattr(model, "feature_names_in_", None)
        if names is not None and list(names) == FEATURE_NAMES[:n]:
            msg = (
                f"{name} was trained on {n} features, the first {n} of the "
                f"{NUM_FEATURES}-feature order, verified by name. Trimming "
                f"{FEATURE_NAMES[n:]} for this model."
            )
            logger.warning(msg)
            self.issues.append(msg)
            self._widths[key] = n
            return True

        # A model saved from a bare array carries no names, so the artifact
        # cannot tell us which columns it wants. If the training notebook has
        # supplied that list and the width agrees, use it - the width match is
        # a consistency check, NOT proof the order is right. If the list is
        # wrong the model will return plausible numbers from wrong columns, so
        # it is recorded as an issue and surfaced in /health.
        expected = NAMED_FEATURE_SETS.get(key)
        if names is None and expected and n == len(expected):
            msg = (
                f"{name} was trained on {n} named features supplied by the "
                f"training notebook, not by the artifact. Width agrees, but the "
                f"order cannot be verified from the file - if the list is wrong, "
                f"its output is wrong but still plausible."
            )
            logger.warning(msg)
            self.issues.append(msg)
            self._named[key] = list(expected)
            self._widths[key] = n
            return True

        detail = (
            "and exposes no feature_names_in_, so the columns it wants cannot "
            "be recovered from the artifact"
            if names is None
            else "and its feature names do not match our training order"
        )
        msg = (
            f"{name} was trained on {n} features but the backend sends "
            f"{NUM_FEATURES} {detail} - dropping it. Retrain it on the "
            f"{NUM_FEATURES}-feature vector, or supply its feature list."
        )
        logger.error(msg)
        self.issues.append(msg)
        return False

    def load(self) -> None:
        """Load all models. Safe to call once at startup; idempotent."""
        if self.loaded:
            return

        # XGBoost flood model
        xgb_path = self._resolve(settings.xgb_model_file)
        if xgb_path:
            try:
                candidate = self._load_pickle(xgb_path)
                if self._validate_features(candidate, "XGBoost", "xgb"):
                    self.xgb = candidate
                    logger.info("Loaded XGBoost model from %s", xgb_path)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load XGBoost model: %s", exc)
                self.issues.append(f"XGBoost failed to load: {exc}")

        # Random Forest model
        rf_path = self._resolve(settings.rf_model_file)
        if rf_path:
            try:
                candidate = self._load_pickle(rf_path)
                if self._validate_features(candidate, "RandomForest", "rf"):
                    self.rf = candidate
                    logger.info("Loaded Random Forest model from %s", rf_path)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load Random Forest model: %s", exc)
                self.issues.append(f"Random Forest failed to load: {exc}")

        # Optional LSTM (PyTorch) — only if torch is installed and file present
        lstm_path = self._resolve(settings.lstm_model_file)
        if lstm_path:
            try:
                import torch  # noqa: WPS433  (optional heavy dep)

                # Prefer weights_only=True (safe, tensors-only). Fall back to a
                # full unpickle only for trusted first-party checkpoints that
                # serialise the whole nn.Module (same trust boundary as above).
                try:
                    self.lstm = torch.load(
                        lstm_path, map_location="cpu", weights_only=True
                    )
                except Exception:  # noqa: BLE001  (full-module checkpoint)
                    self.lstm = torch.load(
                        lstm_path, map_location="cpu", weights_only=False
                    )
                if hasattr(self.lstm, "eval"):
                    self.lstm.eval()
                # NOTE: predict_flood_proba feeds the LSTM the same flat
                # (1, NUM_FEATURES) vector as the tree models — NOT the 7-week
                # sequence it was trained on. Until features.py assembles
                # sequences, a loaded LSTM will either raise (and be dropped
                # from the blend) or emit a meaningless probability.
                msg = (
                    "LSTM loaded, but the live path sends a flat "
                    f"{NUM_FEATURES}-feature vector, not a sequence - its "
                    "output is not trustworthy until sequence assembly exists"
                )
                logger.warning(msg)
                self.issues.append(msg)
                logger.info("Loaded LSTM model from %s", lstm_path)
            except ImportError:
                logger.info("torch not installed — skipping LSTM model")
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load LSTM model: %s", exc)

        # Ensemble weights
        weights_path = self._resolve(settings.ensemble_weights_file)
        if weights_path:
            try:
                with open(weights_path) as fh:
                    self.weights = json.load(fh)
                logger.info("Loaded ensemble weights: %s", self.weights)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load ensemble weights, using defaults: %s", exc)

        if self.xgb is not None or self.rf is not None or self.lstm is not None:
            # Report where the loaded models actually came from, per file —
            # not just where XGBoost was found.
            origins = {
                self._origins[f]
                for f, obj in (
                    (settings.xgb_model_file, self.xgb),
                    (settings.rf_model_file, self.rf),
                    (settings.lstm_model_file, self.lstm),
                )
                if obj is not None and f in self._origins
            }
            if len(origins) == 1:
                self.source = origins.pop()
            elif origins:
                self.source = "mixed"
            else:
                self.source = "local"
        else:
            self.source = "none"
            msg = "No ML models loaded — flood prediction will use rule-based fallback."
            if settings.require_models:
                raise RuntimeError(msg + " (REQUIRE_MODELS is set)")
            logger.warning(msg)

        self.loaded = True

    # ── inference ──────────────────────────────────────────────────────────────
    def predict_flood_proba(
        self, feature_vector: np.ndarray, named: Optional[dict] = None
    ) -> float:
        """
        P(flood) in [0, 1] from a 30-feature vector.
        Weighted ensemble of available models; rule-based fallback otherwise.
        Read-only — safe to call concurrently.
        """
        x = np.asarray(feature_vector, dtype=np.float32).reshape(1, -1)
        probas, weights = [], []

        if self.xgb is not None:
            try:
                xi = self._x_for("xgb", x, named)
                if xi is not None:
                    probas.append(float(self.xgb.predict_proba(xi)[0, 1]))
                    weights.append(self.weights.get("xgb", DEFAULT_WEIGHTS["xgb"]))
            except Exception as exc:  # noqa: BLE001
                self._warn_once("xgb", exc)

        if self.rf is not None:
            try:
                xi = self._x_for("rf", x, named)
                if xi is None:
                    # Named features were not supplied by this caller. Skip
                    # rather than guess at columns.
                    self._warn_once("rf", RuntimeError(
                        "named features not supplied; pass compute_named_features(inputs)"
                    ))
                else:
                    probas.append(float(self.rf.predict_proba(xi)[0, 1]))
                    weights.append(self.weights.get("rf", DEFAULT_WEIGHTS["rf"]))
            except Exception as exc:  # noqa: BLE001
                self._warn_once("rf", exc)

        if self.lstm is not None:
            try:
                import torch

                with torch.no_grad():
                    t = torch.from_numpy(self._x_for("lstm", x))
                    out = self.lstm(t)
                    p = torch.sigmoid(out).flatten()[0].item() if out.numel() == 1 \
                        else torch.softmax(out, dim=-1).flatten()[-1].item()
                probas.append(float(p))
                weights.append(self.weights.get("lstm", DEFAULT_WEIGHTS["lstm"]))
            except Exception as exc:  # noqa: BLE001
                self._warn_once("lstm", exc)

        if probas:
            total = sum(weights) or 1.0
            return float(sum(p * w for p, w in zip(probas, weights)) / total)

        return self._rule_based_flood(feature_vector)

    @staticmethod
    def _rule_based_flood(vec: np.ndarray) -> float:
        """Transparent fallback using SAR backscatter + rainfall signals."""
        vv = float(vec[0])         # VV_backscatter
        rain = float(vec[6])       # chirps_rainfall_mm
        rain_anom = float(vec[7])  # chirps_anomaly
        p = 0.0
        if vv < -14:
            p += 0.5
        if rain > 30:
            p += 0.2
        if rain_anom > 10:
            p += 0.1
        return float(min(p, 1.0))

    def _x_for(self, key: str, x: np.ndarray, named: Optional[dict] = None) -> np.ndarray:
        """Build this model's input.

        A model with a named feature order gets its columns selected by name
        from `named`; everything else gets the flood vector trimmed to the
        width verified at load time. Never pads, never reorders positionally.
        Returns None when a named model's features were not supplied, so the
        caller skips it rather than feeding it the wrong columns.
        """
        cols = self._named.get(key)
        if cols is not None:
            if not named:
                return None
            missing = [c for c in cols if c not in named]
            if missing:
                raise KeyError(f"missing named features: {missing}")
            return np.array([[named[c] for c in cols]], dtype=np.float32)
        w = self._widths.get(key, NUM_FEATURES)
        return x if w >= x.shape[1] else x[:, :w]

    def _warn_once(self, key: str, exc: Exception) -> None:
        """Log an inference failure once per process, then record it in status.

        These used to be logger.debug, which is invisible at the production
        LOG_LEVEL — a model could fail every call and nothing would show it.
        """
        msg = f"{key} inference failed: {exc}"
        if key not in self._warned:
            self._warned.add(key)
            logger.warning("%s (further occurrences suppressed)", msg)
            self.issues.append(msg)

    def status(self) -> dict:
        # A model that is loaded but throwing at inference contributes nothing,
        # so it must not be reported as active - that is the exact blind spot
        # this status block exists to close.
        active = [n for n, m in
                  (("xgb", self.xgb), ("rf", self.rf), ("lstm", self.lstm))
                  if m is not None and n not in self._warned]
        return {
            "loaded": self.loaded,
            "source": self.source,
            "xgb": self.xgb is not None,
            "rf": self.rf is not None,
            "lstm": self.lstm is not None,
            "weights": self.weights,
            "expected_features": NUM_FEATURES,
            # Per-model width actually sent (a trimmed model shows < expected).
            "feature_widths": dict(self._widths),
            # Models whose columns are selected by name from a list supplied by
            # the training notebook rather than read from the artifact.
            "named_feature_models": sorted(self._named),
            # What P(flood) is actually being produced by, right now.
            "predicting_with": active or "rule_based_fallback",
            # Non-empty means something is degraded — check before trusting scores.
            "issues": self.issues,
        }


# Process-level singleton. `load()` is called from the app lifespan at startup.
model_manager = ModelManager()
