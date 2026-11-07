import sqlite3

import pandas as pd
import pytest

import load as load_mod
import transform
import validate


@pytest.fixture
def conn(tmp_path):
    connection = load_mod.connect(tmp_path / "test.db")
    load_mod.create_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def star(sample_df):
    return sample_df, transform.build_dim_date(sample_df), transform.build_dim_time()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_schema_creates_the_star_and_the_audit_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"dim_date", "dim_time", "fact_intensity", "data_quality_results", "pipeline_runs"} <= tables


def test_load_writes_every_period(conn, star):
    fact, dim_date, dim_time = star
    result = load_mod.load(fact, dim_date, dim_time, conn)
    assert result.fact_rows_written == len(fact)
    assert result.fact_rows_total == len(fact)
    assert result.inserted == len(fact)
    assert count(conn, "dim_time") == 48
    assert count(conn, "dim_date") == len(dim_date)


def test_loading_twice_gives_the_same_row_count(conn, star):
    fact, dim_date, dim_time = star
    first = load_mod.load(fact, dim_date, dim_time, conn)
    second = load_mod.load(fact, dim_date, dim_time, conn)

    assert second.fact_rows_total == first.fact_rows_total == len(fact)
    assert second.inserted == 0            # nothing new on the second pass
    assert second.updated == len(fact)     # every row refreshed in place
    assert count(conn, "dim_date") == len(dim_date)
    assert count(conn, "dim_time") == 48


def test_upsert_refreshes_a_late_arriving_actual(conn, star):
    fact, dim_date, dim_time = star
    load_mod.load(fact, dim_date, dim_time, conn)

    key = fact.loc[~fact["has_actual"], "period_start_utc"].iloc[0]
    stored = conn.execute(
        "SELECT actual, has_actual FROM fact_intensity WHERE period_start_utc = ?",
        (key.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).fetchone()
    assert stored == (None, 0)

    # the same period, now settled, as a later run would see it
    settled = fact.copy()
    mask = settled["period_start_utc"] == key
    settled.loc[mask, "actual"] = 123.0
    settled.loc[mask, "has_actual"] = True
    settled.loc[mask, "forecast_error"] = 123.0 - settled.loc[mask, "forecast"]
    settled.loc[mask, "abs_forecast_error"] = settled.loc[mask, "forecast_error"].abs()

    load_mod.load(settled, dim_date, dim_time, conn)

    refreshed = conn.execute(
        "SELECT actual, has_actual FROM fact_intensity WHERE period_start_utc = ?",
        (key.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).fetchone()
    assert refreshed == (123.0, 1)
    assert count(conn, "fact_intensity") == len(fact)   # still no duplicate


def test_fact_keys_resolve_against_both_dimensions(conn, star):
    fact, dim_date, dim_time = star
    load_mod.load(fact, dim_date, dim_time, conn)
    orphans = conn.execute(
        "SELECT COUNT(*) FROM fact_intensity f "
        "LEFT JOIN dim_date d ON d.date_key = f.date_key "
        "LEFT JOIN dim_time t ON t.time_key = f.time_key "
        "WHERE d.date_key IS NULL OR t.time_key IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_missing_actual_is_stored_as_null_not_zero(conn, star):
    fact, dim_date, dim_time = star
    load_mod.load(fact, dim_date, dim_time, conn)
    nulls = conn.execute("SELECT COUNT(*) FROM fact_intensity WHERE actual IS NULL").fetchone()[0]
    assert nulls == int((~fact["has_actual"]).sum()) == 1
    zeros = conn.execute("SELECT COUNT(*) FROM fact_intensity WHERE actual = 0").fetchone()[0]
    assert zeros == 0


def test_views_build_and_return_rows(conn, star):
    fact, dim_date, dim_time = star
    load_mod.load(fact, dim_date, dim_time, conn)
    load_mod.create_views(conn)

    for view in ("v_intensity_enriched", "v_intensity_by_half_hour", "v_daily_summary", "v_index_mix"):
        assert conn.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0] > 0

    sql_avg = conn.execute("SELECT AVG(actual) FROM fact_intensity WHERE actual IS NOT NULL").fetchone()[0]
    assert sql_avg == pytest.approx(fact["actual"].mean())


def test_quality_results_are_recorded_and_rerunnable(conn, sample_df):
    results = validate.validate(sample_df, strict=False)
    load_mod.record_quality_results(conn, "run1", "2026-11-05T00:00:00Z", results)
    load_mod.record_quality_results(conn, "run1", "2026-11-05T00:00:00Z", results)
    assert count(conn, "data_quality_results") == len(results)
