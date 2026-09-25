from datetime import datetime
from typing import List, Optional

from app.schemas.common import CamelModel
from app.schemas.vaccination import VaccinationOut


class ChildOut(CamelModel):
    id: str
    name: str
    gender: Optional[str] = None
    born_date: Optional[datetime] = None
    facility_id: Optional[str] = None
    worker_id: Optional[str] = None
    parent_name: Optional[str] = None
    parent_phone: Optional[str] = None
    current_location: Optional[str] = None
    distance_km: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    qr_code: Optional[str] = None
    last_seen: Optional[datetime] = None
    status: str = "toVisit"

    # Enriched fields (from cached risk score + due-vaccine computation)
    risk_score: Optional[float] = None
    risk_band: Optional[str] = None
    # True when no score has been computed for this child yet. The client must
    # use this rather than inferring it from risk_score == 0: the IGS is
    # multiplicative, so a child with no vaccination debt scores EXACTLY 0.0,
    # which is a real score meaning "fully up to date", not a missing one.
    # 11% of seeded children are in that state.
    risk_pending: bool = True
    due_vaccines: List[str] = []


class ChildDetail(ChildOut):
    history: List[VaccinationOut] = []


class ChildCreate(CamelModel):
    name: str
    gender: Optional[str] = None
    born_date: Optional[datetime] = None
    facility_id: Optional[str] = None
    worker_id: Optional[str] = None
    parent_name: Optional[str] = None
    parent_phone: Optional[str] = None
    current_location: Optional[str] = None
    distance_km: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    qr_code: Optional[str] = None
    client_uuid: Optional[str] = None


class ChildUpdate(CamelModel):
    name: Optional[str] = None
    parent_name: Optional[str] = None
    parent_phone: Optional[str] = None
    current_location: Optional[str] = None
    distance_km: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = None
    last_seen: Optional[datetime] = None
