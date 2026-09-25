"""Vaccination records: history per child, record a dose."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import Child, Vaccination, Worker
from app.schemas.vaccination import VaccinationCreate, VaccinationOut
from app.services import registration, scoring
from app.services.activity_service import log_activity

router = APIRouter()


@router.get("", response_model=list[VaccinationOut])
def list_vaccinations(
    child_id: Optional[str] = Query(None, alias="childId"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    """Doses for one child, or every dose this worker has administered.

    With `childId` this is the child's timeline. Without it, it is the
    worker's own record of what they have given, across all their children -
    which the client cannot otherwise assemble without one request per child.

    The worker view joins the child so each row can name them; the per-child
    view leaves `childName` null, since it is already scoped to one child.
    """
    if child_id:
        records = (
            db.query(Vaccination)
            .filter(Vaccination.child_id == child_id)
            .order_by(Vaccination.date_given.desc().nullslast())
            .limit(limit)
            .all()
        )
        return [VaccinationOut.from_orm_record(v) for v in records]

    rows = (
        db.query(Vaccination, Child.name)
        .join(Child, Child.id == Vaccination.child_id)
        .filter(Vaccination.administered_by == current.id)
        .order_by(Vaccination.date_given.desc().nullslast())
        .limit(limit)
        .all()
    )
    return [VaccinationOut.from_orm_record(v, child_name=name) for v, name in rows]


@router.post("", response_model=VaccinationOut, status_code=201)
def record_vaccination(
    payload: VaccinationCreate,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    child = db.query(Child).filter(Child.id == payload.child_id).first()
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")

    # Idempotency for offline re-uploads
    if payload.client_uuid:
        existing = (
            db.query(Vaccination)
            .filter(Vaccination.client_uuid == payload.client_uuid)
            .first()
        )
        if existing:
            return VaccinationOut.from_orm_record(existing)

    record = registration.build_vaccination(payload, current)
    db.add(record)
    db.commit()
    db.refresh(record)

    # A new dose changes the child's vaccination debt -> rescore.
    scoring.refresh_child_risk(db, child)
    log_activity(
        db, current.id, "vaccination",
        "Vaccination recorded", f"{record.vaccine} for {child.name}",
        payload={"childId": child.id, "vaccine": record.vaccine},
    )
    return VaccinationOut.from_orm_record(record)
