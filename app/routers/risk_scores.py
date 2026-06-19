"""
Child risk scoring (IGS) — the core differentiating feature.

Reads cached scores (fast, scales with Postgres). If a child has no cached
score yet, it is computed on the fly and persisted so subsequent reads are cheap.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import Child, RiskScore, Worker
from app.schemas.risk import RiskComponents, RiskScoreOut
from app.services import scoring

router = APIRouter()


def _to_out(db: Session, child: Child, row: RiskScore) -> RiskScoreOut:
    cdi = scoring.get_current_cdi(db, child.facility_id)
    return RiskScoreOut(
        child_id=child.id,
        child_name=child.name,
        risk_score=row.risk_score,
        risk_label=row.risk_label,
        overdue_count=row.overdue_count or 0,
        facility_id=child.facility_id,
        days_to_window=cdi.days_to_window if cdi else None,
        components=RiskComponents(
            cdi=row.cdi or 0.0,
            vaccination_debt=row.vaccination_debt or 0.0,
            accessibility=row.accessibility or 0.0,
            age_urgency=row.age_urgency or 0.0,
        ),
        scored_at=row.scored_at,
    )


def _ensure_score(db: Session, child: Child) -> RiskScore:
    row = scoring.get_current_risk(db, child.id)
    if row is None:
        row = scoring.refresh_child_risk(db, child)
    return row


@router.get("", response_model=list[RiskScoreOut])
def get_risk_scores(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    """Risk scores for all children of a worker, sorted high → low."""
    children = (
        db.query(Child).filter(Child.worker_id == (worker_id or current.id)).all()
    )
    out = [_to_out(db, c, _ensure_score(db, c)) for c in children]
    out.sort(key=lambda x: x.risk_score, reverse=True)
    return out


@router.get("/child/{child_id}", response_model=RiskScoreOut)
def get_child_risk(
    child_id: str,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    child = db.query(Child).filter(Child.id == child_id).first()
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")
    return _to_out(db, child, _ensure_score(db, child))
