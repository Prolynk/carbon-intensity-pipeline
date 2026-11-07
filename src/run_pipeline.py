"""Run the whole pipeline: extract -> transform -> validate -> load.

    python src/run_pipeline.py --start 2026-08-07 --end 2026-11-05

Exits 0 on success, 1 on any failure, so it can sit behind cron or a scheduler.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import extract as extract_mod  # noqa: E402
import load as load_mod  # noqa: E402
import transform as transform_mod  # noqa: E402
import validate as validate_mod  # noqa: E402

LOGGER = logging.getLogger("pipeline")


def setup_logging(log_file: Path, verbose: bool = False) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)-10s | %(message)s"
    handlers = [logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        handlers=handlers,
        force=True,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GB carbon intensity API-to-warehouse pipeline")
    p.add_argument("--start", type=parse_day, help="start date, YYYY-MM-DD (UTC, inclusive)")
    p.add_argument("--end", type=parse_day, help="end date, YYYY-MM-DD (UTC, exclusive)")
    p.add_argument("--days", type=int, default=90, help="days back from --end if --start omitted")
    p.add_argument("--db", default=str(ROOT / "data" / "carbon_intensity.db"))
    p.add_argument("--raw-dir", default=str(ROOT / "data" / "raw"))
    p.add_argument("--log-file", default=str(ROOT / "logs" / "pipeline.log"))
    p.add_argument("--summary-file", default=str(ROOT / "docs" / "run_summary.json"))
    p.add_argument("--no-cache", action="store_true", help="ignore saved raw responses")
    p.add_argument("--offline", action="store_true", help="replay data/raw only, no API calls")
    p.add_argument("--backfill-hours", type=int, default=48,
                   help="ignore the cache for chunks inside the last N hours, so actuals "
                        "settled since the previous run are picked up (0 disables)")
    p.add_argument("--no-strict", action="store_true", help="log check failures instead of stopping")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(Path(args.log_file), args.verbose)

    end = args.end or datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = args.start or (end - timedelta(days=args.days))

    run_id = uuid.uuid4().hex[:12]
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    LOGGER.info("=" * 78)
    LOGGER.info("run %s | window %s -> %s (%d days)", run_id, start.date(), end.date(), (end - start).days)

    summary: dict[str, object] = {
        "run_id": run_id,
        "run_at_utc": run_at,
        "window_start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days_requested": (end - start).total_seconds() / 86400,
        "api_requests": 0,
        "cache_hits": 0,
        "retries": 0,
        "periods_loaded": 0,
        "checks_run": 0,
        "checks_failed": 0,
        "missing_actuals": 0,
        "status": "failed",
    }

    try:
        # 1. Extract
        if args.offline:
            payloads = extract_mod.load_raw_files(args.raw_dir)
            summary["cache_hits"] = len(payloads)
        else:
            ex = extract_mod.extract(
                start, end, raw_dir=args.raw_dir, use_cache=not args.no_cache,
                backfill_hours=args.backfill_hours,
            )
            payloads = ex.payloads
            summary["api_requests"] = ex.requests_made
            summary["cache_hits"] = ex.cache_hits
            summary["retries"] = ex.retries
            summary["chunks"] = ex.chunks
            summary["backfilled_chunks"] = ex.backfilled_chunks
            summary["raw_records"] = ex.records

        # 2. Transform
        fact = transform_mod.transform(payloads)
        fact = transform_mod.clip_to_window(fact, start, end)
        dim_date = transform_mod.build_dim_date(fact)
        dim_time = transform_mod.build_dim_time()

        # 3. Validate
        results = validate_mod.validate(fact, strict=not args.no_strict)
        summary["checks_run"] = len(results)
        summary["checks_failed"] = sum(
            1 for r in results if not r.passed and r.severity == "error"
        )
        summary["checks_warned"] = sum(
            1 for r in results if not r.passed and r.severity == "warning"
        )
        summary["checks_passed"] = sum(1 for r in results if r.passed)
        summary["checks"] = [r.as_dict() for r in results]
        summary["missing_actuals"] = int((~fact["has_actual"]).sum())

        # 4. Load
        conn = load_mod.connect(args.db)
        try:
            load_mod.create_schema(conn)
            lr = load_mod.load(fact, dim_date, dim_time, conn)
            load_mod.create_views(conn)
            load_mod.record_quality_results(conn, run_id, run_at, results)
            summary["periods_loaded"] = lr.fact_rows_written
            summary["periods_in_warehouse"] = lr.fact_rows_total
            summary["rows_inserted"] = lr.inserted
            summary["rows_refreshed"] = lr.updated
            summary["dim_date_rows"] = lr.dim_date_rows
            summary["dim_time_rows"] = lr.dim_time_rows
            summary["days_loaded"] = int(dim_date.shape[0])
            summary["status"] = "success"
            load_mod.record_run(conn, {k: summary[k] for k in (
                "run_id", "run_at_utc", "window_start_utc", "window_end_utc", "days_requested",
                "api_requests", "cache_hits", "retries", "periods_loaded", "checks_run",
                "checks_failed", "missing_actuals", "status")})
        finally:
            conn.close()

    except Exception as exc:  # noqa: BLE001 - top-level guard, logged then re-raised as exit code
        LOGGER.exception("pipeline failed: %s", exc)
        summary["error"] = f"{type(exc).__name__}: {exc}"
        _write_summary(Path(args.summary_file), summary)
        return 1

    _write_summary(Path(args.summary_file), summary)
    LOGGER.info("-" * 78)
    LOGGER.info(
        "SUCCESS | %d days, %d half-hourly periods | %d API requests, %d retries | "
        "%d checks: %d passed, %d failed, %d warning(s) | %d missing actuals",
        summary["days_loaded"],
        summary["periods_loaded"],
        summary["api_requests"],
        summary["retries"],
        summary["checks_run"],
        summary["checks_passed"],
        summary["checks_failed"],
        summary["checks_warned"],
        summary["missing_actuals"],
    )
    return 0


def _write_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    LOGGER.info("run summary written to %s", path)


if __name__ == "__main__":
    raise SystemExit(main())
