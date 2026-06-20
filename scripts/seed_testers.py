"""
Add tester accounts + demo children for client UAT.

Usage:
    python -m scripts.seed_testers

Idempotent: existing workers (by worker_id) and children (by qr_code) are
skipped, so it is safe to re-run. Adds 3 CHW logins and 12 children spread
across them with varied vaccination histories (so risk bands differ), then
recomputes CDI + risk scores.

Logins created:
    CHW-003 / 3344   CHW-004 / 5566   CHW-005 / 7788
"""
from datetime import datetime, timedelta

from app.core.security import hash_pin
from app.db.database import SessionLocal
from app.db.models import Child, Facility, Vaccination, Worker
from app.services.scoring import refresh_all_scores

# New CHW accounts -> assigned facility (must already exist from scripts.seed)
WORKERS = [
    {"worker_id": "CHW-003", "name": "Peter Gatkuoth", "pin": "3344",
     "facility": "Nhialdiu PHCC",      "county": "Rubkona", "phone": "+211 92 300 0003"},
    {"worker_id": "CHW-004", "name": "Sarah Nyibol",   "pin": "5566",
     "facility": "Koch Health Centre", "county": "Koch",    "phone": "+211 92 300 0004"},
    {"worker_id": "CHW-005", "name": "John Deng",      "pin": "7788",
     "facility": "Wau Shilluk Health", "county": "Malakal", "phone": "+211 92 300 0005"},
]

# 12 children, 4 per new CHW, with a mix of vaccination states for varied risk.
# given = antigens already received (must match the EPI schedule names).
CHILDREN = [
    # ── CHW-003 · Nhialdiu PHCC ──────────────────────────────────────────────
    {"worker": "CHW-003", "facility": "Nhialdiu PHCC", "qr": "C-3001",
     "name": "Nyalong Chol", "gender": "F", "age_weeks": 6, "distance": 2.3,
     "location": "Nhialdiu · Block A", "parent": "Nyakuoth Chol (mother)",
     "phone": "+211 92 311 0011", "last_seen_days": None, "status": "toVisit", "given": []},
    {"worker": "CHW-003", "facility": "Nhialdiu PHCC", "qr": "C-3002",
     "name": "Gatwech Puok", "gender": "M", "age_weeks": 10, "distance": 3.1,
     "location": "Nhialdiu · near market", "parent": "Rebecca Nyaluak (mother)",
     "phone": "+211 92 311 0022", "last_seen_days": 30, "status": "toVisit",
     "given": ["BCG", "OPV-0"]},
    {"worker": "CHW-003", "facility": "Nhialdiu PHCC", "qr": "C-3003",
     "name": "Adut Mayen", "gender": "F", "age_weeks": 40, "distance": 1.7,
     "location": "Nhialdiu Road · Sector 2", "parent": "Sarah Adut (mother)",
     "phone": "+211 92 311 0033", "last_seen_days": 50, "status": "toVisit",
     "given": ["BCG", "Penta-1", "OPV-1", "Penta-3"]},
    {"worker": "CHW-003", "facility": "Nhialdiu PHCC", "qr": "C-3004",
     "name": "Riek Garang", "gender": "M", "age_weeks": 14, "distance": 4.5,
     "location": "Rironi village", "parent": "Nyandeng Riek (mother)",
     "phone": "+211 92 311 0044", "last_seen_days": 12, "status": "visited",
     "given": ["BCG", "OPV-0", "Penta-1", "OPV-1"]},

    # ── CHW-004 · Koch Health Centre ─────────────────────────────────────────
    {"worker": "CHW-004", "facility": "Koch Health Centre", "qr": "C-4001",
     "name": "Nyakim Bol", "gender": "F", "age_weeks": 6, "distance": 1.2,
     "location": "Koch Town · centre", "parent": "Achol Bol (mother)",
     "phone": "+211 92 411 0011", "last_seen_days": None, "status": "toVisit", "given": []},
    {"worker": "CHW-004", "facility": "Koch Health Centre", "qr": "C-4002",
     "name": "Both Chuol", "gender": "M", "age_weeks": 38, "distance": 5.0,
     "location": "Koch · outskirts", "parent": "Mary Chuol (mother)",
     "phone": "+211 92 411 0022", "last_seen_days": 45, "status": "toVisit",
     "given": ["BCG", "Penta-1", "OPV-1", "Penta-2", "Penta-3", "OPV-3"]},
    {"worker": "CHW-004", "facility": "Koch Health Centre", "qr": "C-4003",
     "name": "Nyawech Riek", "gender": "F", "age_weeks": 10, "distance": 2.8,
     "location": "Koch · Block C", "parent": "Tabitha Riek (mother)",
     "phone": "+211 92 411 0033", "last_seen_days": 20, "status": "toVisit",
     "given": ["BCG", "OPV-0"]},
    {"worker": "CHW-004", "facility": "Koch Health Centre", "qr": "C-4004",
     "name": "James Lual", "gender": "M", "age_weeks": 80, "distance": 3.3,
     "location": "Koch village", "parent": "Grace Lual (mother)",
     "phone": "+211 92 411 0044", "last_seen_days": 8, "status": "visited",
     "given": ["BCG", "OPV-0", "Penta-1", "Penta-2", "Penta-3", "Measles-1"]},

    # ── CHW-005 · Wau Shilluk Health ─────────────────────────────────────────
    {"worker": "CHW-005", "facility": "Wau Shilluk Health", "qr": "C-5001",
     "name": "Akech Deng", "gender": "F", "age_weeks": 6, "distance": 2.0,
     "location": "Wau Shilluk Camp · Block B", "parent": "Nyibol Deng (mother)",
     "phone": "+211 92 511 0011", "last_seen_days": None, "status": "toVisit", "given": []},
    {"worker": "CHW-005", "facility": "Wau Shilluk Health", "qr": "C-5002",
     "name": "Peter Okello", "gender": "M", "age_weeks": 14, "distance": 4.0,
     "location": "Wau Shilluk · North", "parent": "Joyce Okello (mother)",
     "phone": "+211 92 511 0022", "last_seen_days": 25, "status": "toVisit",
     "given": ["BCG", "OPV-0", "Penta-1"]},
    {"worker": "CHW-005", "facility": "Wau Shilluk Health", "qr": "C-5003",
     "name": "Aluel Garang", "gender": "F", "age_weeks": 40, "distance": 1.5,
     "location": "Wau Shilluk · South", "parent": "Mary Garang (mother)",
     "phone": "+211 92 511 0033", "last_seen_days": 10, "status": "visited",
     "given": ["BCG", "Penta-1", "OPV-1", "Penta-3", "Measles-1"]},
    {"worker": "CHW-005", "facility": "Wau Shilluk Health", "qr": "C-5004",
     "name": "Chol Majok", "gender": "M", "age_weeks": 10, "distance": 6.0,
     "location": "Wau Shilluk · East", "parent": "Rebecca Majok (mother)",
     "phone": "+211 92 511 0044", "last_seen_days": 60, "status": "toVisit",
     "given": ["BCG"]},
]


def run():
    db = SessionLocal()
    try:
        # Resolve facilities + create workers
        worker_ids = {}
        for wd in WORKERS:
            w = db.query(Worker).filter(Worker.worker_id == wd["worker_id"]).first()
            if not w:
                fac = db.query(Facility).filter(Facility.name == wd["facility"]).first()
                if not fac:
                    print(f"  ! facility '{wd['facility']}' not found; run scripts.seed first")
                    continue
                w = Worker(
                    worker_id=wd["worker_id"], name=wd["name"],
                    pin_hash=hash_pin(wd["pin"]), facility_id=fac.id,
                    county=wd["county"], phone=wd["phone"], role="CHW", active=True,
                )
                db.add(w)
                db.flush()
                print(f"  + worker {wd['worker_id']} ({wd['name']}) PIN {wd['pin']}")
            worker_ids[wd["worker_id"]] = w.id

        # Create children + vaccination history
        added = 0
        for cd in CHILDREN:
            if db.query(Child).filter(Child.qr_code == cd["qr"]).first():
                continue
            fac = db.query(Facility).filter(Facility.name == cd["facility"]).first()
            wid = worker_ids.get(cd["worker"])
            if not fac or not wid:
                print(f"  ! skipping {cd['name']} (missing facility/worker)")
                continue
            born = datetime.utcnow() - timedelta(weeks=cd["age_weeks"])
            last_seen = (
                datetime.utcnow() - timedelta(days=cd["last_seen_days"])
                if cd["last_seen_days"] is not None else None
            )
            child = Child(
                name=cd["name"], gender=cd["gender"], born_date=born,
                facility_id=fac.id, worker_id=wid,
                parent_name=cd["parent"], parent_phone=cd["phone"],
                current_location=cd["location"], distance_km=cd["distance"],
                qr_code=cd["qr"], status=cd["status"], last_seen=last_seen,
            )
            db.add(child)
            db.flush()
            for vac in cd["given"]:
                db.add(Vaccination(
                    child_id=child.id, vaccine=vac, status="given",
                    date_given=born + timedelta(weeks=2), administered_by=wid,
                ))
            added += 1
            print(f"  + child {cd['name']} ({cd['qr']}) -> {cd['worker']}")

        db.commit()

        print(f"\nCreated {added} children. Recomputing scores...")
        result = refresh_all_scores(db)
        print(f"  scored {result['facilities']} facilities, {result['children']} children")

        print("\nTester logins to share:")
        print("  CHW-003 / 3344   (Nhialdiu PHCC)")
        print("  CHW-004 / 5566   (Koch Health Centre)")
        print("  CHW-005 / 7788   (Wau Shilluk Health)")
    finally:
        db.close()


if __name__ == "__main__":
    run()
