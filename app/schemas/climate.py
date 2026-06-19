from datetime import datetime
from typing import Optional

from app.schemas.common import CamelModel


class CDIComponents(CamelModel):
    p_flood: float
    p_cutoff: float
    p_ccf: float
    p_disp: float


class SarUploadResult(CamelModel):
    matched: int
    skipped: list[str] = []


class CDIOut(CamelModel):
    facility_id: str
    facility_name: str
    county: Optional[str] = None
    state: Optional[str] = None
    cdi_score: float
    risk: str                          # Low | Medium | High | Critical
    hazard: str
    days_to_window: int
    hazard_detail: str
    hazard_timeframe: str
    components: CDIComponents
    scored_at: Optional[datetime] = None
