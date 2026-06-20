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
        self.source = "none"          # local | huggingface | none
        self._models_dir = Path(settings.models_dir)

    # ── loading ──────────────────────────────────────────────────────────────
    def _local_path(self, filename: str) -> Optional[Path]:
        p = self._models_dir / filename
        return p if p.exists() else None

    def _resolve(self, filename: str) -> Optional[Path]:
        """Return a local path, downloading from HF Hub if configured & missing."""
        local = self._local_path(filename)
        if local:
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

    def load(self) -> None:
        """Load all models. Safe to call once at startup; idempotent."""
        if self.loaded:
            return

        # XGBoost flood model
        xgb_path = self._resolve(settings.xgb_model_file)
        if xgb_path:
            try:
                self.xgb = self._load_pickle(xgb_path)
                logger.info("Loaded XGBoost model from %s", xgb_path)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load XGBoost model: %s", exc)

        # Random Forest model
        rf_path = self._resolve(settings.rf_model_file)
        if rf_path:
            try:
                self.rf = self._load_pickle(rf_path)
                logger.info("Loaded Random Forest model from %s", rf_path)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load Random Forest model: %s", exc)

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
            self.source = "huggingface" if settings.hf_model_repo and not self._local_path(
                settings.xgb_model_file
            ) else "local"
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
                logger.debug("XGB inference failed: %s", exc)

        if self.rf is not None:
            try:
                probas.append(float(self.rf.predict_proba(x)[0, 1]))
                weights.append(self.weights.get("rf", DEFAULT_WEIGHTS["rf"]))
            except Exception as exc:  # noqa: BLE001
                logger.debug("RF inference failed: %s", exc)

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
                logger.debug("LSTM inference failed: %s", exc)

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

    def status(self) -> dict:
        return {
            "loaded": self.loaded,
            "source": self.source,
            "xgb": self.xgb is not None,
            "rf": self.rf is not None,
            "lstm": self.lstm is not None,
            "weights": self.weights,
        }


# Process-level singleton. `load()` is called from the app lifespan at startup.
model_manager = ModelManager()
