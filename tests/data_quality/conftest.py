"""Fixtures for the data-quality suite.

These tests run against a *loaded database*, not against a DataFrame in memory.
That is the point: they check the thing the dashboard actually reads, so they
would catch a defect introduced by the load step itself, not only one that came
in from the API.

The same test bodies run against three databases:

  --target=warehouse  the real loaded database (default)
  --target=clean      built from tests/fixtures/sample_clean.json
  --target=defects    built from tests/fixtures/sample_with_defects.json

The defects target is how the suite proves it can fail. Running it is expected
to produce failures; `scripts/verify_defect_detection.py` runs it and checks
that every planted defect was caught.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import load as load_mod  # noqa: E402
import transform as transform_mod  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
WAREHOUSE = ROOT / "data" / "carbon_intensity.db"

# Missing actuals are expected only inside this window back from the last period.
# Settlement lag on the source has been observed at roughly 1-3 hours.
RECENT_WINDOW_HOURS = 24

PLAUSIBLE_MIN = 0
PLAUSIBLE_MAX = 1000
VALID_INDEX = {"very low", "low", "moderate", "high", "very high"}


def pytest_addoption(parser):
    parser.addoption(
        "--target",
        action="store",
        default="warehouse",
        choices=["warehouse", "clean", "defects"],
        help="which database the data-quality tests run against",
    )


def _build_db(payload_path: Path, db_path: Path) -> None:
    """Transform and load a fixture *without* validating it.

    validate.py would reject the defect fixture before it ever reached the
    database, which would prove nothing about these tests. Bypassing it here is
    deliberate and is the only place in the project that does so.
    """
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    fact = transform_mod.transform([payload])
    dim_date = transform_mod.build_dim_date(fact)
    dim_time = transform_mod.build_dim_time()
    conn = load_mod.connect(db_path)
    try:
        load_mod.create_schema(conn)
        load_mod.load(fact, dim_date, dim_time, conn)
        load_mod.create_views(conn)
    finally:
        conn.close()


@pytest.fixture(scope="session")
def target(request) -> str:
    return request.config.getoption("--target")


@pytest.fixture(scope="session")
def db_path(target, tmp_path_factory) -> Path:
    if target == "warehouse":
        if not WAREHOUSE.exists():
            pytest.skip(
                f"{WAREHOUSE.relative_to(ROOT)} not found -- run src/run_pipeline.py first"
            )
        return WAREHOUSE
    src = FIXTURES / ("sample_clean.json" if target == "clean" else "sample_with_defects.json")
    out = tmp_path_factory.mktemp("dq") / f"{target}.db"
    _build_db(src, out)
    return out


@pytest.fixture(scope="session")
def conn(db_path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def fact(conn) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM fact_intensity", conn)
    df["period_start_utc"] = pd.to_datetime(df["period_start_utc"], utc=True, format="ISO8601")
    df["period_end_utc"] = pd.to_datetime(df["period_end_utc"], utc=True, format="ISO8601")
    return df.sort_values("period_start_utc").reset_index(drop=True)


@pytest.fixture(scope="session")
def raw_payloads(target) -> list[dict]:
    """The saved API responses the warehouse was built from, for reconciliation."""
    if target == "warehouse":
        files = sorted((ROOT / "data" / "raw").glob("intensity_*.json"))
        if not files:
            pytest.skip("no raw responses in data/raw to reconcile against")
        return [json.loads(f.read_text(encoding="utf-8")) for f in files]
    name = "sample_clean.json" if target == "clean" else "sample_with_defects.json"
    return [json.loads((FIXTURES / name).read_text(encoding="utf-8"))]


@pytest.fixture(scope="session")
def expected_days(fact) -> int:
    """Whole days the window should contain, from the loaded period range."""
    span = fact["period_start_utc"].max() - fact["period_start_utc"].min()
    return int(round((span.total_seconds() / 1800 + 1) / 48))
