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

    # ── Registration record ──────────────────────────────────────────────
    # consent_at is the consent record: when the caregiver gave consent, as
    # captured on the device. Stored even when consent_given is false, so a
    # refusal is auditable too.
    consent_given: Optional[bool] = None
    consent_at: Optional[datetime] = None
    # True when born_date is an estimated age bracket rather than a date from
    # a card. Everything downstream treats born_date as exact, so this is the
    # only signal that it is not.
    born_date_estimated: Optional[bool] = None
    notes: Optional[str] = None
    registered_by: Optional[str] = None      # worker code as typed, e.g. CHW-001
    registered_at: Optional[datetime] = None

    # Accepted so it is not rejected, but deliberately NOT stored: the server
    # computes due vaccines from the EPI schedule and the child's age, and
    # persisting the client's view would create a second source of truth that
    # silently goes stale as the child ages.
    due_vaccines: Optional[List[str]] = None


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
