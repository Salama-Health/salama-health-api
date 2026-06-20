"""
Climate data ingestion (Section 2 of the data spec).

Three sources:
  A. Sentinel-1 SAR  -> uploaded weekly as a CSV (see routers/climate.upload_sar)
  B. CHIRPS rainfall -> downloaded from HDX (fetch_chirps_weekly)
  C. Open-Meteo      -> per-facility climate (fetch_openmeteo)

All network calls are best-effort: on failure the pipeline falls back to
seasonal estimates so a missing source never crashes the weekly run.

This module also reads/writes the history tables used to build rolling-window
features (RainfallHistory, VVHistory).
"""
from __future__ import annotations

import csv
import io
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import County, Facility, RainfallHistory, VVHistory

logger = logging.getLogger(__name__)


# ── Source C: Open-Meteo ──────────────────────────────────────────────────────
def fetch_openmeteo(lat: float, lng: float, now: Optional[datetime] = None) -> Optional[dict]:
    """
    Fetch 7 days of daily climate for a coordinate and return aggregates.
    Returns None on failure (caller falls back to estimates).
    """
    now = now or datetime.utcnow()
    start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    params = {
        "latitude": lat,
        "longitude": lng,
        "start_date": start,
        "end_date": end,
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "relative_humidity_2m_max",
            "et0_fao_evapotranspiration",
        ]),
        "timezone": settings.open_meteo_timezone,
    }
    try:
        resp = httpx.get(settings.open_meteo_url, params=params, timeout=30)
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        tmax = [v for v in daily.get("temperature_2m_max", []) if v is not None]
        tmin = [v for v in daily.get("temperature_2m_min", []) if v is not None]
        hum = [v for v in daily.get("relative_humidity_2m_max", []) if v is not None]
        et0 = [v for v in daily.get("et0_fao_evapotranspiration", []) if v is not None]
        if not tmax:
            return None
        return {
            "temp_max": max(tmax),
            "temp_min": min(tmin) if tmin else min(tmax),
            "humidity": sum(hum) / len(hum) if hum else 65.0,
            "et0": sum(et0) if et0 else 4.5,
            "daily_temps_max": tmax,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Open-Meteo fetch failed for (%s,%s): %s", lat, lng, exc)
        return None


# ── Source B: CHIRPS via HDX ──────────────────────────────────────────────────
def fetch_chirps_weekly() -> Dict[str, Tuple[float, float]]:
    """
    Download the latest CHIRPS subnational CSV from HDX and return, per P-code,
    the most recent (rfh, rfh_avg). Returns {} on failure.
    """
    try:
        meta = httpx.get(settings.chirps_hdx_package_url, timeout=30)
        meta.raise_for_status()
        resources = meta.json()["result"]["resources"]
        csv_url = next(
            (r["url"] for r in resources
             if r.get("format", "").lower() == "csv"
             and "subnat" in r.get("name", "").lower()),
            None,
        )
        if not csv_url:
            csv_url = next((r["url"] for r in resources
                            if r.get("format", "").lower() == "csv"), None)
        if not csv_url:
            logger.warning("No CHIRPS CSV resource found on HDX")
            return {}

        data = httpx.get(csv_url, timeout=60, follow_redirects=True)
        data.raise_for_status()
        return _parse_chirps_csv(data.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("CHIRPS fetch failed: %s", exc)
        return {}


def _parse_chirps_csv(text: str) -> Dict[str, Tuple[float, float]]:
    """Keep the most recent (rfh, rfh_avg) per PCODE."""
    reader = csv.DictReader(io.StringIO(text))
    latest: Dict[str, Tuple[str, float, float]] = {}
    for row in reader:
        pcode = (row.get("PCODE") or row.get("pcode") or "").strip()
        date = (row.get("date") or "").strip()
        if not pcode or not date or date.lower() == "#date":   # skip HXL tag row
            continue
        try:
            rfh = float(row.get("rfh") or 0)
            rfh_avg = float(row.get("rfh_avg") or 0)
        except ValueError:
            continue
        prev = latest.get(pcode)
        if prev is None or date > prev[0]:
            latest[pcode] = (date, rfh, rfh_avg)
    return {p: (v[1], v[2]) for p, v in latest.items()}


def store_chirps_history(db: Session, chirps_by_pcode: Dict[str, Tuple[float, float]],
                         now: Optional[datetime] = None) -> int:
    """Write this week's rainfall per county into RainfallHistory."""
    now = now or datetime.utcnow()
    woy = now.isocalendar().week
    counties = db.query(County).filter(County.pcode.isnot(None)).all()
    written = 0
    for c in counties:
        rec = chirps_by_pcode.get(c.pcode)
        if not rec:
            continue
        rfh, rfh_avg = rec
        db.add(RainfallHistory(
            county=c.name, observed_date=now, year=now.year,
            week_of_year=woy, rfh=rfh, rfh_avg=rfh_avg,
        ))
        written += 1
    db.commit()
    logger.info("Stored CHIRPS rainfall for %d counties", written)
    return written


# ── History readers (for feature building) ────────────────────────────────────
def get_rainfall_history(db: Session, county: Optional[str], weeks: int = 5) -> List[float]:
    if not county:
        return []
    rows = (
        db.query(RainfallHistory)
        .filter(RainfallHistory.county == county)
        .order_by(RainfallHistory.observed_date.desc())
        .limit(weeks)
        .all()
    )
    return [r.rfh for r in rows if r.rfh is not None]


def get_latest_rfh_avg(db: Session, county: Optional[str]) -> float:
    if not county:
        return 0.0
    row = (
        db.query(RainfallHistory)
        .filter(RainfallHistory.county == county)
        .order_by(RainfallHistory.observed_date.desc())
        .first()
    )
    return row.rfh_avg if row and row.rfh_avg is not None else 0.0


def get_vv_history(db: Session, facility_id: str, n: int = 3) -> List[float]:
    rows = (
        db.query(VVHistory)
        .filter(VVHistory.facility_id == facility_id)
        .order_by(VVHistory.observed_at.desc())
        .limit(n)
        .all()
    )
    return [r.vv_backscatter for r in rows if r.vv_backscatter is not None]


def get_county_idp_normalised(db: Session, county: Optional[str]) -> float:
    if not county:
        return 0.0
    row = db.query(County).filter(County.name == county).first()
    if not row or not row.idp_count:
        return 0.0
    max_idp = (
        db.query(County)
        .filter(County.state.in_(settings.pilot_states_list))
        .order_by(County.idp_count.desc())
        .first()
    )
    denom = max_idp.idp_count if max_idp and max_idp.idp_count else 1
    return float(row.idp_count) / float(max(denom, 1))


# ── SAR CSV ingestion ─────────────────────────────────────────────────────────
def store_sar_csv(db: Session, csv_text: str, now: Optional[datetime] = None) -> dict:
    """
    Parse an uploaded SAR CSV (columns: facility_name, VV_backscatter) and
    append a VVHistory row per matched facility.
    """
    now = now or datetime.utcnow()
    reader = csv.DictReader(io.StringIO(csv_text))
    matched, skipped = 0, []
    for row in reader:
        name = (row.get("facility_name") or row.get("name") or "").strip()
        raw = row.get("VV_backscatter") or row.get("vv_backscatter")
        if not name or raw in (None, ""):
            continue
        try:
            vv = float(raw)
        except ValueError:
            skipped.append(name)
            continue
        facility = db.query(Facility).filter(Facility.name == name).first()
        if not facility:
            skipped.append(name)
            continue
        db.add(VVHistory(facility_id=facility.id, vv_backscatter=vv, observed_at=now))
        matched += 1
    db.commit()
    return {"matched": matched, "skipped": skipped}


def fetch_openmeteo_all(facilities: List[Facility]) -> Dict[str, Optional[dict]]:
    """Fetch Open-Meteo for many facilities, rate-limited per the spec."""
    out: Dict[str, Optional[dict]] = {}
    for f in facilities:
        if f.latitude is None or f.longitude is None:
            out[f.id] = None
            continue
        out[f.id] = fetch_openmeteo(f.latitude, f.longitude)
        time.sleep(settings.open_meteo_request_delay_s)
    return out


# ── Source A (automated): Sentinel-1 SAR via Google Earth Engine ───────────────
def fetch_sar_all(facilities: List[Facility]) -> Dict[str, float]:
    """
    Headless GEE pull: mean Sentinel-1 VV backscatter (dB) within
    `sar_buffer_m` of each facility over the last `sar_window_days`.

    Returns {facility_id: vv}. Empty dict if GEE isn't configured or on failure
    (the pipeline then keeps the previous VV / estimate). Requires a service
    account (settings.gee_service_account + gee_key_file [+ gee_project]).
    """
    if not (settings.gee_service_account and settings.gee_key_file):
        logger.info("GEE not configured; skipping automated SAR fetch")
        return {}

    coords = [(f.id, f.longitude, f.latitude)
              for f in facilities if f.latitude is not None and f.longitude is not None]
    if not coords:
        return {}

    try:
        import ee

        creds = ee.ServiceAccountCredentials(
            settings.gee_service_account, settings.gee_key_file
        )
        ee.Initialize(creds, project=settings.gee_project or None)

        now = datetime.utcnow()
        end = ee.Date(now.strftime("%Y-%m-%d"))
        start = end.advance(-settings.sar_window_days, "day")
        s1 = (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterDate(start, end)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .select("VV")
            .mean()
        )

        feats = [
            ee.Feature(ee.Geometry.Point([lng, lat]), {"fid": fid})
            for (fid, lng, lat) in coords
        ]
        fc = ee.FeatureCollection(feats)
        buf = settings.sar_buffer_m

        def _add_vv(ft):
            v = s1.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=ft.geometry().buffer(buf),
                scale=10,
                maxPixels=int(1e9),
            ).get("VV")
            return ft.set("vv", v)

        info = fc.map(_add_vv).getInfo()
        result: Dict[str, float] = {}
        for ft in info.get("features", []):
            props = ft.get("properties", {})
            vv = props.get("vv")
            if vv is not None:
                result[props["fid"]] = float(vv)
        logger.info("GEE SAR: got VV for %d/%d facilities", len(result), len(coords))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("GEE SAR fetch failed: %s", exc)
        return {}


def store_sar_history(db: Session, vv_by_facility: Dict[str, float],
                      now: Optional[datetime] = None) -> int:
    """Append one VVHistory row per facility from a {facility_id: vv} map."""
    now = now or datetime.utcnow()
    written = 0
    for fid, vv in vv_by_facility.items():
        db.add(VVHistory(facility_id=fid, vv_backscatter=vv, observed_at=now))
        written += 1
    db.commit()
    return written
