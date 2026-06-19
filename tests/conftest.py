"""
Test fixtures.

Tests run against a throwaway SQLite database so they need no Postgres. We set
the environment BEFORE importing the app so settings pick it up.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite+pysqlite:///{tempfile.gettempdir()}/salama_test.db")
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["REQUIRE_MODELS"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client):
    """Create a worker directly, then log in to get a token."""
    from app.core.security import hash_pin
    from app.db.database import SessionLocal
    from app.db.models import Facility, Worker

    db = SessionLocal()
    try:
        if not db.query(Worker).filter(Worker.worker_id == "CHW-T01").first():
            fac = Facility(name="Test PHCC", county="Rubkona", state="Unity", elevation_m=390.0)
            db.add(fac)
            db.flush()
            db.add(Worker(
                worker_id="CHW-T01", name="Test CHW", pin_hash=hash_pin("0000"),
                facility_id=fac.id, role="CHW", active=True,
            ))
            db.commit()
    finally:
        db.close()

    resp = client.post("/auth/login", json={"workerId": "CHW-T01", "pin": "0000"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}
