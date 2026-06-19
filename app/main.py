"""
Salama Health API — application entrypoint.

Run (dev):   uvicorn app.main:app --reload
Run (prod):  gunicorn app.main:app -c gunicorn_conf.py
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.db.database import Base, engine
from app.logging_config import configure_logging
from app.ml.model_loader import model_manager
from app.routers import (
    activity,
    auth,
    children,
    climate,
    facilities,
    notifications,
    reports,
    risk_scores,
    routes,
    sync,
    vaccinations,
    workers,
)
from app.schemas.common import HealthStatus
from app.services.scheduler import shutdown_scheduler, start_scheduler

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once per worker process at startup.
    logger.info("Starting %s v%s (%s)", settings.app_name, __version__, settings.environment)

    # Create tables if they don't exist (Alembic owns migrations in prod, but
    # this keeps dev / fresh deploys turnkey).
    Base.metadata.create_all(bind=engine)

    # Load ML models into this process's memory (once).
    model_manager.load()
    logger.info("Model status: %s", model_manager.status())

    # Elect a single scheduler leader across workers (advisory lock).
    start_scheduler()

    yield

    shutdown_scheduler()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    description="Climate-aware immunisation prediction backend for South Sudan.",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── Routers ───────────────────────────────────────────────────────────────────
p = settings.api_v1_prefix
app.include_router(auth.router,          prefix=f"{p}/auth",         tags=["Authentication"])
app.include_router(workers.router,       prefix=f"{p}/workers",      tags=["Workers"])
app.include_router(children.router,      prefix=f"{p}/children",     tags=["Children"])
app.include_router(vaccinations.router,  prefix=f"{p}/vaccinations", tags=["Vaccinations"])
app.include_router(facilities.router,    prefix=f"{p}/facilities",   tags=["Facilities"])
app.include_router(risk_scores.router,   prefix=f"{p}/risk-scores",  tags=["Risk Scoring"])
app.include_router(climate.router,       prefix=f"{p}/climate",      tags=["Climate CDI"])
app.include_router(activity.router,      prefix=f"{p}/activity",     tags=["Activity Feed"])
app.include_router(sync.router,          prefix=f"{p}/sync",         tags=["Sync"])
app.include_router(reports.router,       prefix=f"{p}/reports",      tags=["Reports"])
app.include_router(routes.router,        prefix=f"{p}/routes",       tags=["Route Planning"])
app.include_router(notifications.router, prefix=f"{p}/devices",      tags=["Notifications"])


@app.get("/", tags=["Health"])
def root():
    return {"message": f"{settings.app_name} is running", "version": __version__}


@app.get("/health", response_model=HealthStatus, tags=["Health"])
def health():
    return HealthStatus(status="ok", models=model_manager.status())
