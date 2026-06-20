"""
Weekly CDI pipeline (Section 7 of the data spec).

Orchestrates the full run:
  1. Download CHIRPS rainfall for all pilot counties -> store to rainfall_history
  2. Fetch Open-Meteo climate per facility (rate-limited)
  3. (SAR VV is uploaded out-of-band via /climate/upload-sar -> vv_history)
  4. Recompute CDI per facility + IGS per child, cached to the DB

Every external call is best-effort; missing data falls back to stored history
or seasonal estimates, so the run always completes.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import Facility
from app.services import ingest, scoring

logger = logging.getLogger(__name__)


def run_weekly_cdi_pipeline(db: Session) -> dict:
    started = datetime.utcnow()
    logger.info("Weekly CDI pipeline starting at %s UTC", started.isoformat())

    # Step 1: CHIRPS rainfall for all counties -> history
    chirps = ingest.fetch_chirps_weekly()
    counties_written = ingest.store_chirps_history(db, chirps) if chirps else 0

    # Step 2: Open-Meteo climate per facility
    facilities = db.query(Facility).filter(Facility.active == True).all()  # noqa: E712
    climate_by_facility = ingest.fetch_openmeteo_all(facilities)
    climate_ok = sum(1 for v in climate_by_facility.values() if v)

    # Step 3: Sentinel-1 SAR (VV backscatter) from Earth Engine -> vv_history.
    # No-op if GEE isn't configured; manual /climate/upload-sar still works too.
    vv_by_facility = ingest.fetch_sar_all(facilities)
    sar_written = ingest.store_sar_history(db, vv_by_facility) if vv_by_facility else 0

    # Step 4 + 5: recompute and cache all scores using the fresh data
    result = scoring.refresh_all_scores(db, climate_by_facility)

    duration = (datetime.utcnow() - started).total_seconds()
    summary = {
        "facilities_scored": result["facilities"],
        "children_scored": result["children"],
        "counties_rainfall_updated": counties_written,
        "facilities_climate_fetched": climate_ok,
        "facilities_sar_updated": sar_written,
        "duration_seconds": round(duration, 1),
        "ran_at": started.isoformat(),
    }
    logger.info("Weekly CDI pipeline complete: %s", summary)
    return summary
