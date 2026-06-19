from app.schemas.common import CamelModel
from app.schemas.worker import WorkerOut


class LoginRequest(CamelModel):
    worker_id: str          # e.g. CHW-001
    pin: str


class TokenPair(CamelModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    worker: WorkerOut


class RefreshRequest(CamelModel):
    refresh_token: str


class AccessToken(CamelModel):
    access_token: str
    token_type: str = "bearer"
