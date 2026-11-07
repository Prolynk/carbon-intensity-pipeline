"""Extract half-hourly carbon intensity data from the Carbon Intensity API.

Source: https://api.carbonintensity.org.uk (National Energy System Operator, CC BY 4.0)

The /intensity/{from}/{to} endpoint caps a single request at 14 days, so a longer
window is split into chunks. Every raw response is written to data/raw/ so the
pipeline can be re-run without touching the API again.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.carbonintensity.org.uk"
MAX_DAYS_PER_REQUEST = 14  # documented API limit for /intensity/{from}/{to}
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 1.5  # seconds; doubled on each retry
RETRY_STATUS = {429, 500, 502, 503, 504}

USER_AGENT = "carbon-intensity-pipeline/1.0 (portfolio project; contact via GitHub)"


class ExtractError(RuntimeError):
    """Raised when the API cannot be read after exhausting retries."""


@dataclass
class ExtractResult:
    """Everything the run summary needs to know about the extract step."""

    payloads: list[dict[str, Any]] = field(default_factory=list)
    raw_files: list[Path] = field(default_factory=list)
    chunks: int = 0
    requests_made: int = 0
    retries: int = 0
    cache_hits: int = 0
    backfilled_chunks: int = 0
    records: int = 0

    @property
    def days_requested(self) -> float:
        return self._days

    _days: float = 0.0


def iso_z(moment: datetime) -> str:
    """Format a datetime as the ISO8601 'YYYY-MM-DDThh:mmZ' the API expects."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def chunk_ranges(
    start: datetime, end: datetime, max_days: int = MAX_DAYS_PER_REQUEST
) -> Iterator[tuple[datetime, datetime]]:
    """Split [start, end) into windows no longer than ``max_days``.

    Chunks are contiguous, not overlapping. The API still returns the settlement
    period that *contains* ``from``, so neighbouring chunks share one period at the
    seam; transform.py de-duplicates on period start.
    """
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")
    if max_days < 1:
        raise ValueError("max_days must be at least 1")

    span = timedelta(days=max_days)
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + span, end)
        yield cursor, chunk_end
        cursor = chunk_end


def _raw_path(raw_dir: Path, start: datetime, end: datetime) -> Path:
    name = f"intensity_{start:%Y%m%dT%H%M}_{end:%Y%m%dT%H%M}.json"
    return raw_dir / name


def fetch_range(
    start: datetime,
    end: datetime,
    session: requests.Session | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep=time.sleep,
) -> tuple[dict[str, Any], int]:
    """Fetch one chunk. Returns (payload, retries_used).

    Retries on timeouts, connection errors and retryable status codes with an
    exponential backoff. A 4xx that is not 429 is not retried -- that is a bug in
    the request, not a flaky network, so it should fail immediately and loudly.
    """
    owns_session = session is None
    session = session or requests.Session()
    if owns_session:
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    url = f"{BASE_URL}/intensity/{iso_z(start)}/{iso_z(end)}"
    attempts_used = 0
    delay = backoff
    last_error: Exception | None = None

    for attempt in range(1, retries + 2):  # first try + ``retries`` retries
        try:
            LOGGER.info("GET %s (attempt %d/%d)", url, attempt, retries + 1)
            response = session.get(url, timeout=timeout)

            if response.status_code in RETRY_STATUS:
                raise requests.HTTPError(
                    f"retryable status {response.status_code}", response=response
                )
            response.raise_for_status()

            payload = response.json()
            if "data" not in payload:
                raise ExtractError(f"unexpected response shape from {url}: {list(payload)[:5]}")

            LOGGER.info("OK %s -> %d periods", url, len(payload["data"]))
            return payload, attempts_used

        except (requests.Timeout, requests.ConnectionError, requests.HTTPError, ValueError) as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status not in RETRY_STATUS:
                raise ExtractError(f"non-retryable HTTP {status} from {url}") from exc
            if attempt > retries:
                break
            attempts_used += 1
            LOGGER.warning(
                "request failed (%s); retry %d in %.1fs", exc, attempts_used, delay
            )
            sleep(delay)
            delay *= 2

    raise ExtractError(f"giving up on {url} after {retries} retries: {last_error}") from last_error


def extract(
    start: datetime,
    end: datetime,
    raw_dir: Path | str = "data/raw",
    use_cache: bool = True,
    max_days: int = MAX_DAYS_PER_REQUEST,
    session: requests.Session | None = None,
    backfill_hours: int = 0,
    **fetch_kwargs,
) -> ExtractResult:
    """Fetch [start, end) in chunks, caching each raw response to disk.

    ``backfill_hours`` ignores the cache for any chunk overlapping the last N
    hours of the window. The source publishes a period's forecast immediately but
    its settled actual some hours later, so a cached chunk from the tail of a
    previous run holds nulls that have since been filled in. Re-fetching that
    chunk and letting the upsert correct the rows is the fix for DEF-001.
    """
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    backfill_from = end - timedelta(hours=backfill_hours) if backfill_hours else None

    owns_session = session is None
    session = session or requests.Session()
    if owns_session:
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    result = ExtractResult()
    result._days = (end - start).total_seconds() / 86400

    for chunk_start, chunk_end in chunk_ranges(start, end, max_days):
        result.chunks += 1
        path = _raw_path(raw_dir, chunk_start, chunk_end)

        in_backfill = backfill_from is not None and chunk_end > backfill_from
        if in_backfill and path.exists():
            LOGGER.info(
                "chunk %s is inside the %dh backfill window, re-fetching to pick up "
                "actuals settled since the last run",
                path.name, backfill_hours,
            )
            result.backfilled_chunks += 1

        if use_cache and path.exists() and not in_backfill:
            LOGGER.info("cache hit, reading %s", path.name)
            payload = json.loads(path.read_text(encoding="utf-8"))
            result.cache_hits += 1
        else:
            payload, retries_used = fetch_range(chunk_start, chunk_end, session=session, **fetch_kwargs)
            result.requests_made += 1
            result.retries += retries_used
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            LOGGER.info("saved raw response to %s", path.name)

        result.payloads.append(payload)
        result.raw_files.append(path)
        result.records += len(payload.get("data", []))

    LOGGER.info(
        "extract complete: %d chunks, %d API requests (%d backfilled), %d cache hits, "
        "%d retries, %d raw records",
        result.chunks,
        result.requests_made,
        result.backfilled_chunks,
        result.cache_hits,
        result.retries,
        result.records,
    )
    return result


def load_raw_files(raw_dir: Path | str = "data/raw") -> list[dict[str, Any]]:
    """Read every saved raw response back off disk (offline re-run)."""
    raw_dir = Path(raw_dir)
    files = sorted(raw_dir.glob("intensity_*.json"))
    if not files:
        raise ExtractError(f"no raw responses found in {raw_dir}")
    LOGGER.info("replaying %d raw files from %s", len(files), raw_dir)
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]
