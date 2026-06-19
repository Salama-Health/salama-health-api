"""
Gunicorn configuration for production (AWS EC2).

Scaling model
-------------
* `workers` Uvicorn worker processes share the listening socket; the kernel
  load-balances connections across them. Each worker loads its own copy of the
  ML models and its own DB connection pool.
* Sizing: workers = (2 * CPU) + 1 is the standard starting point. Override with
  the WEB_CONCURRENCY env var to match the instance size. For ~100 concurrent
  users a 2–4 vCPU instance with 4–9 workers is comfortable, because user
  requests hit cached scores in Postgres (no per-request inference).
* Keep total DB connections under Postgres max_connections:
      workers * (DB_POOL_SIZE + DB_MAX_OVERFLOW) < max_connections
"""
import multiprocessing
import os

bind = os.getenv("BIND", "0.0.0.0:8000")

workers = int(os.getenv("WEB_CONCURRENCY", (multiprocessing.cpu_count() * 2) + 1))
worker_class = "uvicorn.workers.UvicornWorker"

# Recycle workers periodically to bound memory growth.
max_requests = 2000
max_requests_jitter = 200

timeout = 60
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")

# Preloading would share model memory via copy-on-write BUT breaks the
# per-worker scheduler leader-election and APScheduler. We keep preload off so
# each worker initialises cleanly in its lifespan.
preload_app = False
