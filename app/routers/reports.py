"""
Reports & analytics for the Reports screen.

All figures are derived from the worker's children + vaccination records.
"""
import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_worker
from app.db.database import get_db
from app.db.models import Child, Vaccination, Worker
from app.ml import epi
from app.schemas.report import (
    CoverageByVaccine,
    DayCount,
    ReportSummary,
    VaccineCoverage,
    WeeklyDoses,
)
from app.services.scoring import age_weeks_of

router = APIRouter()

# Antigen groups shown on the coverage chart
VACCINE_GROUPS = {
    "BCG": ["BCG"],
    "OPV": ["OPV-0", "OPV-1", "OPV-2", "OPV-3"],
    "Penta": ["Penta-1", "Penta-2", "Penta-3"],
    "Measles": ["Measles-1", "Measles-2"],
    "Rota": ["Rota-1", "Rota-2"],
}


def _worker_child_ids(db: Session, worker_id: str) -> list[str]:
    return [cid for (cid,) in db.query(Child.id).filter(Child.worker_id == worker_id)]


@router.get("/summary", response_model=ReportSummary)
def summary(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    period: str = Query("month"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    wid = worker_id or current.id
    child_ids = _worker_child_ids(db, wid)
    if not child_ids:
        return ReportSummary()

    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    doses_this_month = (
        db.query(Vaccination)
        .filter(
            Vaccination.child_id.in_(child_ids),
            Vaccination.status == "given",
            Vaccination.date_given >= month_start,
        )
        .count()
    )

    # Coverage: given doses / due doses across all the worker's children
    children = db.query(Child).filter(Child.id.in_(child_ids)).all()
    total_due = 0
    total_given_due = 0
    reached = 0
    penta1 = penta3 = 0
    for child in children:
        given = {v.vaccine for v in child.vaccinations if v.status == "given"}
        if given:
            reached += 1
        due = set(epi.due_antigens(age_weeks_of(child)))
        total_due += len(due)
        total_given_due += len(due & given)
        if "Penta-1" in given:
            penta1 += 1
        if "Penta-3" in given:
            penta3 += 1

    coverage_rate = round(total_given_due / total_due, 4) if total_due else 0.0
    dropout_rate = round((penta1 - penta3) / penta1, 4) if penta1 else 0.0

    return ReportSummary(
        doses_this_month=doses_this_month,
        coverage_rate=coverage_rate,
        children_reached=reached,
        dropout_rate=max(dropout_rate, 0.0),
        period=month_start.strftime("%B %Y"),
    )


@router.get("/doses-weekly", response_model=WeeklyDoses)
def doses_weekly(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    wid = worker_id or current.id
    child_ids = _worker_child_ids(db, wid)
    today = datetime.utcnow().date()
    start = today - timedelta(days=6)

    counts: dict = defaultdict(int)
    if child_ids:
        rows = (
            db.query(Vaccination)
            .filter(
                Vaccination.child_id.in_(child_ids),
                Vaccination.status == "given",
                Vaccination.date_given >= datetime.combine(start, datetime.min.time()),
            )
            .all()
        )
        for r in rows:
            if r.date_given:
                counts[r.date_given.date()] += 1

    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        days.append(DayCount(label=d.strftime("%a"), count=counts.get(d, 0)))
    return WeeklyDoses(days=days)


@router.get("/coverage-by-vaccine", response_model=CoverageByVaccine)
def coverage_by_vaccine(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    wid = worker_id or current.id
    children = db.query(Child).filter(Child.worker_id == wid).all()
    total = len(children) or 1

    out = []
    for group, antigens in VACCINE_GROUPS.items():
        covered = 0
        antigen_set = set(antigens)
        for child in children:
            given = {v.vaccine for v in child.vaccinations if v.status == "given"}
            if given & antigen_set:
                covered += 1
        out.append(VaccineCoverage(name=group, coverage=round(covered / total, 4)))
    return CoverageByVaccine(vaccines=out)


@router.post("/export")
def export_report(
    worker_id: Optional[str] = Query(None, alias="workerId"),
    db: Session = Depends(get_db),
    current: Worker = Depends(get_current_worker),
):
    """Generate a CSV export of the worker's vaccination records."""
    wid = worker_id or current.id
    child_ids = _worker_child_ids(db, wid)
    rows = (
        db.query(Vaccination)
        .filter(Vaccination.child_id.in_(child_ids))
        .order_by(Vaccination.date_given.desc().nullslast())
        .all() if child_ids else []
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["child_id", "vaccine", "dose", "date_given", "status", "batch"])
    for r in rows:
        writer.writerow([
            r.child_id, r.vaccine, r.dose,
            r.date_given.isoformat() if r.date_given else "",
            r.status, r.batch_number or "",
        ])
    buf.seek(0)

    filename = f"salama_report_{datetime.utcnow():%Y%m%d}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
