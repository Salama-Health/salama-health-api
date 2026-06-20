"""
Database engine, session factory and FastAPI dependency.

Uses SQLAlchemy 2.0 with a connection pool sized for many concurrent workers.
Each Gunicorn worker process gets its own pool, so keep
    total_workers * (db_pool_size + db_max_overflow) < postgres max_connections.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# Arbitrary constant identifying the schema-creation advisory lock.
_SCHEMA_LOCK_KEY = 911_220_418


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=settings.db_pool_pre_ping,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


def init_db() -> None:
    """
    Create tables if missing. On PostgreSQL this is guarded by a transaction
    level advisory lock so concurrent Gunicorn workers don't race each other on
    first boot (which previously caused a duplicate table error and a restart).
    """
    from app.db import models  # noqa: F401  (register metadata)

    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                         {"k": _SCHEMA_LOCK_KEY})
            Base.metadata.create_all(bind=conn)
    else:
        Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency that yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
