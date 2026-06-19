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

    @classmethod
    def from_orm_record(cls, v) -> "VaccinationOut":
        return cls(
            id=v.id,
            child_id=v.child_id,
            vaccine=v.vaccine,
            dose=v.dose,
            date=v.date_given,
            status=v.status,
            batch=v.batch_number,
        )


class VaccinationCreate(CamelModel):
    child_id: str
    vaccine: str
    dose: Optional[str] = None
    date_given: Optional[datetime] = None
    status: str = "given"
    batch_number: Optional[str] = None
    notes: Optional[str] = None
    # Client-generated idempotency key so offline re-uploads de-duplicate.
    client_uuid: Optional[str] = None
