"""Activity feed for the home screen."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import ActivityLog, Worker
from app.schemas.activity import ActivityOut

router = APIRouter()


@router.get("", response_model=list[ActivityOut])
def get_activity(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    rows = (
        db.query(ActivityLog)
        .filter(ActivityLog.worker_id == (worker_id or current.id))
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return rows
