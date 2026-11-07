"""Validity and uniqueness: is each value a legal value?

Covers requirements R5-R9 of docs/traceability.md.
"""

from __future__ import annotations

import pandas as pd

from conftest import PLAUSIBLE_MAX, PLAUSIBLE_MIN, VALID_INDEX


def test_no_duplicate_period_starts(fact):
    """DQ-07 | R5 | The natural key is unique.

    Chunked fetching overlaps at the seams, so duplicates are a live risk rather
    than a theoretical one.
    """
    dupes = fact["period_start_utc"][fact["period_start_utc"].duplicated()]
    assert dupes.empty, (
        f"{dupes.nunique()} duplicated period start(s): "
        f"{[str(x) for x in dupes.unique()[:5]]}"
    )


def test_forecast_values_are_numeric_and_in_range(fact):
    """DQ-08 | R6 | Forecast is a number inside a physically plausible range."""
    col = pd.to_numeric(fact["forecast"], errors="coerce")
    bad = fact[col.isna() | (col < PLAUSIBLE_MIN) | (col > PLAUSIBLE_MAX)]
    assert bad.empty, (
        f"{len(bad)} forecast value(s) non-numeric or outside "
        f"{PLAUSIBLE_MIN}-{PLAUSIBLE_MAX} gCO2/kWh: "
        f"{bad[['period_start_utc', 'forecast']].head(3).to_dict('records')}"
    )


def test_actual_values_are_non_negative(fact):
    """DQ-09 | R6 | Carbon intensity cannot be negative."""
    col = pd.to_numeric(fact["actual"], errors="coerce")
    bad = fact[col.notna() & (col < 0)]
    assert bad.empty, (
        f"{len(bad)} negative actual(s): "
        f"{bad[['period_start_utc', 'actual']].head(3).to_dict('records')}"
    )


def test_actual_values_are_within_a_plausible_range(fact):
    """DQ-10 | R6 | Actuals sit inside a plausible band for the GB grid.

    The observed maximum over 90 days is about 250, so 1000 is a wide net that
    still catches a unit error or a sentinel value such as 9999.
    """
    col = pd.to_numeric(fact["actual"], errors="coerce")
    bad = fact[col.notna() & ((col < PLAUSIBLE_MIN) | (col > PLAUSIBLE_MAX))]
    assert bad.empty, (
        f"{len(bad)} actual(s) outside {PLAUSIBLE_MIN}-{PLAUSIBLE_MAX} gCO2/kWh: "
        f"{bad[['period_start_utc', 'actual']].head(3).to_dict('records')}"
    )


def test_index_values_are_documented_categories(fact):
    """DQ-11 | R7 | The index band is one of the API's five published values."""
    present = set(fact["intensity_index"].dropna().unique())
    unknown = present - VALID_INDEX
    assert not unknown, (
        f"index value(s) not in the documented set {sorted(VALID_INDEX)}: {sorted(unknown)}"
    )


def test_has_actual_flag_agrees_with_the_actual_column(fact):
    """DQ-12 | R8 | The denormalised flag has not drifted from the value."""
    mismatched = fact[fact["actual"].notna() != (fact["has_actual"] == 1)]
    assert mismatched.empty, (
        f"{len(mismatched)} row(s) where has_actual disagrees with actual being null"
    )


def test_missing_actuals_are_null_not_zero(fact):
    """DQ-13 | R9 | Unknown is stored as NULL, never imputed as zero.

    A zero-filled actual would silently drag every average down and make the
    forecast look worse than it is.
    """
    zeros = fact[pd.to_numeric(fact["actual"], errors="coerce") == 0]
    assert zeros.empty, (
        f"{len(zeros)} actual(s) stored as zero; unknown values must stay NULL"
    )
