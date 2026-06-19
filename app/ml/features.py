"""
Feature engineering for the flood model.

Builds the 30-feature vector the models were trained on. In production the
weather/SAR features should be filled from live CHIRPS + Open-Meteo + Sentinel-1
data for each facility's coordinates; until that pipeline is wired, we derive
seasonal estimates from the facility's static characteristics so the ensemble
still produces sensible, varying scores.

Feature order MUST match training:
    VV_backscatter, VV_7day_mean, VV_delta, flood_signal, elevation_m,
    low_elevation, chirps_rainfall_mm, chirps_anomaly, rainfall_roll30d,
    rainfall_lag7d, rainfall_lag14d, rainfall_lag28d, rainfall_anomaly,
    temp_max_celsius, temp_min_celsius, temp_anomaly, temp_excess_8c, ccf_risk,
    cumulative_heat_7d, freeze_risk, humidity_pct, evapotranspiration,
    water_deficit, drought_signal, idp_normalised, season_sin, season_cos,
    rainy_season, month
"""
import math
from datetime import datetime

import numpy as np

# Observed max in the CHIRPS training dataset — used to normalise rainfall.
CHIRPS_RAIN_MAX = 103.8


def is_rainy_season(month: int) -> bool:
    """South Sudan rainy season ~April–November."""
    return 4 <= month <= 11


def build_feature_vector(elevation_m: float | None, now: datetime | None = None) -> np.ndarray:
    """
    Build the 30-feature vector for a facility.

    Replace the seasonal estimates below with live API values keyed on the
    facility's latitude/longitude when the climate-ingestion pipeline is ready.
    """
    now = now or datetime.utcnow()
    month = now.month
    doy = now.timetuple().tm_yday

    elevation = elevation_m if elevation_m is not None else 400.0
    low_elev = 1.0 if elevation < 400 else 0.0
    rainy = 1.0 if is_rainy_season(month) else 0.0

    baseline_rain = 45.0 if rainy else 5.0
    temp_max = 38.0 if rainy else 34.0
    temp_min = 22.0
    t_excess = max(temp_max - 8.0, 0.0)
    ccf_risk = 1 - math.exp(-0.012 * t_excess)

    vec = np.array([
        -12.0,                              # VV_backscatter
        -12.0,                              # VV_7day_mean
        0.0,                                # VV_delta
        0.0,                                # flood_signal
        elevation,                          # elevation_m
        low_elev,                           # low_elevation
        baseline_rain,                      # chirps_rainfall_mm
        5.0,                                # chirps_anomaly
        baseline_rain * 4,                  # rainfall_roll30d
        baseline_rain * 0.8,                # rainfall_lag7d
        baseline_rain * 0.7,                # rainfall_lag14d
        baseline_rain * 0.5,                # rainfall_lag28d
        5.0,                                # rainfall_anomaly
        temp_max,                           # temp_max_celsius
        temp_min,                           # temp_min_celsius
        1.0,                                # temp_anomaly
        t_excess,                           # temp_excess_8c
        ccf_risk,                           # ccf_risk
        t_excess * 7,                       # cumulative_heat_7d
        0.0,                                # freeze_risk
        65.0,                               # humidity_pct
        4.5,                                # evapotranspiration
        max(4.5 - baseline_rain, 0.0),      # water_deficit
        0.0 if baseline_rain > 10 else 1.0, # drought_signal
        0.3,                                # idp_normalised
        math.sin(2 * math.pi * doy / 365),  # season_sin
        math.cos(2 * math.pi * doy / 365),  # season_cos
        rainy,                              # rainy_season
        float(month),                       # month
    ], dtype=np.float32)

    return vec
