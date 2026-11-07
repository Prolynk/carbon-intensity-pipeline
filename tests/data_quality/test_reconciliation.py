"""Reconciliation: does the warehouse still agree with the source it came from?

Every other test in this suite checks the database against rules. These check it
against the saved API responses, which is the only way to catch a transform that
is internally consistent but wrong.

Covers requirements R15-R18 of docs/traceability.md.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import load as load_mod  # noqa: E402
import transform as transform_mod  # noqa: E402

TOLERANCE = 0.01  # gCO2/kWh; the warehouse rounds nothing, so this is generous


def _raw_frame(raw_payloads) -> pd.DataFrame:
    rows = []
    for payload in raw_payloads:
        for record in payload["data"]:
            intensity = record.get("intensity") or {}
            rows.append(
                {
                    "period_start_utc": record["from"],
                    "forecast": intensity.get("forecast"),
                    "actual": intensity.get("actual"),
                    "intensity_index": intensity.get("index"),
                }
            )
    df = pd.DataFrame(rows)
    df["period_start_utc"] = pd.to_datetime(df["period_start_utc"], utc=True, format="ISO8601")
    return df.drop_duplicates(subset="period_start_utc", keep="first")


def test_row_count_reconciles_with_the_raw_files(fact, raw_payloads):
    """DQ-21 | R15 | Every warehouse period traces back to a saved raw record."""
    raw = _raw_frame(raw_payloads)
    in_window = raw[
        (raw["period_start_utc"] >= fact["period_start_utc"].min())
        & (raw["period_start_utc"] <= fact["period_start_utc"].max())
    ]
    assert len(fact) == len(in_window), (
        f"warehouse holds {len(fact)} periods but the raw files hold "
        f"{len(in_window)} across the same range"
    )


def test_no_warehouse_period_is_absent_from_the_raw_files(fact, raw_payloads):
    """DQ-22 | R15 | Nothing was invented by the transform."""
    raw_keys = set(_raw_frame(raw_payloads)["period_start_utc"])
    invented = set(fact["period_start_utc"]) - raw_keys
    assert not invented, (
        f"{len(invented)} warehouse period(s) have no matching raw record: "
        f"{sorted(str(x) for x in invented)[:3]}"
    )


def test_average_actual_recomputes_from_the_raw_json(fact, raw_payloads):
    """DQ-23 | R16 | The headline metric survives a round trip to the source.

    This is the test that would catch a silent unit change, a zero-fill, or rows
    dropped by the load.
    """
    raw = _raw_frame(raw_payloads)
    merged = fact[["period_start_utc"]].merge(raw, on="period_start_utc", how="inner")
    from_raw = pd.to_numeric(merged["actual"], errors="coerce").mean()
    from_db = pd.to_numeric(fact["actual"], errors="coerce").mean()
    assert abs(from_db - from_raw) <= TOLERANCE, (
        f"average actual is {from_db:.4f} in the warehouse but {from_raw:.4f} "
        f"recomputed from the raw JSON (tolerance {TOLERANCE})"
    )


def test_values_match_the_raw_record_row_by_row(fact, raw_payloads):
    """DQ-24 | R16 | Not just the average -- every forecast and actual matches."""
    raw = _raw_frame(raw_payloads)
    merged = fact[["period_start_utc", "forecast", "actual"]].merge(
        raw[["period_start_utc", "forecast", "actual"]],
        on="period_start_utc", how="inner", suffixes=("_db", "_raw"),
    )
    f_drift = (merged["forecast_db"] - merged["forecast_raw"]).abs() > TOLERANCE
    a_drift = (merged["actual_db"] - merged["actual_raw"]).abs() > TOLERANCE
    bad = merged[f_drift.fillna(False) | a_drift.fillna(False)]
    assert bad.empty, (
        f"{len(bad)} row(s) differ from the raw source, first at "
        f"{bad['period_start_utc'].iloc[0] if len(bad) else ''}"
    )


def test_index_bands_match_the_raw_record(fact, raw_payloads):
    """DQ-25 | R16 | Index normalisation lower-cased, it did not relabel."""
    raw = _raw_frame(raw_payloads)
    raw["intensity_index"] = raw["intensity_index"].str.strip().str.lower()
    merged = fact[["period_start_utc", "intensity_index"]].merge(
        raw[["period_start_utc", "intensity_index"]],
        on="period_start_utc", how="inner", suffixes=("_db", "_raw"),
    )
    bad = merged[merged["intensity_index_db"] != merged["intensity_index_raw"]]
    assert bad.empty, f"{len(bad)} row(s) carry a different index band from the source"


def test_loading_twice_does_not_change_the_row_count(fact, raw_payloads, tmp_path):
    """DQ-26 | R17 | Idempotency: the upsert refreshes, it does not append.

    Built in a throwaway database so the real warehouse is never written to by
    the test suite.
    """
    payloads = raw_payloads
    built = transform_mod.transform(payloads)
    dim_date = transform_mod.build_dim_date(built)
    dim_time = transform_mod.build_dim_time()

    conn = load_mod.connect(tmp_path / "idempotency.db")
    try:
        load_mod.create_schema(conn)
        first = load_mod.load(built, dim_date, dim_time, conn)
        second = load_mod.load(built, dim_date, dim_time, conn)
        assert second.fact_rows_total == first.fact_rows_total, (
            f"row count moved from {first.fact_rows_total} to {second.fact_rows_total} "
            "on a second load"
        )
        assert second.inserted == 0, f"{second.inserted} row(s) inserted on the second load"
    finally:
        conn.close()


def test_a_late_arriving_actual_replaces_an_existing_null(raw_payloads, tmp_path):
    """DQ-27 | R18 | A null actual is corrected in place when it settles.

    This is the test behind the DEF-001 fix. Without it, the backfill could
    silently insert a duplicate row instead of updating the existing one.
    """
    built = transform_mod.transform(raw_payloads)
    dim_date = transform_mod.build_dim_date(built)
    dim_time = transform_mod.build_dim_time()

    unsettled = built.copy()
    # Pick the latest period that *does* have a settled actual. Taking the last
    # row outright would pick one of the genuinely unsettled tail periods from
    # DEF-001, and the test would be asserting against a null in the source too.
    settled = built[built["has_actual"]]
    if settled.empty:
        pytest.skip("no settled actuals in this target to simulate a backfill with")
    target_key = settled["period_start_utc"].iloc[-1]
    mask = unsettled["period_start_utc"] == target_key
    unsettled.loc[mask, ["actual", "forecast_error", "abs_forecast_error"]] = None
    unsettled.loc[mask, "has_actual"] = False

    conn = load_mod.connect(tmp_path / "backfill.db")
    try:
        load_mod.create_schema(conn)
        load_mod.load(unsettled, dim_date, dim_time, conn)
        key = target_key.strftime("%Y-%m-%dT%H:%M:%SZ")
        before = conn.execute(
            "SELECT actual, has_actual FROM fact_intensity WHERE period_start_utc = ?", (key,)
        ).fetchone()
        assert before == (None, 0), f"setup failed, row reads {before}"

        rows_before = conn.execute("SELECT COUNT(*) FROM fact_intensity").fetchone()[0]
        load_mod.load(built, dim_date, dim_time, conn)  # the backfill re-fetch

        after = conn.execute(
            "SELECT actual, has_actual FROM fact_intensity WHERE period_start_utc = ?", (key,)
        ).fetchone()
        rows_after = conn.execute("SELECT COUNT(*) FROM fact_intensity").fetchone()[0]

        assert after[1] == 1 and after[0] is not None, (
            f"the null actual at {key} was not filled by the re-load; reads {after}"
        )
        assert rows_after == rows_before, (
            "the backfill inserted a new row instead of updating the existing one"
        )
    finally:
        conn.close()
