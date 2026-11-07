"""Data-quality checks on the transformed table.

Every check returns a CheckResult. Hard checks raise ValidationError when they
fail -- a silent bad load is worse than a noisy stop. Missing actuals are a known
property of the source (recent periods are forecast-only until settlement), so
they are measured and reported rather than treated as a failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

LOGGER = logging.getLogger(__name__)

PLAUSIBLE_MIN = 0
PLAUSIBLE_MAX = 1000  # gCO2/kWh; GB has never been near this
EXPECTED_STEP = pd.Timedelta(minutes=30)
VALID_INDEX = {"very low", "low", "moderate", "high", "very high"}


class ValidationError(AssertionError):
    """Raised when one or more hard data-quality checks fail."""


@dataclass
class CheckResult:
    name: str
    passed: bool
    severity: str  # "error" or "warning"
    count: int
    detail: str
    sample: list[Any] = field(default_factory=list)

    def __str__(self) -> str:
        status = "PASS" if self.passed else ("FAIL" if self.severity == "error" else "WARN")
        return f"[{status}] {self.name}: {self.detail}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.name,
            "passed": self.passed,
            "severity": self.severity,
            "issue_count": self.count,
            "detail": self.detail,
        }


def check_not_empty(df: pd.DataFrame) -> CheckResult:
    n = len(df)
    return CheckResult(
        "not_empty", n > 0, "error", 0 if n else 1,
        f"{n} periods in table" if n else "table is empty",
    )


def check_no_duplicate_periods(df: pd.DataFrame) -> CheckResult:
    dupes = df["period_start_utc"][df["period_start_utc"].duplicated()]
    n = int(dupes.nunique())
    return CheckResult(
        "no_duplicate_periods", n == 0, "error", n,
        "no duplicate period starts" if n == 0 else f"{n} duplicated period start(s)",
        [str(x) for x in dupes.unique()[:5]],
    )


def check_no_gaps(df: pd.DataFrame) -> CheckResult:
    starts = df["period_start_utc"].sort_values()
    deltas = starts.diff().dropna()
    bad = deltas[deltas != EXPECTED_STEP]
    n = int(len(bad))
    if n == 0:
        detail = f"continuous 30-minute sequence across {len(starts)} periods"
    else:
        missing = int(((bad - EXPECTED_STEP) / EXPECTED_STEP).sum())
        detail = f"{n} break(s) in the sequence, about {missing} period(s) missing"
    return CheckResult(
        "no_gaps_in_sequence", n == 0, "error", n, detail,
        [str(starts.loc[i]) for i in bad.index[:5]],
    )


def check_period_length(df: pd.DataFrame) -> CheckResult:
    lengths = df["period_end_utc"] - df["period_start_utc"]
    bad = lengths[lengths != EXPECTED_STEP]
    n = int(len(bad))
    return CheckResult(
        "period_is_30_minutes", n == 0, "error", n,
        "every period spans exactly 30 minutes" if n == 0 else f"{n} period(s) are not 30 minutes long",
    )


def check_forecast_numeric_and_in_range(df: pd.DataFrame) -> CheckResult:
    col = df["forecast"]
    bad = col.isna() | (col < PLAUSIBLE_MIN) | (col > PLAUSIBLE_MAX)
    n = int(bad.sum())
    return CheckResult(
        "forecast_numeric_in_range", n == 0, "error", n,
        f"all forecasts numeric and within {PLAUSIBLE_MIN}-{PLAUSIBLE_MAX} gCO2/kWh"
        if n == 0 else f"{n} forecast value(s) missing or out of range",
    )


def check_actual_in_range(df: pd.DataFrame) -> CheckResult:
    col = df["actual"].dropna()
    bad = (col < PLAUSIBLE_MIN) | (col > PLAUSIBLE_MAX)
    n = int(bad.sum())
    return CheckResult(
        "actual_in_range", n == 0, "error", n,
        f"all {len(col)} present actuals within {PLAUSIBLE_MIN}-{PLAUSIBLE_MAX} gCO2/kWh"
        if n == 0 else f"{n} actual value(s) out of range",
    )


def check_index_values(df: pd.DataFrame) -> CheckResult:
    present = set(df["intensity_index"].dropna().unique())
    unknown = present - VALID_INDEX
    n = len(unknown)
    return CheckResult(
        "index_in_known_set", n == 0, "error", n,
        f"index values all known ({', '.join(sorted(present))})"
        if n == 0 else f"unexpected index value(s): {sorted(unknown)}",
    )


def check_missing_actuals(df: pd.DataFrame) -> CheckResult:
    n = int((~df["has_actual"]).sum())
    pct = (n / len(df) * 100) if len(df) else 0.0
    return CheckResult(
        "missing_actuals", n == 0, "warning", n,
        "every period has a settled actual" if n == 0
        else f"{n} period(s) ({pct:.2f}%) have no actual yet -- expected near the end of the window",
    )


def check_no_future_periods(df: pd.DataFrame) -> CheckResult:
    now = pd.Timestamp.now(tz="UTC")
    future = df["period_start_utc"] > now
    n = int(future.sum())
    return CheckResult(
        "no_future_periods", n == 0, "warning", n,
        "no periods start in the future" if n == 0
        else f"{n} period(s) start in the future (forecast-only rows)",
    )


CHECKS = [
    check_not_empty,
    check_no_duplicate_periods,
    check_no_gaps,
    check_period_length,
    check_forecast_numeric_and_in_range,
    check_actual_in_range,
    check_index_values,
    check_missing_actuals,
    check_no_future_periods,
]


def run_checks(df: pd.DataFrame) -> list[CheckResult]:
    results = []
    for check in CHECKS:
        result = check(df)
        results.append(result)
        log = LOGGER.info if result.passed else (
            LOGGER.error if result.severity == "error" else LOGGER.warning
        )
        log("%s", result)
    return results


def validate(df: pd.DataFrame, strict: bool = True) -> list[CheckResult]:
    """Run every check. Raise on a failed hard check when ``strict``."""
    results = run_checks(df)
    failures = [r for r in results if not r.passed and r.severity == "error"]
    LOGGER.info(
        "validation: %d checks run, %d passed, %d failed, %d warning(s)",
        len(results),
        sum(r.passed for r in results),
        len(failures),
        sum(1 for r in results if not r.passed and r.severity == "warning"),
    )
    if failures and strict:
        raise ValidationError(
            "data-quality checks failed: " + "; ".join(f.name for f in failures)
        )
    return results


def results_frame(results: list[CheckResult]) -> pd.DataFrame:
    """Checks as a table, so Power BI's Data Quality page can read them from SQL."""
    return pd.DataFrame([r.as_dict() for r in results])
