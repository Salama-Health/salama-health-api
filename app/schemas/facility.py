from typing import Optional

from app.schemas.common import CamelModel


class FacilityOut(CamelModel):
    id: str
    name: str
    county: Optional[str] = None
    state: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    children: int = 0                  # total children count
    assigned: bool = False             # assigned to the requesting worker

    # Roll-ups for the home screen cards
    high_priority: int = 0
    due_soon: int = 0
    recently_visited: int = 0

    # Latest cached CDI summary (nullable until first refresh)
    cdi_score: Optional[float] = None
    risk: Optional[str] = None         # ok | warning | danger
    hazard: Optional[str] = None
    days_to_window: Optional[int] = None
    hazard_detail: Optional[str] = None
    hazard_timeframe: Optional[str] = None


class FacilityCreate(CamelModel):
    name: str
    county: Optional[str] = None
    state: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    elevation_m: Optional[float] = None
