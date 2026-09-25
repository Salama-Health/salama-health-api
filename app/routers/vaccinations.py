"""Vaccination records: history per child, record a dose."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, aliased

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import Child, Facility, Vaccination, Worker
from app.ml import epi
from app.schemas.vaccination import VaccinationCreate, VaccinationOut
from app.services import registration, scoring
from app.services.activity_service import log_activity

router = APIRouter()


@router.get("", response_model=list[VaccinationOut])
def list_vaccinations(
    child_id: Optional[str] = Query(None, alias="childId"),
    scope: str = Query("mine", pattern="^(mine|region)$"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    """Doses for one child, or every dose this worker has administered.

    With `childId` this is the child's timeline. Without it, it is the
    worker's own record of what they have given, across all their children -
    which the client cannot otherwise assemble without one request per child.

    `scope=region` widens that to every dose given in the worker's county,
    whoever gave it, which is how a worker sees coverage around them rather
    than only their own caseload. Ignored when `childId` is set.

    Every row names who administered the dose, so a worker reading a child's
    card can tell their own work from a colleague's.
    """
    giver = aliased(Worker)

    if child_id:
        rows = (
            db.query(Vaccination, giver.name)
            .outerjoin(giver, giver.id == Vaccination.administered_by)
            .filter(Vaccination.child_id == child_id)
            .order_by(Vaccination.date_given.desc().nullslast())
            .limit(limit)
            .all()
        )
        return [
            VaccinationOut.from_orm_record(v, administered_by=by)
            for v, by in rows
        ]

    q = (
        db.query(Vaccination, Child.name, giver.name)
        .join(Child, Child.id == Vaccination.child_id)
        .outerjoin(giver, giver.id == Vaccination.administered_by)
    )

    if scope == "region":
        # Everything given in the worker's county, whoever gave it - a
        # supervisor's view, and how a CHW sees coverage around them rather
        # than only their own caseload.
        county = _worker_county(db, current)
        if not county:
            return []
        q = q.join(Facility, Facility.id == Child.facility_id).filter(
            Facility.county == county
        )
    else:
        q = q.filter(Vaccination.administered_by == current.id)

    rows = q.order_by(Vaccination.date_given.desc().nullslast()).limit(limit).all()
    return [
        VaccinationOut.from_orm_record(v, child_name=cname, administered_by=by)
        for v, cname, by in rows
    ]


def _worker_county(db: Session, worker: Worker) -> Optional[str]:
    """The worker's county, preferring their facility's over their own field.

    The facility is the authoritative location; `Worker.county` is a
    convenience copy that the seed fills from the facility and a hand-entered
    worker might not have at all.
    """
    if worker.facility_id:
        facility = (
            db.query(Facility).filter(Facility.id == worker.facility_id).first()
        )
        if facility and facility.county:
            return facility.county
    return worker.county


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

    # Refuse doses that should not be given, rather than relying on the client
    # to hide them. Every `detail` here is shown to the health worker verbatim.
    if payload.status == "given":
        if payload.vaccine not in epi.EPI_SCHEDULE:
            raise HTTPException(
                status_code=400,
                detail=f"{payload.vaccine} is not on the national schedule.",
            )

        already = next(
            (v for v in child.vaccinations
             if v.vaccine == payload.vaccine and v.status == "given"),
            None,
        )
        if already:
            when = already.date_given.strftime("%d %b %Y") if already.date_given else "earlier"
            raise HTTPException(
                status_code=409,
                detail=f"{child.name} already received {payload.vaccine} on {when}.",
            )

        # Due age, not the overdue grace period: a dose given on schedule is
        # not yet "overdue", but it is certainly allowed.
        due_at = epi.EPI_SCHEDULE[payload.vaccine]
        age_weeks = scoring.age_weeks_of(child)
        if age_weeks < due_at:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{payload.vaccine} is not due yet. {child.name} is "
                    f"{int(age_weeks)} weeks old; it is given from "
                    f"{due_at} weeks."
                ),
            )

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
