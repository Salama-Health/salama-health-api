"""
Import the full seed from scripts/seed_data/ (93 facilities, 1,230 children,
13,975 doses) and compute the first round of scores.

Usage:
    python -m scripts.import_seed

Idempotent - safe to re-run. Every table is keyed on a natural identifier
(county name, facility name, worker_id, child qr_code, vaccination
client_uuid), so existing rows are skipped rather than duplicated.

This is the successor to scripts/seed.py, which carried five hand-written
demo facilities. Use that one only for a throwaway local database.

Radar history
-------------
vv_history.csv holds the last 8 real Sentinel-1 readings, with their true
observation dates, for the 62 facilities still being observed in 2024. The
other 31 stopped in 2021, so seeding them would rank five-year-old
conditions against recent ones; they are left to the -12.0 seasonal fallback
in app/ml/features.py instead.

That distinction is not cosmetic: VV backscatter carries ~79% of the flood
model's weight, so a facility with real history and one on the fallback are
not comparable. The API exposes the latest observation date per facility
(CDIOut.sar_observed_at) so a client can show which is which, and a null
there means "no radar, scored from seasonal estimates".
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

from app.core.security import hash_pin
from app.db.database import Base, SessionLocal, engine
from app.db.models import (
    Child,
    County,
    Facility,
    Vaccination,
    VVHistory,
    Worker,
)
from app.ml.epi import EPI_SCHEDULE
from app.services.scoring import refresh_all_scores

DATA = Path(__file__).resolve().parent / "seed_data"


# ── helpers ───────────────────────────────────────────────────────────────────
def rows(name: str) -> list[dict]:
    path = DATA / name
    if not path.exists():
        sys.exit(f"ERROR: {path} not found. Did the deploy sync scripts/seed_data/?")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def num(v, default=None):
    """Blank cells are common in the CSVs; treat them as missing, not zero."""
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def date(v):
    if not v or not str(v).strip():
        return None
    return datetime.fromisoformat(str(v).strip()[:19])


def run() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # ── counties ──────────────────────────────────────────────────────────
        added = 0
        for c in rows("counties.csv"):
            if db.query(County).filter(County.name == c["name"]).first():
                continue
            idp = num(c.get("idp_count"))
            db.add(County(
                name=c["name"], pcode=c["pcode"], state=c["state"],
                idp_count=int(idp) if idp is not None else 0,
                idp_updated_at=datetime.utcnow() if idp is not None else None,
            ))
            added += 1
        db.flush()
        print(f"counties      : +{added}")

        # ── facilities ────────────────────────────────────────────────────────
        fac_ids: dict[str, str] = {}
        added = 0
        for f in rows("facilities.csv"):
            row = db.query(Facility).filter(Facility.name == f["name"]).first()
            if not row:
                row = Facility(
                    name=f["name"], county=f["county"], state=f["state"],
                    latitude=num(f.get("latitude")),
                    longitude=num(f.get("longitude")),
                    elevation_m=num(f.get("elevation_m")),
                    flood_affected_norm=num(f.get("flood_affected_norm"), 0.0),
                    active=str(f.get("active", "true")).lower() != "false",
                )
                db.add(row)
                db.flush()
                added += 1
            fac_ids[f["name"]] = row.id
        print(f"facilities    : +{added} ({len(fac_ids)} total)")

        # ── workers ───────────────────────────────────────────────────────────
        worker_ids: dict[str, str] = {}
        added = 0
        for w in rows("workers.csv"):
            row = db.query(Worker).filter(Worker.worker_id == w["worker_id"]).first()
            if not row:
                fac_name = (w.get("facility_id") or "").strip()
                if fac_name and fac_name not in fac_ids:
                    sys.exit(f"ERROR: worker {w['worker_id']} references unknown facility {fac_name!r}")
                row = Worker(
                    worker_id=w["worker_id"], name=w["name"],
                    pin_hash=hash_pin(w["pin"]),
                    role=w.get("role") or "CHW",
                    facility_id=fac_ids.get(fac_name),
                    county=w.get("county") or None,
                    phone=w.get("phone") or None,
                    active=True,
                )
                db.add(row)
                db.flush()
                added += 1
            worker_ids[w["worker_id"]] = row.id
        print(f"workers       : +{added} ({len(worker_ids)} total)")

        # ── children ──────────────────────────────────────────────────────────
        # child_ref is an import key only. It is stored as qr_code so a re-run
        # can recognise the row, and so vaccinations can be attached to it.
        child_ids: dict[str, str] = {}
        added = 0
        for c in rows("children.csv"):
            ref = c["child_ref"]
            qr = (c.get("qr_code") or "").strip() or ref
            row = db.query(Child).filter(Child.qr_code == qr).first()
            if not row:
                fac_name = (c.get("facility_id") or "").strip()
                if fac_name not in fac_ids:
                    sys.exit(f"ERROR: child {ref} references unknown facility {fac_name!r}")
                born = date(c.get("born_date"))
                if born is None:
                    sys.exit(f"ERROR: child {ref} has no born_date; age drives the whole EPI schedule")
                row = Child(
                    name=c["name"], gender=c.get("gender") or None, born_date=born,
                    facility_id=fac_ids[fac_name],
                    worker_id=worker_ids.get((c.get("worker_id") or "").strip()),
                    parent_name=c.get("parent_name") or None,
                    parent_phone=c.get("parent_phone") or None,
                    current_location=c.get("current_location") or None,
                    distance_km=num(c.get("distance_km")),
                    latitude=num(c.get("latitude")),
                    longitude=num(c.get("longitude")),
                    qr_code=qr,
                    status=c.get("status") or "toVisit",
                )
                db.add(row)
                db.flush()
                added += 1
            child_ids[ref] = row.id
        print(f"children      : +{added} ({len(child_ids)} total)")

        # ── vaccinations ──────────────────────────────────────────────────────
        # A dose outside EPI_SCHEDULE is silently ignored when computing
        # vaccination debt, which would make a child look up to date. Refuse
        # the import instead of letting that through.
        vocab = set(EPI_SCHEDULE)
        added = 0
        for v in rows("vaccinations.csv"):
            ref = v["child_ref"]
            if ref not in child_ids:
                sys.exit(f"ERROR: vaccination references unknown child_ref {ref!r}")
            vaccine = v["vaccine"].strip()
            if vaccine not in vocab:
                sys.exit(
                    f"ERROR: vaccine {vaccine!r} is not in EPI_SCHEDULE. It would be "
                    f"ignored when computing overdue doses, making children look "
                    f"vaccinated. Fix the CSV or add it to app/ml/epi.py."
                )
            given = date(v.get("date_given"))
            # Natural key for idempotency; client_uuid is uniquely indexed.
            uuid = f"seed:{ref}:{vaccine}:{v.get('date_given') or ''}"
            if db.query(Vaccination).filter(Vaccination.client_uuid == uuid).first():
                continue
            db.add(Vaccination(
                child_id=child_ids[ref], vaccine=vaccine,
                dose=v.get("dose") or None, date_given=given,
                status=v.get("status") or "given",
                batch_number=v.get("batch_number") or None,
                notes=v.get("notes") or None,
                client_uuid=uuid, synced=True,
            ))
            added += 1
            if added % 2000 == 0:
                db.flush()
        print(f"vaccinations  : +{added}")

        # ── radar history ─────────────────────────────────────────────────────
        added = 0
        seeded_facilities = set()
        for r in rows("vv_history.csv"):
            fac_name = r["facility_name"]
            if fac_name not in fac_ids:
                continue
            observed = date(r["observed_at"])
            fid = fac_ids[fac_name]
            exists = (
                db.query(VVHistory)
                .filter(VVHistory.facility_id == fid, VVHistory.observed_at == observed)
                .first()
            )
            if exists:
                continue
            db.add(VVHistory(
                facility_id=fid,
                vv_backscatter=num(r["vv_backscatter"]),
                observed_at=observed,
            ))
            seeded_facilities.add(fac_name)
            added += 1
        print(f"vv_history    : +{added} rows across {len(seeded_facilities)} facilities")
        print(f"                {len(fac_ids) - len(seeded_facilities)} facilities have no "
              f"radar and will score from seasonal estimates")

        db.commit()

        # ── scores ────────────────────────────────────────────────────────────
        print("\nComputing CDI + child risk scores ...")
        result = refresh_all_scores(db)
        print(f"  scored {result['facilities']} facilities, {result['children']} children")
        print("\nImport complete. Login: CHW-001 / 1234")
    finally:
        db.close()


if __name__ == "__main__":
    run()
