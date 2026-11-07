"""Completeness: is everything that should be here, here?

Covers requirements R1-R4 of docs/traceability.md.
"""

from __future__ import annotations

import pandas as pd
import pytest

from conftest import RECENT_WINDOW_HOURS


def test_row_count_equals_days_times_48(fact, expected_days):
    """DQ-01 | R1 | Row count is exactly days x 48 half-hourly periods."""
    assert len(fact) == expected_days * 48, (
        f"expected {expected_days} x 48 = {expected_days * 48} periods, found {len(fact)}"
    )


def test_no_missing_forecast_values(fact):
    """DQ-02 | R2 | Every period carries a forecast; the API always publishes one."""
    missing = fact[fact["forecast"].isna()]
    assert missing.empty, (
        f"{len(missing)} period(s) have no forecast, first at "
        f"{missing['period_start_utc'].iloc[0] if len(missing) else ''}"
    )


def test_missing_actuals_only_in_the_recent_window(fact):
    """DQ-03 | R3 | Nulls in `actual` appear only near the end of the window.

    A null actual is acceptable where the period has not settled yet. A null in
    the middle of the window is a different problem -- a dropped record -- and
    must not be waved through as 'the known settlement lag'.
    """
    nulls = fact[fact["actual"].isna()]
    if nulls.empty:
        return
    cutoff = fact["period_start_utc"].max() - pd.Timedelta(hours=RECENT_WINDOW_HOURS)
    stale = nulls[nulls["period_start_utc"] < cutoff]
    assert stale.empty, (
        f"{len(stale)} null actual(s) older than {RECENT_WINDOW_HOURS}h before the end "
        f"of the window; earliest {stale['period_start_utc'].min()}. "
        "See docs/defects/DEF-001.md for the expected tail nulls."
    )


def test_missing_actuals_are_contiguous_at_the_tail(fact):
    """DQ-04 | R3 | Unsettled periods form one unbroken block at the end."""
    nulls = fact[fact["actual"].isna()]
    if nulls.empty:
        return
    tail = fact.iloc[-len(nulls):]
    assert tail["actual"].isna().all(), (
        "null actuals are scattered rather than forming a settled-lag tail, "
        "which suggests dropped records rather than pending settlement"
    )


def test_every_day_in_the_window_is_present(fact):
    """DQ-05 | R4 | No calendar day is absent from the loaded range."""
    days = fact["period_start_utc"].dt.date
    span = pd.date_range(days.min(), days.max(), freq="D").date
    missing = sorted(set(span) - set(days.unique()))
    assert not missing, f"{len(missing)} day(s) absent from the window: {missing[:5]}"


def test_dimension_tables_are_populated(conn):
    """DQ-06 | R4 | Both dimensions loaded; dim_time is always the full 48 slots."""
    times = conn.execute("SELECT COUNT(*) FROM dim_time").fetchone()[0]
    dates = conn.execute("SELECT COUNT(*) FROM dim_date").fetchone()[0]
    assert times == 48, f"dim_time holds {times} rows, expected 48"
    assert dates > 0, "dim_date is empty"
