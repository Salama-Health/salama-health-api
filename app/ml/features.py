"""
Feature engineering for the flood model (Section 3 + 4 of the data spec).

`compute_feature_vector(inputs)` turns the raw inputs into the **30**-feature
numpy array in the exact training order. `FeatureInputs` carries the raw values;
`estimated_inputs()` produces a sensible fallback when live ingestion data is
not yet available (dev / early pilot weeks) so the system still returns scores.

Feature order (index : name) — MUST match training:
    0  VV_backscatter       10 rainfall_lag14d      20 humidity_pct
    1  VV_7day_mean         11 rainfall_lag28d      21 evapotranspiration
    2  VV_delta             12 rainfall_anomaly     22 water_deficit
    3  flood_signal         13 temp_max_celsius     23 drought_signal
    4  elevation_m          14 temp_min_celsius     24 idp_normalised
    5  low_elevation        15 temp_anomaly         25 season_sin
    6  chirps_rainfall_mm   16 temp_excess_8c       26 season_cos
    7  chirps_anomaly       17 ccf_risk             27 rainy_season
    8  rainfall_roll30d     18 cumulative_heat_7d   28 month
    9  rainfall_lag7d       19 freeze_risk          29 flood_affected_norm
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import numpy as np

from app.config import settings

NUM_FEATURES = 30

# The training feature order, by name. This is the contract the model
# artifacts are checked against at load time (see app/ml/model_loader.py):
# a model that exposes `feature_names_in_` must match a prefix of this list,
# in this order, or it is refused rather than allowed to fail silently at
# inference. Keep in sync with `compute_feature_vector` below.
FEATURE_NAMES = [
    "VV_backscatter",       # 0
    "VV_7day_mean",         # 1
    "VV_delta",             # 2
    "flood_signal",         # 3
    "elevation_m",          # 4
    "low_elevation",        # 5
    "chirps_rainfall_mm",   # 6
    "chirps_anomaly",       # 7
    "rainfall_roll30d",     # 8
    "rainfall_lag7d",       # 9
    "rainfall_lag14d",      # 10
    "rainfall_lag28d",      # 11
    "rainfall_anomaly",     # 12
    "temp_max_celsius",     # 13
    "temp_min_celsius",     # 14
    "temp_anomaly",         # 15
    "temp_excess_8c",       # 16
    "ccf_risk",             # 17
    "cumulative_heat_7d",   # 18
    "freeze_risk",          # 19
    "humidity_pct",         # 20
    "evapotranspiration",   # 21
    "water_deficit",        # 22
    "drought_signal",       # 23
    "idp_normalised",       # 24
    "season_sin",           # 25
    "season_cos",           # 26
    "rainy_season",         # 27
    "month",                # 28
    # NOTE: the Phase 2 XGBoost artifact was trained WITHOUT this last
    # feature - it expects the 29 above. The loader detects that by name and
    # trims the vector for that model rather than dropping it.
    "flood_affected_norm",  # 29
]
assert len(FEATURE_NAMES) == NUM_FEATURES


def is_rainy_season(month: int) -> bool:
    """South Sudan rainy season ~April–November."""
    return 4 <= month <= 11


def _at(seq: List[float], idx: int, default: float = 0.0) -> float:
    """Safe list access with fallback to the last element, then default."""
    if not seq:
        return default
    if idx < len(seq):
        return float(seq[idx])
    return float(seq[-1])


@dataclass
class FeatureInputs:
    # SAR — most recent first, up to 3 values
    vv_history: List[float] = field(default_factory=list)
    # Rainfall (mm) — most recent first, up to 5 weekly values
    rain_history: List[float] = field(default_factory=list)
    rfh_avg: float = 0.0                       # CHIRPS long-term avg this week
    rainfall_climatology_mean: Optional[float] = None  # mean same-week all years

    # Open-Meteo aggregates over the past 7 days
    temp_max: float = 34.0
    temp_min: float = 22.0
    humidity: float = 65.0
    et0: float = 4.5
    daily_temps_max: List[float] = field(default_factory=list)
    temp_climatology_mean: Optional[float] = None

    # Static / per-facility
    elevation_m: float = 400.0
    idp_normalised: float = 0.0
    flood_affected_norm: float = 0.0

    # Date context
    now: datetime = field(default_factory=datetime.utcnow)


def compute_feature_vector(i: FeatureInputs) -> np.ndarray:
    T_SAFE = settings.cold_chain_safe_c
    LAMBDA = settings.ccf_lambda

    month = i.now.month
    doy = i.now.timetuple().tm_yday
    rainy = 1.0 if is_rainy_season(month) else 0.0

    # ── SAR ───────────────────────────────────────────────────────────────
    vv = i.vv_history or [-12.0]
    vv_backscatter = float(vv[0])
    vv_7day_mean = float(sum(vv) / len(vv))
    vv_delta = vv_backscatter - _at(vv, 1, vv_backscatter)
    flood_signal = 1.0 if vv_backscatter < -14.0 else 0.0

    # ── Elevation ─────────────────────────────────────────────────────────
    elevation = i.elevation_m if i.elevation_m is not None else 400.0
    low_elevation = 1.0 if elevation < 400 else 0.0

    # ── Rainfall ──────────────────────────────────────────────────────────
    rain = i.rain_history or [5.0]
    chirps_rainfall_mm = float(rain[0])
    chirps_anomaly = chirps_rainfall_mm - i.rfh_avg
    rainfall_roll30d = float(sum(rain[:4]))
    rainfall_lag7d = _at(rain, 1, chirps_rainfall_mm)
    rainfall_lag14d = _at(rain, 2, chirps_rainfall_mm)
    rainfall_lag28d = _at(rain, 4, chirps_rainfall_mm)
    clim_rain = i.rainfall_climatology_mean
    if clim_rain is None:
        clim_rain = sum(rain) / len(rain)
    rainfall_anomaly = chirps_rainfall_mm - clim_rain

    # ── Temperature / cold chain ──────────────────────────────────────────
    temp_max = float(i.temp_max)
    temp_min = float(i.temp_min)
    temp_excess_8c = max(temp_max - T_SAFE, 0.0)
    ccf_risk = 1 - math.exp(-LAMBDA * temp_excess_8c)
    daily_max = i.daily_temps_max or [temp_max] * 7
    cumulative_heat_7d = float(sum(max(t - T_SAFE, 0.0) for t in daily_max))
    freeze_risk = 1.0 if temp_min < 0.0 else 0.0
    clim_temp = i.temp_climatology_mean
    temp_anomaly = (temp_max - clim_temp) if clim_temp is not None else 1.0

    # ── Humidity / drought ────────────────────────────────────────────────
    humidity_pct = float(i.humidity)
    evapotranspiration = float(i.et0)
    water_deficit = evapotranspiration - chirps_rainfall_mm
    drought_signal = 1.0 if water_deficit > 20.0 else 0.0

    # ── Seasonality ───────────────────────────────────────────────────────
    season_sin = math.sin(2 * math.pi * doy / 365)
    season_cos = math.cos(2 * math.pi * doy / 365)

    vec = np.array([
        vv_backscatter,        # 0
        vv_7day_mean,          # 1
        vv_delta,              # 2
        flood_signal,          # 3
        elevation,             # 4
        low_elevation,         # 5
        chirps_rainfall_mm,    # 6
        chirps_anomaly,        # 7
        rainfall_roll30d,      # 8
        rainfall_lag7d,        # 9
        rainfall_lag14d,       # 10
        rainfall_lag28d,       # 11
        rainfall_anomaly,      # 12
        temp_max,              # 13
        temp_min,              # 14
        temp_anomaly,          # 15
        temp_excess_8c,        # 16
        ccf_risk,              # 17
        cumulative_heat_7d,    # 18
        freeze_risk,           # 19
        humidity_pct,          # 20
        evapotranspiration,    # 21
        water_deficit,         # 22
        drought_signal,        # 23
        i.idp_normalised,      # 24
        season_sin,            # 25
        season_cos,            # 26
        rainy,                 # 27
        float(month),          # 28
        i.flood_affected_norm, # 29
    ], dtype=np.float32)

    assert vec.shape[0] == NUM_FEATURES, f"expected {NUM_FEATURES} features, got {vec.shape[0]}"
    return vec


def estimated_inputs(
    elevation_m: Optional[float],
    flood_affected_norm: float = 0.0,
    idp_normalised: float = 0.0,
    now: Optional[datetime] = None,
) -> FeatureInputs:
    """
    Seasonal-estimate inputs for when no live ingestion data exists yet.
    Keeps scores plausible and varying until the weekly pipeline has run.
    """
    now = now or datetime.utcnow()
    rainy = is_rainy_season(now.month)
    baseline_rain = 45.0 if rainy else 5.0
    temp_max = 38.0 if rainy else 34.0

    return FeatureInputs(
        vv_history=[-12.0, -12.0, -12.0],
        rain_history=[baseline_rain] * 5,
        rfh_avg=baseline_rain * 0.9,
        rainfall_climatology_mean=baseline_rain * 0.9,
        temp_max=temp_max,
        temp_min=22.0,
        humidity=65.0,
        et0=4.5,
        daily_temps_max=[temp_max] * 7,
        temp_climatology_mean=temp_max - 1.0,
        elevation_m=elevation_m if elevation_m is not None else 400.0,
        idp_normalised=idp_normalised,
        flood_affected_norm=flood_affected_norm,
        now=now,
    )
