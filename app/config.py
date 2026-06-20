"""
Application configuration.

All settings are environment-driven (12-factor). Defaults are safe for local
development; production values come from the environment / .env file.
"""
from functools import lru_cache
from typing import List

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
    hf_model_repo: str = "MubarakB/salama-cdi-models"

    # If True the API refuses to start when no models are found (production).
    require_models: bool = False

    # ── Scoring / scheduler ─────────────────────────────────────────────────
    # The CDI pipeline runs WEEKLY (climate data is dekadal/weekly). Scores are
    # precomputed & cached, so reads never run inference (see services/scoring.py).
    enable_scheduler: bool = True
    # Weekly cron: day_of_week 0=Sunday .. 6=Saturday (APScheduler "sun".."sat")
    cdi_refresh_cron_day: str = "sun"         # weekly run on Sunday
    cdi_refresh_cron_hour: int = 0            # 00:00 UTC
    cdi_refresh_cron_minute: int = 0
    score_cache_ttl_minutes: int = 60 * 24 * 7  # weekly freshness window

    # ── Climate ingestion (Section 2 of the data spec) ───────────────────────
    open_meteo_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_timezone: str = "Africa/Nairobi"
    open_meteo_request_delay_s: float = 2.0   # ~30 req/min free-tier limit
    chirps_hdx_package_url: str = (
        "https://data.humdata.org/api/3/action/package_show?id=ssd-rainfall-subnational"
    )
    # Pilot coverage (states whose counties we ingest CHIRPS for).
    # Stored as a plain comma separated string so any pydantic-settings version
    # accepts it; use `pilot_states_list` for the parsed list.
    pilot_states: str = "Unity,Jonglei,Upper Nile"

    # Cold-chain constants (WHO upper safe storage limit + calibrated lambda)
    cold_chain_safe_c: float = 8.0
    ccf_lambda: float = 0.012
    chirps_rain_max: float = 103.8            # observed max weekly rainfall (mm)

    # ── Sentinel-1 SAR via Google Earth Engine (headless, service account) ───
    # When set, the weekly pipeline pulls VV backscatter from GEE automatically.
    # If unset, SAR falls back to the estimate (or the manual /climate/upload-sar).
    gee_service_account: str = ""             # service account email
    gee_key_file: str = ""                    # path to the SA JSON key in-container
    gee_project: str = ""                     # GCP project registered for Earth Engine
    sar_window_days: int = 12                 # Sentinel-1 revisit is ~6-12 days
    sar_buffer_m: int = 500                   # averaging radius around each facility

    # ── CORS ────────────────────────────────────────────────────────────────
    # Comma separated string ("*" or "https://a,https://b"); see cors_origins_list.
    cors_origins: str = "*"                   # tighten in prod

    @staticmethod
    def _split_csv(value: str) -> List[str]:
        return [v.strip() for v in value.split(",") if v.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        return self._split_csv(self.cors_origins)

    @property
    def pilot_states_list(self) -> List[str]:
        return self._split_csv(self.pilot_states)

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so settings are parsed once per process."""
    return Settings()


settings = get_settings()
