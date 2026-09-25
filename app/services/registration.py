"""
Building records from client payloads.

`POST /children` and the `newChildren[]` array on `/sync/upload` carry the
IDENTICAL object - the app serialises one model for both - and the same is true
of vaccinations. They were previously constructed in two places, field by field,
which is how the consent fields came to be dropped: adding them to one path
would have silently left the other behind.

One builder per record type, used by both routes.
"""
from datetime import datetime, timezone
from typing import Optional

from app.db.models import Child, Vaccination
from app.schemas.child import ChildCreate
from app.schemas.vaccination import VaccinationCreate


def _utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise to naive UTC to match the DateTime columns.

    The app sends ISO-8601 with a Z suffix, so pydantic hands us an aware
    datetime, while the columns are `timestamp without time zone`. Storing an
    aware value against a naive column silently shifts it. Converting here
    keeps every stored timestamp unambiguously UTC - which matters most for
    consent_at, where the stored time is the audit record.
    """
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def build_child(payload: ChildCreate, worker) -> Child:
    """A Child from a registration payload, from either entry point.

    `worker` is the authenticated worker, used only as a fallback for facility
    and worker when the payload does not name them.

    `payload.due_vaccines` is intentionally not persisted - the server derives
    due vaccines from the EPI schedule and the child's age, and storing the
    client's snapshot would create a second source of truth that goes stale as
    the child ages.
    """
    return Child(
        name=payload.name,
        gender=payload.gender,
        born_date=_utc_naive(payload.born_date),
        born_date_estimated=bool(payload.born_date_estimated),
        facility_id=payload.facility_id or worker.facility_id,
        worker_id=payload.worker_id or worker.id,
        parent_name=payload.parent_name,
        parent_phone=payload.parent_phone,
        current_location=payload.current_location,
        distance_km=payload.distance_km,
        latitude=payload.latitude,
        longitude=payload.longitude,
        qr_code=payload.qr_code or payload.client_uuid,
        notes=payload.notes,
        consent_given=payload.consent_given,
        consent_at=_utc_naive(payload.consent_at),
        # Fall back to the authenticated worker's code so the audit trail is
        # never empty, even from an older client that does not send it.
        registered_by=payload.registered_by or worker.worker_id,
        registered_at=_utc_naive(payload.registered_at),
    )


def build_vaccination(
    payload: VaccinationCreate, worker, child_id: Optional[str] = None
) -> Vaccination:
    """A Vaccination from a dose payload, from either entry point."""
    return Vaccination(
        child_id=child_id or payload.child_id,
        vaccine=payload.vaccine,
        dose=payload.dose,
        date_given=_utc_naive(payload.date_given),
        status=payload.status,
        batch_number=payload.batch_number,
        site=payload.site,
        notes=payload.notes,
        administered_by=worker.id,
        client_uuid=payload.client_uuid,
        synced=True,
    )
