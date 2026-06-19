"""
South Sudan EPI (Expanded Programme on Immunisation) schedule and the
vaccination-debt / age-urgency helpers used by the child risk score.

Weeks are weeks-of-age at which each antigen is due.
"""
from typing import List

# Antigen -> due age in weeks
EPI_SCHEDULE = {
    "BCG": 0, "OPV-0": 0,
    "Penta-1": 6, "OPV-1": 6, "PCV-1": 6, "Rota-1": 6,
    "Penta-2": 10, "OPV-2": 10, "PCV-2": 10, "Rota-2": 10,
    "Penta-3": 14, "OPV-3": 14, "PCV-3": 14,
    "Measles-1": 36, "Yellow Fever": 36,
    "Measles-2": 78,
}

# A dose is only counted as "overdue" once it is this many weeks past due.
GRACE_WEEKS = 2


def due_antigens(age_weeks: float) -> List[str]:
    """All antigens that should have been given by this age (past grace)."""
    return [v for v, w in EPI_SCHEDULE.items() if age_weeks >= (w + GRACE_WEEKS)]


def compute_vaccination_debt(age_weeks: float, given: List[str]) -> float:
    """VD = overdue doses / total due doses  (0.0 .. 1.0)."""
    given_set = set(given)
    due = due_antigens(age_weeks)
    if not due:
        return 0.0
    overdue = sum(1 for v in due if v not in given_set)
    return overdue / len(due)


def overdue_count(age_weeks: float, given: List[str]) -> int:
    given_set = set(given)
    return sum(1 for v in due_antigens(age_weeks) if v not in given_set)


def compute_age_urgency(age_weeks: float) -> float:
    """Younger infants are most vulnerable -> higher urgency weight."""
    if age_weeks <= 2:
        return 1.00
    if age_weeks <= 16:
        return 0.95
    if age_weeks <= 36:
        return 0.80
    if age_weeks <= 78:
        return 0.65
    return 0.50
