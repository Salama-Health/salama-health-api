"""
Route planning — server-side optimised visit order.

Sorts a worker's pending visits by risk band (highest first) then by distance,
mirroring the app's local heuristic but using cached risk scores. Real
turn-by-turn routing should call a maps provider (OSRM/Google) on the device
with the coordinates returned here.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import Child, Worker
from app.schemas.route import OptimizedRoute, RouteStop
from app.services import scoring

# Lower index = higher priority
_BAND_ORDER = {"High": 0, "Medium": 1, "Watch": 2, "Low": 3}


router = APIRouter()


@router.get("/optimized", response_model=OptimizedRoute)
def optimized_route(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    children = (
        db.query(Child)
        .filter(
            Child.worker_id == (worker_id or current.id),
            Child.status == "toVisit",
        )
        .all()
    )

    enriched = []
    for c in children:
        risk = scoring.get_current_risk(db, c.id)
        band = risk.risk_label if risk else "Low"
        enriched.append((c, band, c.distance_km or 99.0))

    enriched.sort(key=lambda t: (_BAND_ORDER.get(t[1], 3), t[2]))

    stops = []
    total_km = 0.0
    for order, (c, band, dist) in enumerate(enriched, start=1):
        total_km += dist if dist < 99.0 else 0.0
        stops.append(RouteStop(
            child_id=c.id, child_name=c.name, risk_band=band,
            distance_km=c.distance_km or 0.0,
            latitude=c.latitude, longitude=c.longitude,
            current_location=c.current_location, order=order,
        ))

    # Heuristic time estimate: travel + per-stop service time
    est_minutes = round(total_km * 11 + len(stops) * 14)
    return OptimizedRoute(
        stops=stops, total_km=round(total_km, 1), est_minutes=est_minutes
    )
