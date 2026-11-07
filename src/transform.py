"""Flatten the Carbon Intensity JSON into one tidy half-hourly table.

Times are kept in UTC as the grain of truth, because Europe/London has 46- and
50-period days at the clock changes. A local-time column is derived for the
"when is the grid greenest" question, since nobody plans their washing in UTC.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import pandas as pd

LOGGER = logging.getLogger(__name__)

LOCAL_TZ = "Europe/London"
INDEX_ORDER = ["very low", "low", "moderate", "high", "very high"]

COLUMNS = [
    "period_start_utc",
    "period_end_utc",
    "period_start_local",
    "local_date",
    "date_key",
    "time_key",
    "local_time_label",
    "forecast",
    "actual",
    "intensity_index",
    "forecast_error",
    "abs_forecast_error",
    "has_actual",
]


class TransformError(ValueError):
    """Raised when the payloads cannot be turned into a usable table."""


def _records(payloads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        data = payload.get("data")
        if data is None:
            raise TransformError("payload has no 'data' key")
        # /intensity/{from}/{to} returns a list; /intensity/date can nest differently.
        if isinstance(data, dict):
            data = [data]
        for record in data:
            intensity = record.get("intensity") or {}
            rows.append(
                {
                    "period_start_utc": record.get("from"),
                    "period_end_utc": record.get("to"),
                    "forecast": intensity.get("forecast"),
                    "actual": intensity.get("actual"),
                    "intensity_index": intensity.get("index"),
                }
            )
    return rows


def transform(payloads: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Turn raw API payloads into the tidy fact-level table."""
    rows = _records(payloads)
    if not rows:
        raise TransformError("no records found in payloads")

    df = pd.DataFrame(rows)

    df["period_start_utc"] = pd.to_datetime(df["period_start_utc"], utc=True, format="ISO8601")
    df["period_end_utc"] = pd.to_datetime(df["period_end_utc"], utc=True, format="ISO8601")

    before = len(df)
    df = df.drop_duplicates(subset="period_start_utc", keep="first")
    dropped = before - len(df)
    if dropped:
        LOGGER.info("dropped %d duplicate period(s) from overlapping chunk seams", dropped)

    df = df.sort_values("period_start_utc").reset_index(drop=True)

    local = df["period_start_utc"].dt.tz_convert(LOCAL_TZ)
    df["period_start_local"] = local
    df["local_date"] = local.dt.date.astype("string")
    df["date_key"] = local.dt.strftime("%Y%m%d").astype(int)
    # 1..48 slot by local clock time. Documented caveat: on clock-change days a
    # local day has 46 or 50 periods, so two slots repeat or two are absent.
    df["time_key"] = local.dt.hour * 2 + (local.dt.minute // 30) + 1
    df["local_time_label"] = local.dt.strftime("%H:%M")

    for col in ("forecast", "actual"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["intensity_index"] = (
        df["intensity_index"].astype("string").str.strip().str.lower()
    )

    df["has_actual"] = df["actual"].notna()
    df["forecast_error"] = df["actual"] - df["forecast"]
    df["abs_forecast_error"] = df["forecast_error"].abs()

    df = df[COLUMNS]

    LOGGER.info(
        "transform complete: %d periods, %s to %s, %d missing actuals",
        len(df),
        df["period_start_utc"].min(),
        df["period_start_utc"].max(),
        int((~df["has_actual"]).sum()),
    )
    return df


def clip_to_window(df: pd.DataFrame, start, end) -> pd.DataFrame:
    """Trim to [start, end) in UTC.

    The API returns the settlement period that *contains* ``from``, so a request
    for 2 days comes back with 97 periods rather than 96. Left in, that stray
    period creates a calendar date holding a single half-hour and quietly skews
    any daily average. Trimming is explicit here rather than implicit.
    """
    start = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
    end = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
    before = len(df)
    out = df[(df["period_start_utc"] >= start) & (df["period_start_utc"] < end)].reset_index(drop=True)
    if before != len(out):
        LOGGER.info("clipped %d period(s) outside the requested window", before - len(out))
    return out


def build_dim_date(df: pd.DataFrame) -> pd.DataFrame:
    """One row per local calendar date covered by the fact table."""
    dates = pd.to_datetime(df["local_date"].unique())
    dim = pd.DataFrame({"date_key": dates.strftime("%Y%m%d").astype(int), "full_date": dates})
    dim["year"] = dim["full_date"].dt.year
    dim["quarter"] = dim["full_date"].dt.quarter
    dim["month_number"] = dim["full_date"].dt.month
    dim["month_name"] = dim["full_date"].dt.strftime("%B")
    dim["day_of_month"] = dim["full_date"].dt.day
    dim["weekday_number"] = dim["full_date"].dt.day_of_week + 1  # Monday = 1
    dim["weekday_name"] = dim["full_date"].dt.strftime("%A")
    dim["is_weekend"] = dim["weekday_number"] >= 6
    dim["iso_week"] = dim["full_date"].dt.isocalendar().week.astype(int)
    dim["full_date"] = dim["full_date"].dt.strftime("%Y-%m-%d")
    return dim.sort_values("date_key").reset_index(drop=True)


def build_dim_time(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """The 48 half-hour slots of a local day, independent of the data."""
    keys = range(1, 49)
    rows = []
    for key in keys:
        minutes = (key - 1) * 30
        hour, minute = divmod(minutes, 60)
        if hour < 6:
            band = "Night (00-06)"
        elif hour < 12:
            band = "Morning (06-12)"
        elif hour < 17:
            band = "Afternoon (12-17)"
        elif hour < 21:
            band = "Evening peak (17-21)"
        else:
            band = "Late evening (21-24)"
        rows.append(
            {
                "time_key": key,
                "time_label": f"{hour:02d}:{minute:02d}",
                "hour_of_day": hour,
                "half_hour_of_hour": 1 if minute == 0 else 2,
                "day_part": band,
            }
        )
    return pd.DataFrame(rows)
