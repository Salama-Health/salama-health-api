from datetime import datetime
from typing import Optional

from app.schemas.common import CamelModel


class VaccinationOut(CamelModel):
    id: str
    child_id: str
    vaccine: str
    dose: Optional[str] = None
    date: Optional[datetime] = None      # maps from date_given
    status: str
    batch: Optional[str] = None          # maps from batch_number
    site: Optional[str] = None           # injection site, when recorded
    # Only populated when listing a worker's doses across children, where the
    # client has no other way to name the child. Null on a per-child history,
    # which is already scoped to one child.
    child_name: Optional[str] = None
    # Who gave the dose. A worker reviewing a child's card needs to know
    # whether a dose was theirs or a colleague's before deciding what to give.
    administered_by: Optional[str] = None

    @classmethod
    def from_orm_record(
        cls,
        v,
        child_name: Optional[str] = None,
        administered_by: Optional[str] = None,
    ) -> "VaccinationOut":
        return cls(
            id=v.id,
            child_id=v.child_id,
            vaccine=v.vaccine,
            dose=v.dose,
            date=v.date_given,
            status=v.status,
            batch=v.batch_number,
            site=v.site,
            child_name=child_name,
            administered_by=administered_by,
        )


class VaccinationCreate(CamelModel):
    child_id: str
    vaccine: str
    dose: Optional[str] = None
    date_given: Optional[datetime] = None
    status: str = "given"
    batch_number: Optional[str] = None
    site: Optional[str] = None               # injection site, e.g. "Left arm"
    notes: Optional[str] = None
    # Client-generated idempotency key so offline re-uploads de-duplicate.
    client_uuid: Optional[str] = None
