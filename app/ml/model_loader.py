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
from app.ml.features import NUM_FEATURES

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

    def _validate_features(self, model, name: str) -> bool:
        """
        Reject a model whose training width != the vector we actually send.

        A mismatch does NOT fail at unpickle time — it fails later inside
        `predict_proba`, where the exception is caught per-model and the
        ensemble quietly renormalises over whatever survived. The result looks
        like a perfectly plausible probability, so the drop is invisible in the
        output. Catching it here turns a silent wrong answer into a loud one.
        """
        n = getattr(model, "n_features_in_", None)
        if n is None:
            # Not all estimators expose it (e.g. a raw xgboost.Booster). We
            # cannot verify, so we keep the model rather than drop it blindly.
            logger.warning(
                "%s exposes no n_features_in_ - feature contract unverified", name
            )
            return True
        if int(n) != NUM_FEATURES:
            msg = (
                f"{name} was trained on {int(n)} features but the backend sends "
                f"{NUM_FEATURES} - dropping it (would fail at inference and be "
                f"silently excluded from the ensemble)"
            )
            logger.error(msg)
            self.issues.append(msg)
            return False
        logger.info("%s feature contract OK (%d features)", name, int(n))
        return True

    def load(self) -> None:
        """Load all models. Safe to call once at startup; idempotent."""
        if self.loaded:
            return

        # XGBoost flood model
        xgb_path = self._resolve(settings.xgb_model_file)
        if xgb_path:
            try:
                candidate = self._load_pickle(xgb_path)
                if self._validate_features(candidate, "XGBoost"):
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
                if self._validate_features(candidate, "RandomForest"):
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
    def predict_flood_proba(self, feature_vector: np.ndarray) -> float:
        """
        P(flood) in [0, 1] from a 30-feature vector.
        Weighted ensemble of available models; rule-based fallback otherwise.
        Read-only — safe to call concurrently.
        """
        x = np.asarray(feature_vector, dtype=np.float32).reshape(1, -1)
        probas, weights = [], []

        if self.xgb is not None:
            try:
                probas.append(float(self.xgb.predict_proba(x)[0, 1]))
                weights.append(self.weights.get("xgb", DEFAULT_WEIGHTS["xgb"]))
            except Exception as exc:  # noqa: BLE001
                self._warn_once("xgb", exc)

        if self.rf is not None:
            try:
                probas.append(float(self.rf.predict_proba(x)[0, 1]))
                weights.append(self.weights.get("rf", DEFAULT_WEIGHTS["rf"]))
            except Exception as exc:  # noqa: BLE001
                self._warn_once("rf", exc)

        if self.lstm is not None:
            try:
                import torch

                with torch.no_grad():
                    t = torch.from_numpy(x)
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
            # What P(flood) is actually being produced by, right now.
            "predicting_with": active or "rule_based_fallback",
            # Non-empty means something is degraded — check before trusting scores.
            "issues": self.issues,
        }


# Process-level singleton. `load()` is called from the app lifespan at startup.
model_manager = ModelManager()
