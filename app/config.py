"""
Application configuration.

All settings are environment-driven (12-factor). Defaults are safe for local
development; production values come from the environment / .env file.
"""
from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_name: str = "Salama Health API"
    environment: str = "development"          # development | staging | production
    debug: bool = False
    api_v1_prefix: str = ""                   # set to "/api/v1" if you want versioning

    # ── Database (local PostgreSQL on the AWS instance) ─────────────────────
    # Example: postgresql+psycopg2://salama:salama@localhost:5432/salama
    database_url: str = "postgresql+psycopg2://salama:salama@localhost:5432/salama"

    # Connection pool — tuned so (workers * (pool_size + max_overflow)) stays
    # comfortably under PostgreSQL's max_connections (default 100).
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: int = 30                 # seconds to wait for a connection
    db_pool_recycle: int = 1800               # recycle connections every 30 min
    db_pool_pre_ping: bool = True             # detect dropped connections

    # ── Auth / JWT ──────────────────────────────────────────────────────────
    secret_key: str = "CHANGE-ME-IN-PRODUCTION-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12        # 12h — field shifts are long
    refresh_token_expire_minutes: int = 60 * 24 * 30  # 30 days offline-friendly

    # ── ML models (loaded locally from disk) ────────────────────────────────
    # Directory on the instance / mounted volume that holds the model files.
    models_dir: str = "./models"
    xgb_model_file: str = "SSD_XGBoost_Flood_Model.pkl"
    rf_model_file: str = "SSD_RF_Model.pkl"
    lstm_model_file: str = "SSD_LSTM_Model.pt"
    ensemble_weights_file: str = "ensemble_weights.json"

    # Optional fallback: pull models from Hugging Face Hub if not found locally.
    hf_model_repo: str = ""                   # e.g. "mubarakabanadda/salama-cdi-models"

    # If True the API refuses to start when no models are found (production).
    require_models: bool = False

    # ── Scoring / scheduler ─────────────────────────────────────────────────
    # CDI depends on daily climate data, so we precompute & cache scores rather
    # than running inference on every request (see services/scoring.py).
    enable_scheduler: bool = True
    cdi_refresh_cron_hour: int = 2            # daily CDI recompute at 02:00
    cdi_refresh_cron_minute: int = 0
    score_cache_ttl_minutes: int = 60 * 6     # treat cached scores stale after 6h

    # ── CORS ────────────────────────────────────────────────────────────────
    cors_origins: List[str] = ["*"]           # tighten to the app's origin in prod

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so settings are parsed once per process."""
    return Settings()


settings = get_settings()
