import pandas as pd
import pytest

import validate


def result_named(results, name):
    return next(r for r in results if r.name == name)


def test_clean_sample_passes_every_hard_check(sample_df):
    results = validate.validate(sample_df, strict=True)
    assert len(results) == len(validate.CHECKS)
    assert all(r.passed for r in results if r.severity == "error")


def test_planted_duplicate_is_caught(sample_df):
    planted = pd.concat([sample_df, sample_df.iloc[[3]]], ignore_index=True)
    check = validate.check_no_duplicate_periods(planted)
    assert not check.passed
    assert check.count == 1
    with pytest.raises(validate.ValidationError):
        validate.validate(planted, strict=True)


def test_planted_gap_is_caught(sample_df):
    planted = sample_df.drop(index=5).reset_index(drop=True)
    check = validate.check_no_gaps(planted)
    assert not check.passed
    assert check.count == 1
    assert "missing" in check.detail
    with pytest.raises(validate.ValidationError):
        validate.validate(planted, strict=True)


def test_out_of_range_actual_is_caught(sample_df):
    planted = sample_df.copy()
    planted.loc[2, "actual"] = 99999
    check = validate.check_actual_in_range(planted)
    assert not check.passed
    assert check.count == 1


def test_non_numeric_forecast_is_caught(sample_df):
    planted = sample_df.copy()
    planted.loc[1, "forecast"] = None
    check = validate.check_forecast_numeric_and_in_range(planted)
    assert not check.passed


def test_unknown_index_value_is_caught(sample_df):
    planted = sample_df.copy()
    planted.loc[0, "intensity_index"] = "extremely spicy"
    check = validate.check_index_values(planted)
    assert not check.passed
    assert "extremely spicy" in check.detail


def test_wrong_period_length_is_caught(sample_df):
    planted = sample_df.copy()
    planted.loc[4, "period_end_utc"] = planted.loc[4, "period_end_utc"] + pd.Timedelta(minutes=15)
    assert not validate.check_period_length(planted).passed


def test_missing_actuals_warn_but_do_not_stop_the_run(sample_df):
    check = validate.check_missing_actuals(sample_df)
    assert not check.passed          # the sample has one unsettled period
    assert check.severity == "warning"
    assert check.count == 1
    validate.validate(sample_df, strict=True)  # must not raise


def test_empty_table_fails_loudly():
    empty = pd.DataFrame(
        {
            "period_start_utc": pd.Series(dtype="datetime64[ns, UTC]"),
            "period_end_utc": pd.Series(dtype="datetime64[ns, UTC]"),
            "forecast": pd.Series(dtype="float"),
            "actual": pd.Series(dtype="float"),
            "intensity_index": pd.Series(dtype="string"),
            "has_actual": pd.Series(dtype="bool"),
        }
    )
    assert not validate.check_not_empty(empty).passed


def test_non_strict_mode_reports_instead_of_raising(sample_df):
    planted = pd.concat([sample_df, sample_df.iloc[[3]]], ignore_index=True)
    results = validate.validate(planted, strict=False)
    assert not result_named(results, "no_duplicate_periods").passed


def test_results_frame_is_loadable(sample_df):
    frame = validate.results_frame(validate.validate(sample_df, strict=False))
    assert list(frame.columns) == ["check_name", "passed", "severity", "issue_count", "detail"]
    assert len(frame) == len(validate.CHECKS)
