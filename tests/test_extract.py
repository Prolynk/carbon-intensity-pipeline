import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

import extract


UTC = timezone.utc


def dt(day, hour=0):
    return datetime(2026, 1, day, hour, tzinfo=UTC)


def ok_response(periods=2):
    resp = Mock(spec=requests.Response)
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "data": [
            {
                "from": f"2026-01-01T{i:02d}:00Z",
                "to": f"2026-01-01T{i:02d}:30Z",
                "intensity": {"forecast": 100 + i, "actual": 101 + i, "index": "low"},
            }
            for i in range(periods)
        ]
    }
    return resp


def error_response(status):
    resp = Mock(spec=requests.Response)
    resp.status_code = status
    resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}", response=resp)
    return resp


# --- chunking -------------------------------------------------------------

def test_chunk_ranges_respects_the_14_day_api_limit():
    chunks = list(extract.chunk_ranges(dt(1), dt(1) + timedelta(days=90)))
    assert len(chunks) == 7                       # ceil(90 / 14)
    assert all((e - s) <= timedelta(days=14) for s, e in chunks)
    assert chunks[0][0] == dt(1)
    assert chunks[-1][1] == dt(1) + timedelta(days=90)


def test_chunks_are_contiguous_with_no_holes():
    chunks = list(extract.chunk_ranges(dt(1), dt(1) + timedelta(days=30)))
    for (_, end), (next_start, _) in zip(chunks, chunks[1:]):
        assert end == next_start


def test_exact_multiple_does_not_make_an_empty_trailing_chunk():
    assert len(list(extract.chunk_ranges(dt(1), dt(1) + timedelta(days=28)))) == 2


def test_end_before_start_is_rejected():
    with pytest.raises(ValueError):
        list(extract.chunk_ranges(dt(5), dt(1)))


def test_iso_z_formats_for_the_api():
    assert extract.iso_z(datetime(2026, 8, 7, 9, 30, tzinfo=UTC)) == "2026-08-07T09:30Z"
    # a naive datetime is assumed to be UTC rather than silently localised
    assert extract.iso_z(datetime(2026, 8, 7, 9, 30)) == "2026-08-07T09:30Z"


# --- retries --------------------------------------------------------------

def test_retry_succeeds_after_two_transient_failures():
    session = Mock()
    session.get.side_effect = [requests.Timeout("timed out"), error_response(503), ok_response()]
    slept = []

    payload, retries = extract.fetch_range(
        dt(1), dt(2), session=session, retries=3, backoff=0.1, sleep=slept.append
    )

    assert session.get.call_count == 3
    assert retries == 2
    assert len(payload["data"]) == 2
    assert slept == [0.1, 0.2]            # backoff doubles


def test_retries_are_exhausted_then_it_fails_loudly():
    session = Mock()
    session.get.side_effect = requests.ConnectionError("network down")

    with pytest.raises(extract.ExtractError, match="giving up"):
        extract.fetch_range(dt(1), dt(2), session=session, retries=2, backoff=0, sleep=lambda _: None)

    assert session.get.call_count == 3     # first attempt plus two retries


def test_a_404_is_not_retried():
    session = Mock()
    session.get.return_value = error_response(404)

    with pytest.raises(extract.ExtractError, match="non-retryable"):
        extract.fetch_range(dt(1), dt(2), session=session, retries=3, backoff=0, sleep=lambda _: None)

    assert session.get.call_count == 1


def test_a_429_is_retried():
    session = Mock()
    session.get.side_effect = [error_response(429), ok_response()]
    _, retries = extract.fetch_range(
        dt(1), dt(2), session=session, retries=2, backoff=0, sleep=lambda _: None
    )
    assert retries == 1


def test_unexpected_response_shape_is_rejected():
    resp = Mock(spec=requests.Response)
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"error": {"code": "400 Bad Request"}}
    session = Mock()
    session.get.return_value = resp

    with pytest.raises(extract.ExtractError, match="unexpected response shape"):
        extract.fetch_range(dt(1), dt(2), session=session, retries=0, sleep=lambda _: None)


# --- caching --------------------------------------------------------------

def test_raw_responses_are_saved_and_reused_without_calling_the_api(tmp_path):
    session = Mock()
    session.get.return_value = ok_response()

    first = extract.extract(dt(1), dt(15), raw_dir=tmp_path, session=session, sleep=lambda _: None)
    assert first.requests_made == 1
    assert first.cache_hits == 0
    saved = list(tmp_path.glob("intensity_*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text())["data"]

    second = extract.extract(dt(1), dt(15), raw_dir=tmp_path, session=session, sleep=lambda _: None)
    assert second.requests_made == 0
    assert second.cache_hits == 1
    assert session.get.call_count == 1          # the API was not hit again


def test_no_cache_forces_a_refetch(tmp_path):
    session = Mock()
    session.get.return_value = ok_response()
    extract.extract(dt(1), dt(15), raw_dir=tmp_path, session=session, sleep=lambda _: None)
    again = extract.extract(
        dt(1), dt(15), raw_dir=tmp_path, use_cache=False, session=session, sleep=lambda _: None
    )
    assert again.requests_made == 1
    assert session.get.call_count == 2


def test_extract_counts_chunks_and_records_across_a_90_day_window(tmp_path):
    session = Mock()
    session.get.return_value = ok_response(periods=3)
    result = extract.extract(
        dt(1), dt(1) + timedelta(days=90), raw_dir=tmp_path, session=session, sleep=lambda _: None
    )
    assert result.chunks == 7
    assert result.requests_made == 7
    assert result.records == 21


def test_load_raw_files_replays_from_disk(tmp_path):
    session = Mock()
    session.get.return_value = ok_response()
    extract.extract(dt(1), dt(15), raw_dir=tmp_path, session=session, sleep=lambda _: None)

    payloads = extract.load_raw_files(tmp_path)
    assert len(payloads) == 1
    assert "data" in payloads[0]


def test_replaying_an_empty_directory_fails_loudly(tmp_path):
    with pytest.raises(extract.ExtractError, match="no raw responses"):
        extract.load_raw_files(tmp_path)
