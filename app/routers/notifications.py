"""
Device registration + alerts.

Alerts are derived live from current CDI scores (approaching hazard windows)
and overdue children. Push delivery (FCM/APNs) would consume the registered
device tokens; this endpoint exposes the in-app alert list.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import CDIScore, Child, Device, Facility, RiskScore, Worker
from app.schemas.common import Message
from app.schemas.notification import AlertOut, DeviceRegister

router = APIRouter()


@router.post("/register", response_model=Message)
def register_device(
    payload: DeviceRegister,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    existing = db.query(Device).filter(Device.fcm_token == payload.fcm_token).first()
    if existing:
        existing.worker_id = current.id
        existing.platform = payload.platform
    else:
        db.add(Device(
            worker_id=current.id,
            fcm_token=payload.fcm_token,
            platform=payload.platform,
        ))
    db.commit()
    return Message(message="Device registered")


@router.get("/alerts", response_model=list[AlertOut])
def get_alerts(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    wid = worker_id or current.id
    alerts: list[AlertOut] = []
    now = datetime.utcnow()

    # Facilities the worker covers, with a dangerous/approaching CDI window
    facility_ids = {
        fid for (fid,) in db.query(Child.facility_id)
        .filter(Child.worker_id == wid).distinct() if fid
    }
    if current.facility_id:
        facility_ids.add(current.facility_id)

    if facility_ids:
        cdi_rows = (
            db.query(CDIScore, Facility)
            .join(Facility, Facility.id == CDIScore.facility_id)
            .filter(
                CDIScore.facility_id.in_(facility_ids),
                CDIScore.is_current == True,            # noqa: E712
                CDIScore.risk_level.in_(["Medium", "High", "Critical"]),
            )
            .all()
        )
        for cdi, fac in cdi_rows:
            alerts.append(AlertOut(
                id=f"hazard-{cdi.id}",
                type="hazard",
                title=f"{cdi.hazard} risk at {fac.name}",
                body=cdi.hazard_detail or "Climate hazard window approaching.",
                facility_id=fac.id,
                severity="danger" if cdi.risk_level in ("High", "Critical") else "warning",
                created_at=cdi.scored_at or now,
            ))

    # High-risk children
    high = (
        db.query(RiskScore, Child)
        .join(Child, Child.id == RiskScore.child_id)
        .filter(
            Child.worker_id == wid,
            RiskScore.is_current == True,                # noqa: E712
            RiskScore.risk_label == "High",
        )
        .all()
    )
    for risk, child in high:
        alerts.append(AlertOut(
            id=f"overdue-{risk.id}",
            type="overdue",
            title=f"{child.name} is high risk",
            body=f"{risk.overdue_count or 0} overdue dose(s). Prioritise this visit.",
            child_id=child.id,
            severity="danger",
            created_at=risk.scored_at or now,
        ))

    alerts.sort(key=lambda a: a.created_at, reverse=True)
    return alerts
