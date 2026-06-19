from typing import List, Optional

from app.schemas.common import CamelModel


class RouteStop(CamelModel):
    child_id: str
    child_name: str
    risk_band: str
    distance_km: float
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    current_location: Optional[str] = None
    order: int


class OptimizedRoute(CamelModel):
    stops: List[RouteStop]
    total_km: float
    est_minutes: int
