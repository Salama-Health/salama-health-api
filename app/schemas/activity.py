from datetime import datetime
from typing import Optional

from app.schemas.common import CamelModel


class ActivityOut(CamelModel):
    id: str
    type: str          # vaccination | visit | sync | alert | registration
    title: str
    subtitle: Optional[str] = None
    created_at: datetime
