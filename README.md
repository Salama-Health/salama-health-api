# Salama Health API

Climate-aware immunisation prediction backend for the Salama Health mobile app
(South Sudan). FastAPI + PostgreSQL, ML models loaded locally, deployed on AWS.

## Stack

- **API:** FastAPI (Python 3.11), served by Gunicorn + Uvicorn workers
- **DB:** PostgreSQL (self-hosted, local to the AWS instance)
- **ORM / migrations:** SQLAlchemy 2.0 + Alembic
- **Auth:** JWT (worker ID + PIN), bcrypt-hashed PINs
- **ML:** XGBoost + RandomForest (+ optional LSTM) flood ensemble, loaded from
  local `models/`, producing the **CDI** (facility) and **IGS** (child) scores
- **Scheduler:** APScheduler daily score refresh (single-leader across workers)
- **Deploy:** Docker Compose (api + postgres + nginx) on EC2

## Project layout

```
app/
  main.py            FastAPI app + lifespan (loads models, starts scheduler)
  config.py          Env-driven settings (pydantic-settings)
  core/security.py   JWT + PIN hashing + auth dependency
  db/                database.py (pooled engine), models.py (ORM)
  schemas/           Pydantic request/response models (camelCase out)
  routers/           12 domain routers
  services/          scoring engine, scheduler, activity helper
  ml/                model_loader (local-first), features, EPI schedule
models/              Local ML model files (git-ignored; mounted on EC2)
scripts/             seed.py, refresh_scores.py
alembic/             DB migrations
nginx/               Reverse proxy config
tests/               Smoke tests (run on SQLite, no Postgres needed)
Dockerfile · docker-compose.yml · gunicorn_conf.py · Makefile
```

## How scoring works (and why it scales)

The two ML-driven scores change **slowly** (climate data is daily), so they are
**precomputed and cached** in Postgres rather than recomputed per request:

- **CDI (Climate Disruption Index)** per facility
  `CDI = 0.35·P(flood) + 0.30·P(cutoff) + 0.20·P(CCF) + 0.15·P(disp)`
  `P(flood)` comes from the model ensemble.
- **IGS (Immunisation Gap Score)** per child, normalised to 0–1
  `IGS = CDI · VaccinationDebt · (1/Accessibility) · AgeUrgency`

A daily job (or `POST /climate/refresh`) runs inference and writes the current
scores. **All user-facing GET requests are O(1) Postgres reads** — independent of
model throughput — which is what lets the box serve ~100 concurrent users.

### Concurrency / model loading

> "models loaded locally — how do they handle many concurrent requests?"

- Each Gunicorn worker process loads its **own** copy of the models once at
  startup (`ModelManager.load`). No shared mutable state, no cross-process locks.
- Inference is **read-only** and runs in FastAPI's threadpool, so it doesn't
  block the event loop and isn't serialised.
- Because scores are cached, inference happens on a schedule (once/day), not on
  the request path — so concurrent user traffic never contends on the models.
- The scheduler elects **one leader** across workers via a Postgres advisory
  lock, so the daily refresh runs once for the whole fleet.

## Local development

Prereqs: Python 3.11, PostgreSQL running locally (or use Docker just for the DB).

```bash
cp .env.example .env                      # set SECRET_KEY at minimum
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -r requirements.txt

# Option A: full stack in Docker
make up                                   # api + postgres + nginx
docker compose exec api python -m scripts.seed

# Option B: API on host, Postgres in Docker
docker compose up -d db
make seed                                 # creates facilities, workers, demo data
make dev                                  # uvicorn http://localhost:8000
```

Open <http://localhost:8000/docs> for interactive Swagger docs.
Default logins after seeding: **CHW-001 / 1234**, **CHW-002 / 5678**.

## Deploy on AWS EC2

1. **Provision** an instance (Ubuntu, 2–4 vCPU is plenty; e.g. t3.large).
   Open security-group ports 80/443 (and 22 for SSH).
2. **Install Docker + Compose plugin.**
3. **Clone** this repo to `/opt/salama-health-api`.
4. **Configure** `.env` (copy from `.env.example`):
   - `ENVIRONMENT=production`
   - `SECRET_KEY=<python -c "import secrets;print(secrets.token_urlsafe(48))">`
   - `POSTGRES_PASSWORD=<strong password>`
   - `WEB_CONCURRENCY=<~ 2×vCPU + 1>`
   - `CORS_ORIGINS=<your app origin>`
5. **Drop the model files** into `models/` (see `models/README.md`).
6. **Launch:**
   ```bash
   docker compose up -d --build
   docker compose exec api python -m scripts.seed     # first run only
   docker compose exec api alembic stamp head          # adopt migrations
   ```
7. **TLS:** put certs in `nginx/certs/` and enable the HTTPS server block in
   `nginx/nginx.conf` (Let's Encrypt / certbot recommended).

The Flutter app's base URL then points at `https://<your-domain-or-EIP>`.

### Migrations

Schema is auto-created on startup for convenience, but use Alembic in production:

```bash
make revision m="add something"     # autogenerate
make migrate                         # alembic upgrade head
```

### Scheduled refresh

In-process scheduler is on by default (`ENABLE_SCHEDULER=true`, daily 02:00 UTC).
To use external cron instead, set `ENABLE_SCHEDULER=false` and add:

```cron
0 2 * * *  ubuntu  cd /opt/salama-health-api && docker compose exec -T api python -m scripts.refresh_scores
```

## API surface

| Domain | Endpoints |
|---|---|
| Auth | `POST /auth/login` · `POST /auth/refresh` · `POST /auth/logout` · `GET /auth/me` |
| Workers | `GET /workers/{id}` |
| Children | `GET /children` · `GET /children/{id}` · `POST /children` · `PATCH /children/{id}` · `GET /children/lookup?qr=` |
| Vaccinations | `GET /vaccinations?childId=` · `POST /vaccinations` |
| Facilities | `GET /facilities` · `GET /facilities/{id}` · `POST /facilities` |
| Risk Scoring | `GET /risk-scores` · `GET /risk-scores/child/{id}` |
| Climate CDI | `GET /climate/facilities` · `GET /climate/facilities/{id}` · `POST /climate/refresh` |
| Activity | `GET /activity` |
| Sync | `POST /sync/upload` · `GET /sync/status` |
| Reports | `GET /reports/summary` · `GET /reports/doses-weekly` · `GET /reports/coverage-by-vaccine` · `POST /reports/export` |
| Routes | `GET /routes/optimized` |
| Notifications | `POST /devices/register` · `GET /devices/alerts` |

All responses are camelCase to match the Flutter models. All endpoints except
`/auth/login`, `/auth/refresh`, `/`, `/health` require a `Bearer` access token.

## Tests

```bash
make test        # runs against SQLite, no Postgres required
```
