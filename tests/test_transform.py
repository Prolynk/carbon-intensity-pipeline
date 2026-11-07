import pandas as pd
import pytest

import transform


def test_transform_produces_expected_columns(sample_df):
    assert list(sample_df.columns) == transform.COLUMNS


def test_transform_row_count_matches_payload(sample_payload, sample_df):
    assert len(sample_df) == len(sample_payload["data"])


def test_times_are_utc_and_periods_are_half_hourly(sample_df):
    assert str(sample_df["period_start_utc"].dt.tz) == "UTC"
    spans = sample_df["period_end_utc"] - sample_df["period_start_utc"]
    assert (spans == pd.Timedelta(minutes=30)).all()


def test_local_column_is_london_and_keys_derive_from_it(sample_df):
    local = sample_df["period_start_local"]
    assert "London" in str(local.dt.tz)
    first = local.iloc[0]
    expected_key = first.hour * 2 + (first.minute // 30) + 1
    assert sample_df["time_key"].iloc[0] == expected_key
    assert sample_df["time_key"].between(1, 48).all()
    assert sample_df["date_key"].iloc[0] == int(first.strftime("%Y%m%d"))


def test_duplicate_periods_from_overlapping_chunks_are_dropped(sample_payload):
    # the same payload twice is the worst case of a chunk seam
    df = transform.transform([sample_payload, sample_payload])
    assert len(df) == len(sample_payload["data"])
    assert not df["period_start_utc"].duplicated().any()


def test_missing_actual_is_preserved_not_filled(sample_df):
    assert sample_df["actual"].isna().sum() == 1
    assert (~sample_df["has_actual"]).sum() == 1
    # a missing actual must not produce a fake error of zero
    assert sample_df.loc[~sample_df["has_actual"], "forecast_error"].isna().all()


def test_forecast_error_is_actual_minus_forecast(sample_df):
    row = sample_df[sample_df["has_actual"]].iloc[0]
    assert row["forecast_error"] == pytest.approx(row["actual"] - row["forecast"])
    assert row["abs_forecast_error"] == pytest.approx(abs(row["forecast_error"]))


def test_rows_are_sorted_by_period_start(sample_payload):
    shuffled = {"data": list(reversed(sample_payload["data"]))}
    df = transform.transform([shuffled])
    assert df["period_start_utc"].is_monotonic_increasing


def test_index_values_are_normalised(sample_payload):
    payload = {"data": [dict(r) for r in sample_payload["data"]]}
    payload["data"][0]["intensity"] = {"forecast": 100, "actual": 110, "index": "  LOW  "}
    df = transform.transform([payload])
    assert df["intensity_index"].iloc[0] == "low"


def test_empty_payload_raises(sample_payload):
    with pytest.raises(transform.TransformError):
        transform.transform([{"data": []}])
    with pytest.raises(transform.TransformError):
        transform.transform([{"nope": 1}])


def test_clip_to_window_drops_the_api_lead_in_period(sample_df):
    start = sample_df["period_start_utc"].iloc[1]
    end = sample_df["period_start_utc"].iloc[-1]
    clipped = transform.clip_to_window(sample_df, start, end)
    assert clipped["period_start_utc"].min() == start
    assert clipped["period_start_utc"].max() < end
    assert len(clipped) == len(sample_df) - 2


def test_dim_time_has_48_slots():
    dim = transform.build_dim_time()
    assert len(dim) == 48
    assert dim["time_key"].tolist() == list(range(1, 49))
    assert dim["time_label"].iloc[0] == "00:00"
    assert dim["time_label"].iloc[-1] == "23:30"
    assert dim["time_key"].is_unique


def test_dim_date_is_one_row_per_local_date(sample_df):
    dim = transform.build_dim_date(sample_df)
    assert len(dim) == sample_df["local_date"].nunique()
    assert dim["date_key"].is_unique
    assert dim["weekday_number"].between(1, 7).all()
