"""
Background scheduler for daily score refresh.

IMPORTANT for multi-worker deployments: with several Gunicorn workers we must
NOT run the scheduler in every process (it would refresh N times). We elect a
single worker using a PostgreSQL advisory lock; only the lock holder schedules
the job. This keeps inference load to one run per day across the whole fleet.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal, engine
from app.services.scoring import refresh_all_scores

logger = logging.getLogger(__name__)

# Arbitrary constant identifying this app's scheduler lock.
_SCHEDULER_ADVISORY_LOCK = 911_220_417

_scheduler: BackgroundScheduler | None = None
_lock_conn = None


def _run_refresh() -> None:
    db = SessionLocal()
    try:
        refresh_all_scores(db)
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled score refresh failed")
    finally:
        db.close()


def _try_acquire_leader_lock() -> bool:
    """Hold a session-level advisory lock for the life of the process."""
    global _lock_conn
    try:
        _lock_conn = engine.connect()
        got = _lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _SCHEDULER_ADVISORY_LOCK}
        ).scalar()
        if not got:
            _lock_conn.close()
            _lock_conn = None
        return bool(got)
    except Exception:  # noqa: BLE001
        logger.warning("Could not acquire scheduler leader lock; assuming non-leader")
        if _lock_conn is not None:
            _lock_conn.close()
            _lock_conn = None
        return False


def start_scheduler() -> None:
    global _scheduler
    if not settings.enable_scheduler:
        logger.info("Scheduler disabled via config")
        return

    if not _try_acquire_leader_lock():
        logger.info("Another worker is the scheduler leader; skipping in this process")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _run_refresh,
        CronTrigger(
            hour=settings.cdi_refresh_cron_hour,
            minute=settings.cdi_refresh_cron_minute,
        ),
        id="daily_score_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started (leader). Daily refresh at %02d:%02d UTC",
        settings.cdi_refresh_cron_hour, settings.cdi_refresh_cron_minute,
    )


def shutdown_scheduler() -> None:
    global _scheduler, _lock_conn
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    if _lock_conn is not None:
        try:
            _lock_conn.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEDULER_ADVISORY_LOCK}
            )
        except Exception:  # noqa: BLE001
            pass
        _lock_conn.close()
        _lock_conn = None
