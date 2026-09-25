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
    # Date of the most recent Sentinel-1 reading behind this score. None means
    # the facility has no radar history and p_flood came from seasonal
    # estimates - a materially weaker score, since VV backscatter carries most
    # of the flood model's weight. Clients should surface the difference.
    sar_observed_at: Optional[datetime] = None
