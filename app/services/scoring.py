"""
Scoring engine — the differentiating feature of Salama Health.

Two scores:
  1. CDI (Climate Disruption Index) per facility:
        CDI = 0.35·P(flood) + 0.30·P(cutoff) + 0.20·P(CCF) + 0.15·P(disp)
     P(flood) comes from the ML ensemble (XGBoost + RF [+ LSTM]); the other
     components are derived from the same feature vector.

  2. IGS (Immunisation Gap Score) per child, normalised to 0..1:
        IGS_raw = CDI · VaccinationDebt · (1/Accessibility) · AgeUrgency
     A child's CDI is taken from its facility's latest CDI.

CACHING STRATEGY (scales to many concurrent users)
--------------------------------------------------
Scores are expensive-ish (model inference) but change slowly (climate data is
daily). So we compute them on a schedule / on relevant writes and persist them
to the `cdi_scores` and `risk_scores` tables with an `is_current` flag. All
read endpoints just SELECT the current row — O(1) DB reads that scale linearly
with Postgres, independent of model throughput.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CDIScore, Child, Facility, RiskScore, Vaccination
from app.ml import epi
from app.ml.features import build_feature_vector, is_rainy_season
from app.ml.model_loader import model_manager

logger = logging.getLogger(__name__)

CHIRPS_RAIN_MAX = 103.8


# ── CDI helpers ───────────────────────────────────────────────────────────────
def cdi_risk_level(cdi: float) -> str:
    if cdi >= 0.60:
        return "danger"
    if cdi >= 0.40:
        return "warning"
    return "ok"


def hazard_label(p_flood: float, p_ccf: float, p_disp: float) -> str:
    if p_flood > 0.40:
        return "Seasonal flooding"
    if p_ccf > 0.30:
        return "Heatwave cold chain risk"
    if p_disp > 0.50:
        return "Population displacement"
    return "No active hazard"


def days_to_window(cdi: float) -> int:
    if cdi >= 0.50:
        return 7
    if cdi >= 0.40:
        return 14
    if cdi >= 0.30:
        return 21
    return 0


def hazard_detail(p_flood: float, p_ccf: float, rain_mm: float) -> str:
    parts = []
    if p_flood > 0.30:
        parts.append(
            f"Flood risk elevated (P={p_flood:.2f}) with ~{rain_mm:.0f}mm recent rainfall."
        )
    if p_ccf > 0.25:
        parts.append(
            f"Cold-chain failure risk (P={p_ccf:.2f}) — temperatures exceeding the 8°C threshold."
        )
    if not parts:
        return "No significant climate hazard currently active at this facility."
    return " ".join(parts)


def compute_cdi(p_flood: float, p_cutoff: float, p_ccf: float, p_disp: float) -> float:
    return round(0.35 * p_flood + 0.30 * p_cutoff + 0.20 * p_ccf + 0.15 * p_disp, 4)


def score_facility(facility: Facility) -> dict:
    """Run inference + formula for one facility. Pure (no DB writes)."""
    vec = build_feature_vector(facility.elevation_m)
    p_flood = model_manager.predict_flood_proba(vec)

    rain_mm = float(vec[6])
    low_elev = float(vec[5])
    p_cutoff = min(low_elev * 0.5 + (rain_mm / CHIRPS_RAIN_MAX) * 0.5, 1.0)
    p_ccf = float(vec[17])          # ccf_risk feature
    p_disp = float(vec[24])         # idp_normalised feature

    cdi = compute_cdi(p_flood, p_cutoff, p_ccf, p_disp)
    risk = cdi_risk_level(cdi)
    return {
        "cdi_score": cdi,
        "p_flood": round(p_flood, 4),
        "p_cutoff": round(p_cutoff, 4),
        "p_ccf": round(p_ccf, 4),
        "p_disp": round(p_disp, 4),
        "risk_level": risk,
        "hazard": hazard_label(p_flood, p_ccf, p_disp),
        "days_to_window": days_to_window(cdi),
        "hazard_detail": hazard_detail(p_flood, p_ccf, rain_mm),
        "hazard_timeframe": "Expected to last 3 to 4 weeks" if cdi > 0.30 else "No disruption forecast",
    }


# ── IGS (child risk) helpers ──────────────────────────────────────────────────
def risk_label(score: float) -> str:
    if score >= 0.90:
        return "High"
    if score >= 0.80:
        return "Medium"
    if score >= 0.72:
        return "Watch"
    return "Low"


def compute_accessibility(distance_km: float, cdi: float, rainy: bool) -> float:
    road_difficulty = 1.0 + cdi
    season_factor = 1.5 if rainy else 1.0
    acc = 1.0 / (max(distance_km, 0.1) * road_difficulty * season_factor)
    return float(min(acc, 1.0))


def age_weeks_of(child: Child) -> float:
    if child.born_date:
        return max((datetime.utcnow() - child.born_date).days / 7, 0.0)
    return 26.0  # default ~6 months if DOB unknown


def score_child(child: Child, facility_cdi: float) -> dict:
    """Compute IGS for a child given its facility's current CDI. Pure."""
    given = [v.vaccine for v in child.vaccinations if v.status == "given"]
    age_weeks = age_weeks_of(child)

    vd = epi.compute_vaccination_debt(age_weeks, given)
    overdue = epi.overdue_count(age_weeks, given)
    uw = epi.compute_age_urgency(age_weeks)

    distance = child.distance_km if child.distance_km is not None else 2.0
    rainy = is_rainy_season(datetime.utcnow().month)
    acc = compute_accessibility(distance, facility_cdi, rainy)

    inv_acc = 1.0 / max(acc, 0.01)
    igs_raw = facility_cdi * vd * inv_acc * uw
    igs_norm = float(min(igs_raw / 5.0, 1.0))   # soft cap at 5.0 -> normalise

    return {
        "risk_score": round(igs_norm, 4),
        "risk_label": risk_label(igs_norm),
        "cdi": round(facility_cdi, 4),
        "vaccination_debt": round(vd, 4),
        "accessibility": round(acc, 4),
        "age_urgency": round(uw, 4),
        "overdue_count": overdue,
    }


# ── persistence / refresh ─────────────────────────────────────────────────────
def _persist_cdi(db: Session, facility: Facility, result: dict) -> CDIScore:
    db.query(CDIScore).filter(
        CDIScore.facility_id == facility.id, CDIScore.is_current == True  # noqa: E712
    ).update({"is_current": False})
    row = CDIScore(facility_id=facility.id, is_current=True, **result)
    db.add(row)
    return row


def _persist_risk(db: Session, child: Child, result: dict) -> RiskScore:
    db.query(RiskScore).filter(
        RiskScore.child_id == child.id, RiskScore.is_current == True  # noqa: E712
    ).update({"is_current": False})
    row = RiskScore(child_id=child.id, is_current=True, **result)
    db.add(row)
    return row


def refresh_facility_cdi(db: Session, facility: Facility, commit: bool = True) -> CDIScore:
    row = _persist_cdi(db, facility, score_facility(facility))
    if commit:
        db.commit()
    return row


def refresh_child_risk(db: Session, child: Child, commit: bool = True) -> RiskScore:
    cdi = get_current_cdi_value(db, child.facility_id)
    row = _persist_risk(db, child, score_child(child, cdi))
    if commit:
        db.commit()
    return row


def refresh_all_scores(db: Session) -> dict:
    """
    Recompute CDI for every facility, then IGS for every child.
    Called by the daily scheduler and the /climate/refresh endpoint.
    """
    facilities = db.query(Facility).filter(Facility.active == True).all()  # noqa: E712
    cdi_by_facility: dict[str, float] = {}
    for f in facilities:
        result = score_facility(f)
        _persist_cdi(db, f, result)
        cdi_by_facility[f.id] = result["cdi_score"]
    db.commit()

    children = db.query(Child).all()
    for c in children:
        cdi = cdi_by_facility.get(c.facility_id, 0.30)
        _persist_risk(db, c, score_child(c, cdi))
    db.commit()

    logger.info(
        "Score refresh complete: %d facilities, %d children",
        len(facilities), len(children),
    )
    return {"facilities": len(facilities), "children": len(children)}


# ── current-score getters ─────────────────────────────────────────────────────
def get_current_cdi(db: Session, facility_id: Optional[str]) -> Optional[CDIScore]:
    if not facility_id:
        return None
    return (
        db.query(CDIScore)
        .filter(CDIScore.facility_id == facility_id, CDIScore.is_current == True)  # noqa: E712
        .first()
    )


def get_current_cdi_value(db: Session, facility_id: Optional[str], default: float = 0.30) -> float:
    row = get_current_cdi(db, facility_id)
    return row.cdi_score if row and row.cdi_score is not None else default


def get_current_risk(db: Session, child_id: str) -> Optional[RiskScore]:
    return (
        db.query(RiskScore)
        .filter(RiskScore.child_id == child_id, RiskScore.is_current == True)  # noqa: E712
        .first()
    )


def due_vaccines_for(child: Child) -> list[str]:
    """Antigens that are due/overdue and not yet given (for the child card)."""
    given = {v.vaccine for v in child.vaccinations if v.status == "given"}
    age_weeks = age_weeks_of(child)
    return [v for v in epi.due_antigens(age_weeks) if v not in given]
