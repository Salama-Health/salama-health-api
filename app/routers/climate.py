"""
Climate CDI router — facility-level Climate Disruption Index + hazard forecast.

Serves cached CDI scores. `/refresh` recomputes all scores in the background
(runs the ML ensemble); normal reads never trigger inference.
"""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import SessionLocal, get_db
from app.db.models import CDIScore, Facility, Worker
from app.schemas.climate import CDIComponents, CDIOut
from app.schemas.common import Message
from app.services import scoring

router = APIRouter()


def _to_out(db: Session, f: Facility, row: CDIScore) -> CDIOut:
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
    return [_to_out(db, f, _ensure_cdi(db, f)) for f in facilities]


@router.get("/facilities/{facility_id}", response_model=CDIOut)
def get_facility_cdi(
    facility_id: str,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    f = db.query(Facility).filter(Facility.id == facility_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Facility not found")
    return _to_out(db, f, _ensure_cdi(db, f))


@router.post("/refresh", response_model=Message)
def refresh_cdi(
    background_tasks: BackgroundTasks,
    current: Worker = Depends(get_current_worker),
):
    """Recompute CDI + child risk for all facilities/children in the background."""
    def _job():
        db = SessionLocal()
        try:
            scoring.refresh_all_scores(db)
        finally:
            db.close()

    background_tasks.add_task(_job)
    return Message(message="Score refresh queued")
