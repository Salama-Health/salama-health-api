"""
Offline-first sync.

The device records vaccinations / registrations / visits while offline and
uploads them in a batch. Every record carries a client-generated UUID so
re-uploads de-duplicate (idempotent). Status reports last sync + pending count.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import Child, SyncRecord, Vaccination, Worker
from app.schemas.sync import SyncStatus, SyncUploadRequest, SyncUploadResult
from app.services import registration, scoring
from app.services.activity_service import log_activity

router = APIRouter()


@router.post("/upload", response_model=SyncUploadResult)
def upload(
    payload: SyncUploadRequest,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    result = SyncUploadResult(synced_at=datetime.utcnow())
    touched_children: set[str] = set()

    # 1) New children
    for c in payload.new_children:
        if c.client_uuid and db.query(Child).filter(Child.qr_code == c.client_uuid).first():
            result.duplicates_skipped += 1
            continue
        try:
            child = registration.build_child(c, current)
            db.add(child)
            db.flush()
            touched_children.add(child.id)
            result.children_saved += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"child {c.name}: {exc}")

    # 2) Vaccinations
    for v in payload.vaccinations:
        if v.client_uuid and db.query(Vaccination).filter(
            Vaccination.client_uuid == v.client_uuid
        ).first():
            result.duplicates_skipped += 1
            continue
        try:
            rec = registration.build_vaccination(v, current)
            db.add(rec)
            touched_children.add(v.child_id)
            result.vaccinations_saved += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"vaccination {v.vaccine}: {exc}")

    # 3) Visit status updates
    for visit in payload.visits:
        try:
            child = db.query(Child).filter(Child.id == visit.get("childId")).first()
            if child:
                child.status = visit.get("status", child.status)
                if visit.get("lastSeen"):
                    child.last_seen = datetime.fromisoformat(visit["lastSeen"])
                touched_children.add(child.id)
                result.visits_saved += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"visit {visit}: {exc}")

    db.commit()

    # Rescore affected children
    for cid in touched_children:
        child = db.query(Child).filter(Child.id == cid).first()
        if child:
            scoring.refresh_child_risk(db, child, commit=False)
    db.commit()

    # Update sync bookkeeping
    sr = db.query(SyncRecord).filter(SyncRecord.worker_id == current.id).first()
    if not sr:
        sr = SyncRecord(worker_id=current.id)
        db.add(sr)
    sr.last_sync = result.synced_at
    sr.pending_records = 0
    db.commit()

    total = result.vaccinations_saved + result.children_saved + result.visits_saved
    if total:
        log_activity(
            db, current.id, "sync",
            "Data synced", f"{total} records uploaded to server",
            payload={"counts": result.model_dump()},
        )
    return result


@router.get("/status", response_model=SyncStatus)
def sync_status(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    sr = (
        db.query(SyncRecord)
        .filter(SyncRecord.worker_id == (worker_id or current.id))
        .first()
    )
    if not sr:
        return SyncStatus(last_sync=None, pending_records=0)
    return SyncStatus(last_sync=sr.last_sync, pending_records=sr.pending_records)
