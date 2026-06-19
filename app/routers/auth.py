"""Authentication: login with worker_id + PIN, refresh, me."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_worker,
    verify_pin,
)
from app.db.database import get_db
from app.db.models import Worker
from app.schemas.auth import AccessToken, LoginRequest, RefreshRequest, TokenPair
from app.schemas.worker import WorkerOut

router = APIRouter()


def _worker_out(w: Worker) -> WorkerOut:
    return WorkerOut(
        id=w.id,
        worker_id=w.worker_id,
        name=w.name,
        role=w.role,
        facility=w.facility.name if w.facility else None,
        facility_id=w.facility_id,
        county=w.county,
        phone=w.phone,
        facilities_count=1 if w.facility_id else 0,
        active=w.active,
    )


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    worker = db.query(Worker).filter(Worker.worker_id == payload.worker_id).first()
    if not worker or not verify_pin(payload.pin, worker.pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker ID or PIN",
        )
    if not worker.active:
        raise HTTPException(status_code=403, detail="Worker account is inactive")

    return TokenPair(
        access_token=create_access_token(worker.id),
        refresh_token=create_refresh_token(worker.id),
        worker=_worker_out(worker),
    )


@router.post("/refresh", response_model=AccessToken)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    data = decode_token(payload.refresh_token, expected_type="refresh")
    worker = db.query(Worker).filter(Worker.id == data.get("sub")).first()
    if not worker or not worker.active:
        raise HTTPException(status_code=401, detail="Worker not found or inactive")
    return AccessToken(access_token=create_access_token(worker.id))


@router.post("/logout", status_code=204)
def logout(current: Worker = Depends(get_current_worker)):
    # Stateless JWT — client discards tokens. Endpoint exists for symmetry and
    # could be extended with a token denylist if needed.
    return None


@router.get("/me", response_model=WorkerOut)
def me(current: Worker = Depends(get_current_worker)):
    return _worker_out(current)
