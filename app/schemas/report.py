from typing import List

from app.schemas.common import CamelModel


class ReportSummary(CamelModel):
    doses_this_month: int = 0
    coverage_rate: float = 0.0         # 0.0 .. 1.0
    children_reached: int = 0
    dropout_rate: float = 0.0          # 0.0 .. 1.0
    # Human-readable month this summary covers, e.g. "September 2026".
    # Sent so the client does not label the figures from the phone clock,
    # which disagrees with the server across a month boundary.
    period: str | None = None


class DayCount(CamelModel):
    label: str                         # Mon, Tue, ...
    count: int


class WeeklyDoses(CamelModel):
    days: List[DayCount]


class VaccineCoverage(CamelModel):
    name: str                          # BCG, OPV, Penta, ...
    coverage: float                    # 0.0 .. 1.0


class CoverageByVaccine(CamelModel):
    vaccines: List[VaccineCoverage]
