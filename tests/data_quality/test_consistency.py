"""Consistency and continuity: do the rows agree with each other and with the schema?

Covers requirements R10-R14 of docs/traceability.md.
"""

from __future__ import annotations

import pandas as pd

STEP = pd.Timedelta(minutes=30)


def test_no_gaps_in_the_period_sequence(fact):
    """DQ-14 | R10 | Each period starts exactly 30 minutes after the previous one."""
    starts = fact["period_start_utc"].sort_values()
    deltas = starts.diff().dropna()
    breaks = deltas[deltas != STEP]
    detail = ""
    if len(breaks):
        missing = int(((breaks - STEP) / STEP).sum())
        first = starts.loc[breaks.index[0]]
        detail = f"; first break before {first}, about {missing} period(s) absent"
    assert breaks.empty, f"{len(breaks)} gap(s) in the 30-minute sequence{detail}"


def test_every_period_spans_exactly_30_minutes(fact):
    """DQ-15 | R11 | End minus start is 30 minutes in every row."""
    lengths = fact["period_end_utc"] - fact["period_start_utc"]
    bad = fact[lengths != STEP]
    assert bad.empty, (
        f"{len(bad)} period(s) are not 30 minutes long, first at "
        f"{bad['period_start_utc'].iloc[0] if len(bad) else ''}"
    )


def test_periods_do_not_overlap(fact):
    """DQ-16 | R10 | One period ends exactly where the next begins."""
    ends = fact["period_end_utc"].iloc[:-1].reset_index(drop=True)
    starts = fact["period_start_utc"].iloc[1:].reset_index(drop=True)
    overlaps = (ends > starts).sum()
    assert overlaps == 0, f"{overlaps} period(s) overlap the one that follows"


def test_timestamps_are_stored_in_utc(conn):
    """DQ-17 | R12 | UTC is the stored grain, marked with a trailing Z.

    Europe/London has 46- and 50-period days at the clock changes, so a
    local-time key would be ambiguous every October.
    """
    rows = conn.execute(
        "SELECT COUNT(*) FROM fact_intensity "
        "WHERE period_start_utc NOT LIKE '%Z' OR period_end_utc NOT LIKE '%Z'"
    ).fetchone()[0]
    assert rows == 0, f"{rows} timestamp(s) are not stored as UTC with a Z suffix"


def test_local_time_column_matches_the_utc_column(fact):
    """DQ-18 | R12 | The derived local column is the same instant as the UTC one."""
    local = pd.to_datetime(fact["period_start_local"], utc=True, format="mixed")
    mismatched = (local != fact["period_start_utc"]).sum()
    assert mismatched == 0, (
        f"{mismatched} row(s) where the local column is a different instant from UTC"
    )


def test_fact_keys_resolve_in_both_dimensions(conn):
    """DQ-19 | R13 | No orphan foreign keys in the star schema."""
    orphans = conn.execute(
        "SELECT COUNT(*) FROM fact_intensity f "
        "LEFT JOIN dim_date d ON d.date_key = f.date_key "
        "LEFT JOIN dim_time t ON t.time_key = f.time_key "
        "WHERE d.date_key IS NULL OR t.time_key IS NULL"
    ).fetchone()[0]
    assert orphans == 0, f"{orphans} fact row(s) point at a missing dimension row"


def test_forecast_error_matches_actual_minus_forecast(fact):
    """DQ-20 | R14 | The stored error column agrees with its own definition."""
    scored = fact[fact["actual"].notna() & fact["forecast"].notna()].copy()
    expected = scored["actual"] - scored["forecast"]
    drift = (scored["forecast_error"] - expected).abs()
    bad = scored[drift > 1e-6]
    assert bad.empty, (
        f"{len(bad)} row(s) where forecast_error is not actual - forecast"
    )
