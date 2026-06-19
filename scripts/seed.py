"""
Seed initial data: facilities, workers, a few demo children + vaccinations.

Usage:
    python -m scripts.seed

Idempotent — safe to re-run; existing rows are skipped.
Default logins after seeding:
    CHW-001 / 1234     CHW-002 / 5678
"""
from datetime import datetime, timedelta

from app.db.database import Base, SessionLocal, engine
from app.db.models import Child, County, Facility, Vaccination, Worker
from app.core.security import hash_pin
from app.services.scoring import refresh_all_scores

Base.metadata.create_all(bind=engine)

# NOTE: PCODEs below are placeholders for the CHIRPS join — replace with the
# real South Sudan admin-2 P-codes before relying on live CHIRPS ingestion.
COUNTIES = [
    {"name": "Rubkona", "pcode": "SS9201", "state": "Unity",      "idp": 120000},
    {"name": "Koch",    "pcode": "SS9203", "state": "Unity",      "idp": 28000},
    {"name": "Melut",   "pcode": "SS9601", "state": "Upper Nile", "idp": 41000},
    {"name": "Malakal", "pcode": "SS9603", "state": "Upper Nile", "idp": 95000},
]

FACILITIES = [
    {"name": "Bentiu PHCC",        "county": "Rubkona", "state": "Unity",      "lat": 9.221,  "lng": 29.801, "elev": 388.0, "flood": 0.82},
    {"name": "Nhialdiu PHCC",      "county": "Rubkona", "state": "Unity",      "lat": 9.194,  "lng": 29.789, "elev": 390.0, "flood": 0.74},
    {"name": "Meluf PHCC",         "county": "Melut",   "state": "Upper Nile", "lat": 10.453, "lng": 32.374, "elev": 398.0, "flood": 0.55},
    {"name": "Wau Shilluk Health", "county": "Malakal", "state": "Upper Nile", "lat": 9.534,  "lng": 31.659, "elev": 405.0, "flood": 0.40},
    {"name": "Koch Health Centre", "county": "Koch",    "state": "Unity",      "lat": 9.135,  "lng": 29.659, "elev": 412.0, "flood": 0.18},
]

WORKERS = [
    {"worker_id": "CHW-001", "name": "Achieng Manyiel", "pin": "1234",
     "facility": "Bentiu PHCC", "county": "Rubkona", "phone": "+211 92 123 4567"},
    {"worker_id": "CHW-002", "name": "Garang Machar",   "pin": "5678",
     "facility": "Meluf PHCC",  "county": "Melut",   "phone": "+211 92 100 0002"},
]

CHILDREN = [
    {"name": "Achol Nyakuoth", "gender": "F", "age_weeks": 6,  "distance": 2.1,
     "location": "Bentiu IDP Camp · Block C", "parent": "Nyakong Chuol (mother)",
     "phone": "+211 92 110 4471", "qr": "C-1047", "given": []},
    {"name": "Nyakim Deng",    "gender": "F", "age_weeks": 14, "distance": 3.4,
     "location": "Rubkona Town · near market", "parent": "Adhel Deng (mother)",
     "phone": "+211 92 334 8820", "qr": "C-0823", "given": ["BCG", "OPV-0", "Penta-1", "OPV-1"]},
    {"name": "Gatluak Mabior", "gender": "M", "age_weeks": 39, "distance": 1.8,
     "location": "Kaler Village", "parent": "Mary Nyandeng (mother)",
     "phone": "+211 92 556 1093", "qr": "C-2190", "given": ["BCG", "Penta-1", "Penta-3", "Rota-1"]},
]


def run():
    db = SessionLocal()
    try:
        for cd in COUNTIES:
            if not db.query(County).filter(County.name == cd["name"]).first():
                db.add(County(
                    name=cd["name"], pcode=cd["pcode"], state=cd["state"],
                    idp_count=cd["idp"], idp_updated_at=datetime.utcnow(),
                ))
                print(f"  + county {cd['name']} (idp {cd['idp']})")
        db.flush()

        fac_ids = {}
        for fd in FACILITIES:
            f = db.query(Facility).filter(Facility.name == fd["name"]).first()
            if not f:
                f = Facility(
                    name=fd["name"], county=fd["county"], state=fd["state"],
                    latitude=fd["lat"], longitude=fd["lng"], elevation_m=fd["elev"],
                    flood_affected_norm=fd["flood"], active=True,
                )
                db.add(f)
                db.flush()
                print(f"  + facility {fd['name']}")
            fac_ids[fd["name"]] = f.id

        worker_ids = {}
        for wd in WORKERS:
            w = db.query(Worker).filter(Worker.worker_id == wd["worker_id"]).first()
            if not w:
                w = Worker(
                    worker_id=wd["worker_id"], name=wd["name"],
                    pin_hash=hash_pin(wd["pin"]),
                    facility_id=fac_ids.get(wd["facility"]),
                    county=wd["county"], phone=wd["phone"], role="CHW", active=True,
                )
                db.add(w)
                db.flush()
                print(f"  + worker {wd['worker_id']} (PIN {wd['pin']})")
            worker_ids[wd["worker_id"]] = w.id

        chw1 = worker_ids["CHW-001"]
        bentiu = fac_ids["Bentiu PHCC"]
        for cd in CHILDREN:
            if db.query(Child).filter(Child.qr_code == cd["qr"]).first():
                continue
            born = datetime.utcnow() - timedelta(weeks=cd["age_weeks"])
            child = Child(
                name=cd["name"], gender=cd["gender"], born_date=born,
                facility_id=bentiu, worker_id=chw1,
                parent_name=cd["parent"], parent_phone=cd["phone"],
                current_location=cd["location"], distance_km=cd["distance"],
                qr_code=cd["qr"], status="toVisit",
            )
            db.add(child)
            db.flush()
            for vac in cd["given"]:
                db.add(Vaccination(
                    child_id=child.id, vaccine=vac, status="given",
                    date_given=born + timedelta(weeks=2), administered_by=chw1,
                ))
            print(f"  + child {cd['name']}")

        db.commit()

        print("Computing initial CDI + risk scores...")
        result = refresh_all_scores(db)
        print(f"  scored {result['facilities']} facilities, {result['children']} children")

        print("\nSeed complete. Login: worker_id=CHW-001, pin=1234")
    finally:
        db.close()


if __name__ == "__main__":
    run()
