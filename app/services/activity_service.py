"""Helper to append entries to a worker's activity feed."""
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import ActivityLog


def log_activity(
    db: Session,
    worker_id: Optional[str],
    type_: str,
    title: str,
    subtitle: str = "",
    payload: Optional[dict] = None,
    commit: bool = True,
) -> ActivityLog:
    entry = ActivityLog(
        worker_id=worker_id,
        type=type_,
        title=title,
        subtitle=subtitle,
        payload=payload,
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
