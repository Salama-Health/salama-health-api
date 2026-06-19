"""
Run the full weekly CDI pipeline once, then exit (CHIRPS + Open-Meteo +
recompute all CDI/risk scores).

Use this from an external cron if you prefer not to run the in-process
scheduler (set ENABLE_SCHEDULER=false):

    # /etc/cron.d/salama   (weekly, Sunday 00:00)
    0 0 * * 0  ubuntu  cd /opt/salama-health-api && \
        docker compose exec -T api python -m scripts.refresh_scores

Usage (local):  python -m scripts.refresh_scores
"""
from app.db.database import SessionLocal
from app.ml.model_loader import model_manager
from app.services.pipeline import run_weekly_cdi_pipeline


def run():
    model_manager.load()
    db = SessionLocal()
    try:
        summary = run_weekly_cdi_pipeline(db)
        print(summary)
    finally:
        db.close()


if __name__ == "__main__":
    run()
