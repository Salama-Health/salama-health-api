from datetime import datetime
from typing import Optional

from app.schemas.common import CamelModel


class DeviceRegister(CamelModel):
    fcm_token: str
    platform: Optional[str] = None     # android | ios


class AlertOut(CamelModel):
    id: str
    type: str                          # hazard | overdue
    title: str
    body: str
    facility_id: Optional[str] = None
    child_id: Optional[str] = None
    severity: str = "info"             # info | warning | danger
    created_at: datetime
