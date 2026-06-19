"""Worker profile endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import Child, Worker
from app.routers.auth import _worker_out
from app.schemas.worker import WorkerOut

router = APIRouter()


@router.get("/{worker_id}", response_model=WorkerOut)
def get_worker(
    worker_id: str,
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    # Accept either the public worker_id (CHW-001) or the internal id.
    worker = (
        db.query(Worker)
        .filter((Worker.id == worker_id) | (Worker.worker_id == worker_id))
        .first()
    )
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    out = _worker_out(worker)
    out.facilities_count = (
        db.query(Child.facility_id)
        .filter(Child.worker_id == worker.id)
        .distinct()
        .count()
    )
    return out
