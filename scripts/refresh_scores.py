"""
Recompute all CDI + child risk scores once, then exit.

Use this from an external cron if you prefer not to run the in-process
scheduler (set ENABLE_SCHEDULER=false):

    # /etc/cron.d/salama   (daily 02:00)
    0 2 * * *  ubuntu  cd /opt/salama-health-api && \
        docker compose exec -T api python -m scripts.refresh_scores

Usage (local):  python -m scripts.refresh_scores
"""
from app.db.database import SessionLocal
from app.ml.model_loader import model_manager
from app.services.scoring import refresh_all_scores


def run():
    model_manager.load()
    db = SessionLocal()
    try:
        result = refresh_all_scores(db)
        print(f"Refreshed {result['facilities']} facilities, {result['children']} children")
    finally:
        db.close()


if __name__ == "__main__":
    run()
