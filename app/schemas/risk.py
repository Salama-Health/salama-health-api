from datetime import datetime
from typing import Optional

from app.schemas.common import CamelModel


class RiskComponents(CamelModel):
    cdi: float
    vaccination_debt: float
    accessibility: float
    age_urgency: float


class RiskScoreOut(CamelModel):
    child_id: str
    child_name: str
    risk_score: float                  # 0.0 .. 1.0
    risk_label: str                    # High | Medium | Watch | Low
    overdue_count: int = 0
    facility_id: Optional[str] = None
    days_to_window: Optional[int] = None
    components: RiskComponents
    scored_at: Optional[datetime] = None
