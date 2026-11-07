"""Regenerate powerbi/dashboard_preview.html from the warehouse.

    python src/build_preview.py

The preview is a static stand-in for Power BI screenshots. It reads the same
tables and views the .pbix reads, so if a number on the page disagrees with a DAX
measure, one of the two is wrong -- which is the point of keeping it.

Also exports powerbi/model_data/*.csv, the files the .pbix imports.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "carbon_intensity.db"
TEMPLATE = ROOT / "powerbi" / "_template.html"
OUTPUT = ROOT / "powerbi" / "dashboard_preview.html"
MODEL_DATA = ROOT / "powerbi" / "model_data"
PLACEHOLDER = "/*__DATA__*/"

SCATTER_SAMPLE = 1400  # plotting 4,314 points turns the scatter into a blob


def export_model_csvs(conn: sqlite3.Connection) -> list[Path]:
    MODEL_DATA.mkdir(parents=True, exist_ok=True)
    written = []
    for table in ("dim_date", "dim_time", "fact_intensity", "data_quality_results"):
        path = MODEL_DATA / f"{table}.csv"
        pd.read_sql_query(f"SELECT * FROM {table}", conn).to_csv(path, index=False)
        written.append(path)
    return written


def collect(conn: sqlite3.Connection) -> dict:
    q = lambda sql: pd.read_sql_query(sql, conn)  # noqa: E731

    kpi = q(
        """SELECT COUNT(*) periods, ROUND(AVG(actual),1) avg_actual,
             ROUND(AVG(forecast),1) avg_forecast, ROUND(AVG(abs_forecast_error),1) mae,
             ROUND(AVG(forecast_error),1) bias,
             ROUND(100.0*SUM(CASE WHEN intensity_index IN ('low','very low') THEN 1 ELSE 0 END)
                   /COUNT(*),1) low_pct,
             ROUND(100.0*SUM(CASE WHEN intensity_index IN ('high','very high') THEN 1 ELSE 0 END)
                   /COUNT(*),1) high_pct,
             ROUND(100.0*SUM(CASE WHEN has_actual=0 THEN 1 ELSE 0 END)/COUNT(*),2) missing_pct,
             SUM(CASE WHEN has_actual=0 THEN 1 ELSE 0 END) missing_n,
             ROUND(MIN(actual),0) min_a, ROUND(MAX(actual),0) max_a
           FROM fact_intensity"""
    ).iloc[0].to_dict()

    acc = q(
        """SELECT ROUND(100.0*SUM(CASE WHEN abs_forecast_error<=20 THEN 1 ELSE 0 END)
                        /COUNT(*),1) w20,
             ROUND(100.0*SUM(CASE WHEN abs_forecast_error<=50 THEN 1 ELSE 0 END)/COUNT(*),1) w50,
             ROUND(MAX(abs_forecast_error),0) worst,
             ROUND(100.0*AVG(abs_forecast_error/actual),1) mape,
             COUNT(*) scored
           FROM fact_intensity WHERE actual IS NOT NULL AND actual > 0"""
    ).iloc[0].to_dict()

    data = {"kpi": {**kpi, **acc}}

    data["halfhour"] = q(
        "SELECT time_label, avg_actual, avg_forecast FROM v_intensity_by_half_hour ORDER BY time_key"
    ).to_dict("records")
    data["daily"] = q(
        "SELECT full_date, avg_actual, greenest_actual, dirtiest_actual "
        "FROM v_daily_summary ORDER BY full_date"
    ).to_dict("records")
    data["heat"] = q(
        "SELECT weekday_number, weekday_name, hour_of_day, avg_actual FROM v_intensity_weekday_hour"
    ).to_dict("records")
    data["daypart"] = q(
        """SELECT t.day_part, ROUND(AVG(f.actual),1) avg_actual
           FROM fact_intensity f JOIN dim_time t ON t.time_key = f.time_key
           GROUP BY t.day_part ORDER BY avg_actual"""
    ).to_dict("records")
    data["mix"] = q(
        "SELECT intensity_index, periods, pct_of_periods FROM v_index_mix"
    ).to_dict("records")
    data["mae_daily"] = q(
        "SELECT full_date, mae, bias FROM v_forecast_accuracy_daily ORDER BY full_date"
    ).to_dict("records")
    data["mae_hour"] = q(
        """SELECT t.hour_of_day, ROUND(AVG(f.abs_forecast_error),1) mae
           FROM fact_intensity f JOIN dim_time t ON t.time_key = f.time_key
           WHERE f.actual IS NOT NULL GROUP BY t.hour_of_day ORDER BY 1"""
    ).to_dict("records")
    data["scatter"] = q(
        f"SELECT forecast x, actual y FROM fact_intensity WHERE actual IS NOT NULL "
        f"ORDER BY RANDOM() LIMIT {SCATTER_SAMPLE}"
    ).to_dict("records")
    data["checks"] = q(
        """SELECT check_name, passed, severity, issue_count, detail
           FROM data_quality_results
           WHERE run_id = (SELECT run_id FROM pipeline_runs ORDER BY run_at_utc DESC LIMIT 1)"""
    ).to_dict("records")
    data["periods_per_day"] = q(
        "SELECT full_date, periods FROM v_daily_summary ORDER BY full_date"
    ).to_dict("records")
    data["weekend"] = q(
        """SELECT d.is_weekend, ROUND(AVG(f.actual),1) avg_actual
           FROM fact_intensity f JOIN dim_date d ON d.date_key = f.date_key GROUP BY 1"""
    ).to_dict("records")
    data["run"] = q(
        "SELECT * FROM pipeline_runs ORDER BY run_at_utc DESC LIMIT 1"
    ).iloc[0].to_dict()

    half = data["halfhour"]
    data["kpi"]["min_hh"] = min(r["avg_actual"] for r in half)
    data["kpi"]["max_hh"] = max(r["avg_actual"] for r in half)
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild the static dashboard preview")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(OUTPUT))
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    try:
        csvs = export_model_csvs(conn)
        data = collect(conn)
    finally:
        conn.close()

    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise SystemExit(f"{TEMPLATE.name} has no {PLACEHOLDER} placeholder")

    payload = json.dumps(data, separators=(",", ":"), default=str)
    out = Path(args.out)
    out.write_text(template.replace(PLACEHOLDER, payload), encoding="utf-8")

    print(f"exported {len(csvs)} model CSVs to {MODEL_DATA.relative_to(ROOT)}/")
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1024:.0f} KB)")
    print(
        f"  {int(data['kpi']['periods']):,} periods | "
        f"avg {data['kpi']['avg_actual']} | MAE {data['kpi']['mae']} | "
        f"greenest {data['kpi']['min_hh']} | dirtiest {data['kpi']['max_hh']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
