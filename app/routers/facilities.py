"""Facilities: list (with assignment + cached CDI roll-up), detail, create."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import Child, Facility, RiskScore, Worker
from app.schemas.facility import FacilityCreate, FacilityOut
from app.services import scoring

router = APIRouter()


def _facility_out(db: Session, f: Facility, assigned_ids: set[str]) -> FacilityOut:
    # Build explicitly: the ORM's `children` is a relationship (list), which
    # would clash with the scalar `children` count field under model_validate.
    out = FacilityOut(
        id=f.id,
        name=f.name,
        county=f.county,
        state=f.state,
        latitude=f.latitude,
        longitude=f.longitude,
    )
    out.assigned = f.id in assigned_ids

    out.children = db.query(Child).filter(Child.facility_id == f.id).count()
    out.recently_visited = (
        db.query(Child)
        .filter(Child.facility_id == f.id, Child.status == "visited")
        .count()
    )
    out.due_soon = (
        db.query(Child)
        .filter(Child.facility_id == f.id, Child.status == "toVisit")
        .count()
    )
    out.high_priority = (
        db.query(Child)
        .join(RiskScore, RiskScore.child_id == Child.id)
        .filter(
            Child.facility_id == f.id,
            RiskScore.is_current == True,            # noqa: E712
            RiskScore.risk_label.in_(["High", "Medium"]),
        )
        .count()
    )

    cdi = scoring.get_current_cdi(db, f.id)
    if cdi:
        out.cdi_score = cdi.cdi_score
        out.risk = cdi.risk_level
        out.hazard = cdi.hazard
        out.days_to_window = cdi.days_to_window
        out.hazard_detail = cdi.hazard_detail
        out.hazard_timeframe = cdi.hazard_timeframe
    return out


def _assigned_ids(db: Session, worker: Worker) -> set[str]:
    ids = {
        fid for (fid,) in db.query(Child.facility_id)
        .filter(Child.worker_id == worker.id).distinct()
        if fid
    }
    if worker.facility_id:
        ids.add(worker.facility_id)
    return ids


@router.get("", response_model=list[FacilityOut])
def list_facilities(
    region: Optional[str] = Query(None, description="Filter by state"),
    assigned_only: bool = Query(False, alias="assignedOnly"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    assigned = _assigned_ids(db, current)
    q = db.query(Facility).filter(Facility.active == True)  # noqa: E712
    if region:
        q = q.filter(Facility.state == region)
    facilities = q.all()
    out = [_facility_out(db, f, assigned) for f in facilities]
    if assigned_only:
        out = [f for f in out if f.assigned]
    return out


@router.get("/{facility_id}", response_model=FacilityOut)
def get_facility(
    facility_id: str,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    f = db.query(Facility).filter(Facility.id == facility_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Facility not found")
    return _facility_out(db, f, _assigned_ids(db, current))


@router.post("", response_model=FacilityOut, status_code=201)
def create_facility(
    payload: FacilityCreate,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    f = Facility(**payload.model_dump())
    db.add(f)
    db.commit()
    db.refresh(f)
    scoring.refresh_facility_cdi(db, f)
    return _facility_out(db, f, _assigned_ids(db, current))
