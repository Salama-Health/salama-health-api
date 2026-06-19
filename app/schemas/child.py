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
