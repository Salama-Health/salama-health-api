"""
Climate CDI router — facility-level Climate Disruption Index + hazard forecast.

Serves cached CDI scores. `/refresh` recomputes all scores in the background
(runs the ML ensemble); normal reads never trigger inference.
"""
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import SessionLocal, get_db
from app.db.models import CDIScore, Facility, VVHistory, Worker
from app.schemas.climate import CDIComponents, CDIOut, SarUploadResult
from app.schemas.common import Message
from app.services import ingest, scoring
from app.services.pipeline import run_weekly_cdi_pipeline

router = APIRouter()


def _latest_sar(db: Session, facility_ids: list[str]) -> dict:
    """Latest radar observation date per facility, in one grouped query.

    Done in bulk so the facility list does not fan out into one query per row.
    """
    if not facility_ids:
        return {}
    rows = (
        db.query(VVHistory.facility_id, func.max(VVHistory.observed_at))
        .filter(VVHistory.facility_id.in_(facility_ids))
        .group_by(VVHistory.facility_id)
        .all()
    )
    return {fid: observed for fid, observed in rows}


def _to_out(db: Session, f: Facility, row: CDIScore, sar_at=None) -> CDIOut:
    return CDIOut(
        facility_id=f.id,
        facility_name=f.name,
        county=f.county,
        state=f.state,
        cdi_score=row.cdi_score,
        risk=row.risk_level,
        hazard=row.hazard,
        days_to_window=row.days_to_window or 0,
        hazard_detail=row.hazard_detail or "",
        hazard_timeframe=row.hazard_timeframe or "",
        components=CDIComponents(
            p_flood=row.p_flood or 0.0,
            p_cutoff=row.p_cutoff or 0.0,
            p_ccf=row.p_ccf or 0.0,
            p_disp=row.p_disp or 0.0,
        ),
        scored_at=row.scored_at,
        sar_observed_at=sar_at,
    )


def _ensure_cdi(db: Session, f: Facility) -> CDIScore:
    row = scoring.get_current_cdi(db, f.id)
    if row is None:
        row = scoring.refresh_facility_cdi(db, f)
    return row


@router.get("/facilities", response_model=list[CDIOut])
def get_all_cdi(
    region: Optional[str] = Query(None, description="Filter by state"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    q = db.query(Facility).filter(Facility.active == True)  # noqa: E712
    if region:
        q = q.filter(Facility.state == region)
    facilities = q.all()
    sar = _latest_sar(db, [f.id for f in facilities])
    return [_to_out(db, f, _ensure_cdi(db, f), sar.get(f.id)) for f in facilities]


@router.get("/facilities/{facility_id}", response_model=CDIOut)
def get_facility_cdi(
    facility_id: str,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    f = db.query(Facility).filter(Facility.id == facility_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Facility not found")
    return _to_out(db, f, _ensure_cdi(db, f), _latest_sar(db, [f.id]).get(f.id))


@router.post("/upload-sar", response_model=SarUploadResult)
async def upload_sar(
    file: UploadFile = File(..., description="CSV: facility_name, VV_backscatter"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    """
    Weekly Sentinel-1 SAR upload (Section 2.1). Appends one VVHistory row per
    matched facility; these feed the VV_backscatter/VV_7day_mean/VV_delta features.
    """
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Expected a .csv file")
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded CSV")

    result = ingest.store_sar_csv(db, text)
    return SarUploadResult(matched=result["matched"], skipped=result["skipped"])


@router.post("/refresh", response_model=Message)
def refresh_cdi(
    background_tasks: BackgroundTasks,
    current: Worker = Depends(get_current_worker),
):
    """
    Run the full weekly CDI pipeline in the background (CHIRPS + Open-Meteo +
    recompute all CDI/risk scores). This is the endpoint the weekly scheduler
    or an external cron calls.
    """
    def _job():
        db = SessionLocal()
        try:
            run_weekly_cdi_pipeline(db)
        finally:
            db.close()

    background_tasks.add_task(_job)
    return Message(message="Weekly CDI pipeline queued")
