"""
SQLAlchemy ORM models.

Schema mirrors the Flutter app's data models (worker, child, vaccination,
facility) plus the prediction tables (CDIScore, RiskScore) and operational
tables (activity log, sync, devices).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


class Worker(Base):
    __tablename__ = "workers"

    id          = Column(String, primary_key=True, default=new_id)
    worker_id   = Column(String, unique=True, nullable=False, index=True)  # CHW-001
    name        = Column(String, nullable=False)
    role        = Column(String, default="CHW")            # CHW | supervisor
    pin_hash    = Column(String, nullable=False)
    facility_id = Column(String, ForeignKey("facilities.id"))
    county      = Column(String)
    phone       = Column(String)
    active      = Column(Boolean, default=True)
    created_at  = Column(DateTime, server_default=func.now())

    facility    = relationship("Facility", back_populates="workers")
    children    = relationship("Child", back_populates="worker")
    devices     = relationship("Device", back_populates="worker", cascade="all, delete-orphan")


class Facility(Base):
    __tablename__ = "facilities"

    id          = Column(String, primary_key=True, default=new_id)
    name        = Column(String, nullable=False, index=True)
    county      = Column(String)
    state       = Column(String, index=True)
    latitude    = Column(Float)
    longitude   = Column(Float)
    elevation_m = Column(Float)
    active      = Column(Boolean, default=True)
    created_at  = Column(DateTime, server_default=func.now())

    workers     = relationship("Worker", back_populates="facility")
    children    = relationship("Child", back_populates="facility")
    cdi_scores  = relationship("CDIScore", back_populates="facility", cascade="all, delete-orphan")


class Child(Base):
    __tablename__ = "children"

    id               = Column(String, primary_key=True, default=new_id)
    name             = Column(String, nullable=False)
    gender           = Column(String)                     # M | F
    born_date        = Column(DateTime)
    facility_id      = Column(String, ForeignKey("facilities.id"), index=True)
    worker_id        = Column(String, ForeignKey("workers.id"), index=True)
    parent_name      = Column(String)
    parent_phone     = Column(String)
    current_location = Column(String)
    distance_km      = Column(Float)
    latitude         = Column(Float)
    longitude        = Column(Float)
    qr_code          = Column(String, unique=True, index=True)
    last_seen        = Column(DateTime)
    status           = Column(String, default="toVisit")  # toVisit | visited | missed
    created_at       = Column(DateTime, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())

    facility         = relationship("Facility", back_populates="children")
    worker           = relationship("Worker", back_populates="children")
    vaccinations     = relationship("Vaccination", back_populates="child", cascade="all, delete-orphan")
    risk_scores      = relationship("RiskScore", back_populates="child", cascade="all, delete-orphan")


class Vaccination(Base):
    __tablename__ = "vaccinations"

    id              = Column(String, primary_key=True, default=new_id)
    child_id        = Column(String, ForeignKey("children.id"), nullable=False, index=True)
    vaccine         = Column(String, nullable=False)      # BCG | Penta-1 | ...
    dose            = Column(String)
    date_given      = Column(DateTime)
    status          = Column(String, default="given")     # given | due | missed
    batch_number    = Column(String)
    administered_by = Column(String, ForeignKey("workers.id"))
    notes           = Column(Text)
    # idempotency key from the client so re-uploaded offline records de-dupe
    client_uuid     = Column(String, unique=True, index=True)
    synced          = Column(Boolean, default=True)
    created_at      = Column(DateTime, server_default=func.now())

    child           = relationship("Child", back_populates="vaccinations")


class CDIScore(Base):
    """Cached Climate Disruption Index per facility (recomputed daily)."""
    __tablename__ = "cdi_scores"

    id               = Column(String, primary_key=True, default=new_id)
    facility_id      = Column(String, ForeignKey("facilities.id"), nullable=False, index=True)
    cdi_score        = Column(Float)
    p_flood          = Column(Float)
    p_cutoff         = Column(Float)
    p_ccf            = Column(Float)
    p_disp           = Column(Float)
    risk_level       = Column(String)                     # ok | warning | danger
    hazard           = Column(String)
    days_to_window   = Column(Integer)
    hazard_detail    = Column(Text)
    hazard_timeframe = Column(String)
    is_current       = Column(Boolean, default=True, index=True)  # latest row per facility
    scored_at        = Column(DateTime, server_default=func.now())

    facility         = relationship("Facility", back_populates="cdi_scores")

    __table_args__ = (
        Index("ix_cdi_facility_current", "facility_id", "is_current"),
    )


class RiskScore(Base):
    """Cached child-level IGS risk score (recomputed daily / on writes)."""
    __tablename__ = "risk_scores"

    id               = Column(String, primary_key=True, default=new_id)
    child_id         = Column(String, ForeignKey("children.id"), nullable=False, index=True)
    risk_score       = Column(Float)                      # 0.0 .. 1.0
    risk_label       = Column(String)                     # High | Medium | Watch | Low
    cdi              = Column(Float)
    vaccination_debt = Column(Float)
    accessibility    = Column(Float)
    age_urgency      = Column(Float)
    overdue_count    = Column(Integer)
    is_current       = Column(Boolean, default=True, index=True)
    scored_at        = Column(DateTime, server_default=func.now())

    child            = relationship("Child", back_populates="risk_scores")

    __table_args__ = (
        Index("ix_risk_child_current", "child_id", "is_current"),
    )


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id         = Column(String, primary_key=True, default=new_id)
    worker_id  = Column(String, ForeignKey("workers.id"), index=True)
    type       = Column(String)   # vaccination | visit | sync | alert | registration
    title      = Column(String)
    subtitle   = Column(String)
    payload    = Column(JSON)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class SyncRecord(Base):
    __tablename__ = "sync_records"

    id              = Column(String, primary_key=True, default=new_id)
    worker_id       = Column(String, ForeignKey("workers.id"), unique=True, index=True)
    last_sync       = Column(DateTime, server_default=func.now())
    pending_records = Column(Integer, default=0)


class Device(Base):
    """Push-notification device registration (FCM / APNs)."""
    __tablename__ = "devices"

    id          = Column(String, primary_key=True, default=new_id)
    worker_id   = Column(String, ForeignKey("workers.id"), nullable=False, index=True)
    fcm_token   = Column(String, nullable=False, unique=True)
    platform    = Column(String)   # android | ios
    created_at  = Column(DateTime, server_default=func.now())

    worker      = relationship("Worker", back_populates="devices")
