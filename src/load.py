"""Load the star schema into SQLite with an upsert.

Every write is an INSERT ... ON CONFLICT DO UPDATE keyed on the natural key, so
re-running the pipeline over an overlapping window refreshes rows instead of
duplicating them. That matters here because the most recent periods arrive
without an actual and are filled in later -- a second run has to correct them.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

LOGGER = logging.getLogger(__name__)

DEFAULT_DB = "data/carbon_intensity.db"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
VIEWS_PATH = Path(__file__).resolve().parents[1] / "sql" / "views.sql"


@dataclass
class LoadResult:
    dim_date_rows: int = 0
    dim_time_rows: int = 0
    fact_rows_written: int = 0
    fact_rows_total: int = 0
    inserted: int = 0
    updated: int = 0


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection, schema_path: Path | str = SCHEMA_PATH) -> None:
    conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
    conn.commit()
    LOGGER.info("schema applied from %s", Path(schema_path).name)


def create_views(conn: sqlite3.Connection, views_path: Path | str = VIEWS_PATH) -> None:
    conn.executescript(Path(views_path).read_text(encoding="utf-8"))
    conn.commit()
    LOGGER.info("views applied from %s", Path(views_path).name)


def _upsert(conn: sqlite3.Connection, table: str, key: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = list(df.columns)
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != key)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key}) DO UPDATE SET {updates}"
    )
    rows = [tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False)]
    conn.executemany(sql, rows)
    return len(rows)


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def load(
    fact: pd.DataFrame,
    dim_date: pd.DataFrame,
    dim_time: pd.DataFrame,
    conn: sqlite3.Connection,
) -> LoadResult:
    """Upsert the three tables. Idempotent: load twice, same row count."""
    before = _count(conn, "fact_intensity") if _table_exists(conn, "fact_intensity") else 0

    result = LoadResult()
    result.dim_date_rows = _upsert(conn, "dim_date", "date_key", _prep_dim_date(dim_date))
    result.dim_time_rows = _upsert(conn, "dim_time", "time_key", dim_time)
    result.fact_rows_written = _upsert(conn, "fact_intensity", "period_start_utc", _prep_fact(fact))
    conn.commit()

    result.fact_rows_total = _count(conn, "fact_intensity")
    result.inserted = result.fact_rows_total - before
    result.updated = result.fact_rows_written - result.inserted

    LOGGER.info(
        "load complete: %d fact rows written (%d new, %d refreshed), %d total in warehouse; "
        "dim_date %d rows, dim_time %d rows",
        result.fact_rows_written,
        result.inserted,
        result.updated,
        result.fact_rows_total,
        result.dim_date_rows,
        result.dim_time_rows,
    )
    return result


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _prep_dim_date(dim_date: pd.DataFrame) -> pd.DataFrame:
    out = dim_date.copy()
    out["is_weekend"] = out["is_weekend"].astype(int)
    return out


def _prep_fact(fact: pd.DataFrame) -> pd.DataFrame:
    out = fact.copy()
    out["period_start_utc"] = out["period_start_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out["period_end_utc"] = out["period_end_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out["period_start_local"] = out["period_start_local"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
    out["has_actual"] = out["has_actual"].astype(int)
    out["loaded_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out[
        [
            "period_start_utc",
            "period_end_utc",
            "period_start_local",
            "date_key",
            "time_key",
            "forecast",
            "actual",
            "intensity_index",
            "forecast_error",
            "abs_forecast_error",
            "has_actual",
            "loaded_at_utc",
        ]
    ]


def record_quality_results(
    conn: sqlite3.Connection, run_id: str, run_at: str, results: list[Any]
) -> int:
    rows = [
        (run_id, run_at, r.name, int(r.passed), r.severity, r.count, r.detail)
        for r in results
    ]
    conn.executemany(
        "INSERT INTO data_quality_results "
        "(run_id, run_at_utc, check_name, passed, severity, issue_count, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id, check_name) DO UPDATE SET "
        "passed = excluded.passed, issue_count = excluded.issue_count, detail = excluded.detail",
        rows,
    )
    conn.commit()
    return len(rows)


def record_run(conn: sqlite3.Connection, summary: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, run_at_utc, window_start_utc, window_end_utc, "
        "days_requested, api_requests, cache_hits, retries, periods_loaded, checks_run, "
        "checks_failed, missing_actuals, status) "
        "VALUES (:run_id, :run_at_utc, :window_start_utc, :window_end_utc, :days_requested, "
        ":api_requests, :cache_hits, :retries, :periods_loaded, :checks_run, :checks_failed, "
        ":missing_actuals, :status) "
        "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status",
        summary,
    )
    conn.commit()
