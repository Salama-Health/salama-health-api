from datetime import datetime
from typing import List, Optional

from app.schemas.common import CamelModel
from app.schemas.child import ChildCreate
from app.schemas.vaccination import VaccinationCreate


class SyncUploadRequest(CamelModel):
    """Batch of actions recorded offline on the device."""
    vaccinations: List[VaccinationCreate] = []
    new_children: List[ChildCreate] = []
    visits: List[dict] = []            # [{childId, status, lastSeen}]


class SyncUploadResult(CamelModel):
    vaccinations_saved: int = 0
    children_saved: int = 0
    visits_saved: int = 0
    duplicates_skipped: int = 0
    errors: List[str] = []
    synced_at: datetime


class SyncStatus(CamelModel):
    last_sync: Optional[datetime] = None
    pending_records: int = 0
