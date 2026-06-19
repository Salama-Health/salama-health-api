"""
Authentication & security helpers.

- PINs are hashed with bcrypt (never stored in clear).
- Stateless JWT access/refresh tokens.
- `get_current_worker` is the FastAPI dependency that protects routes.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Worker

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# tokenUrl is informational (used by /docs). Auth is actually via /auth/login.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=True)


# ── PIN hashing ──────────────────────────────────────────────────────────────
def hash_pin(pin: str) -> str:
    return pwd_context.hash(pin)


def verify_pin(pin: str, pin_hash: str) -> bool:
    return pwd_context.verify(pin, pin_hash)


# ── JWT ──────────────────────────────────────────────────────────────────────
def _create_token(subject: str, expires_minutes: int, token_type: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(worker_pk: str) -> str:
    return _create_token(worker_pk, settings.access_token_expire_minutes, "access")


def create_refresh_token(worker_pk: str) -> str:
    return _create_token(worker_pk, settings.refresh_token_expire_minutes, "refresh")


def decode_token(token: str, expected_type: Optional[str] = None) -> dict:
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if expected_type and payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Expected a {expected_type} token",
        )
    return payload


# ── Dependency ───────────────────────────────────────────────────────────────
def get_current_worker(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Worker:
    payload = decode_token(token, expected_type="access")
    worker_pk = payload.get("sub")
    if not worker_pk:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    worker = db.query(Worker).filter(Worker.id == worker_pk).first()
    if not worker or not worker.active:
        raise HTTPException(status_code=401, detail="Worker not found or inactive")
    return worker
