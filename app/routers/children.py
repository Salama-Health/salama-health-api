"""Children registry: list, detail, create, update, QR lookup."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import Child, Worker
from app.schemas.child import ChildCreate, ChildDetail, ChildOut, ChildUpdate
from app.schemas.vaccination import VaccinationOut
from app.services import scoring
from app.services.activity_service import log_activity

router = APIRouter()


def _to_out(db: Session, child: Child) -> ChildOut:
    out = ChildOut.model_validate(child)
    risk = scoring.get_current_risk(db, child.id)
    if risk:
        out.risk_score = risk.risk_score
        out.risk_band = risk.risk_label
    out.due_vaccines = scoring.due_vaccines_for(child)
    return out


def _to_detail(db: Session, child: Child) -> ChildDetail:
    detail = ChildDetail.model_validate(child)
    risk = scoring.get_current_risk(db, child.id)
    if risk:
        detail.risk_score = risk.risk_score
        detail.risk_band = risk.risk_label
    detail.due_vaccines = scoring.due_vaccines_for(child)
    detail.history = [VaccinationOut.from_orm_record(v) for v in child.vaccinations]
    return detail


@router.get("", response_model=list[ChildOut])
def list_children(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    facility_id: Optional[str] = Query(None, alias="facilityId"),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    q = db.query(Child)
    q = q.filter(Child.worker_id == (worker_id or current.id))
    if facility_id:
        q = q.filter(Child.facility_id == facility_id)
    if status_filter:
        q = q.filter(Child.status == status_filter)
    children = q.all()
    return [_to_out(db, c) for c in children]


@router.get("/lookup", response_model=ChildDetail)
def lookup_by_qr(
    qr: str = Query(..., description="QR code payload scanned from the child card"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    child = db.query(Child).filter(Child.qr_code == qr).first()
    if not child:
        raise HTTPException(status_code=404, detail="No child matches that QR code")
    return _to_detail(db, child)


@router.get("/{child_id}", response_model=ChildDetail)
def get_child(
    child_id: str,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    child = db.query(Child).filter(Child.id == child_id).first()
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")
    return _to_detail(db, child)


@router.post("", response_model=ChildDetail, status_code=201)
def create_child(
    payload: ChildCreate,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    # Idempotency for offline re-uploads
    if payload.client_uuid:
        existing = db.query(Child).filter(Child.qr_code == payload.client_uuid).first()
        if existing:
            return _to_detail(db, existing)

    child = Child(
        name=payload.name,
        gender=payload.gender,
        born_date=payload.born_date,
        facility_id=payload.facility_id or current.facility_id,
        worker_id=payload.worker_id or current.id,
        parent_name=payload.parent_name,
        parent_phone=payload.parent_phone,
        current_location=payload.current_location,
        distance_km=payload.distance_km,
        latitude=payload.latitude,
        longitude=payload.longitude,
        qr_code=payload.qr_code or payload.client_uuid,
    )
    db.add(child)
    db.commit()
    db.refresh(child)

    # Score the new child immediately so the card isn't blank.
    scoring.refresh_child_risk(db, child)
    log_activity(
        db, current.id, "registration",
        "New child registered", f"{child.name} added to your register",
        payload={"childId": child.id},
    )
    return _to_detail(db, child)


@router.patch("/{child_id}", response_model=ChildDetail)
def update_child(
    child_id: str,
    payload: ChildUpdate,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    child = db.query(Child).filter(Child.id == child_id).first()
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(child, field, value)
    db.commit()
    db.refresh(child)
    return _to_detail(db, child)
