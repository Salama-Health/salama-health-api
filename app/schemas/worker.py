from typing import Optional

from app.schemas.common import CamelModel


class WorkerOut(CamelModel):
    id: str
    worker_id: str
    name: str
    role: str
    facility: Optional[str] = None       # facility name (resolved)
    facility_id: Optional[str] = None
    county: Optional[str] = None
    phone: Optional[str] = None
    facilities_count: int = 0
    active: bool = True


class WorkerCreate(CamelModel):
    worker_id: str
    name: str
    pin: str
    role: str = "CHW"
    facility_id: Optional[str] = None
    county: Optional[str] = None
    phone: Optional[str] = None
