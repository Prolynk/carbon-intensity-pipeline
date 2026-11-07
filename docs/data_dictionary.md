# Data dictionary

Database: `data/carbon_intensity.db` (SQLite). Grain of the fact table is one
settlement period — half an hour of Great Britain's electricity grid.

Source: [Carbon Intensity API](https://api.carbonintensity.org.uk) from the
National Energy System Operator, licensed CC BY 4.0.

---

## `fact_intensity`

One row per half-hourly settlement period. 4,320 rows for the 90-day window.

| Column | Type | Description |
|---|---|---|
| `period_start_utc` | TEXT (ISO8601) | **Primary key.** Start of the settlement period, UTC. The natural key and the upsert key. |
| `period_end_utc` | TEXT (ISO8601) | End of the period, always `period_start_utc + 30 minutes`. |
| `period_start_local` | TEXT | Same instant in `Europe/London`, with the UTC offset included so BST and GMT are distinguishable. |
| `date_key` | INTEGER | `yyyymmdd` of the **local** date. Foreign key to `dim_date`. |
| `time_key` | INTEGER | 1–48, the local half-hour slot. Foreign key to `dim_time`. |
| `forecast` | REAL | Published forecast carbon intensity, gCO₂/kWh. Present for every period. |
| `actual` | REAL | Settled actual carbon intensity, gCO₂/kWh. **Nullable** — recent periods are forecast-only until settlement. |
| `intensity_index` | TEXT | Published band: `very low`, `low`, `moderate`, `high`, `very high`. Lower-cased and trimmed on load. |
| `forecast_error` | REAL | `actual - forecast`. Null when `actual` is null — never zero-filled. Positive means the grid was dirtier than forecast. |
| `abs_forecast_error` | REAL | `ABS(forecast_error)`. The input to MAE. |
| `has_actual` | INTEGER | 1 or 0. Lets the dashboard count unsettled periods without a null-aware filter. |
| `loaded_at_utc` | TEXT | When this row was last written. Refreshed by the upsert, so it shows when a late actual arrived. |

Why UTC is the grain: `Europe/London` has a 46-period day in March and a 50-period
day in October. Storing the key in local time would make the primary key ambiguous
on the autumn clock change, where 01:30 happens twice.

---

## `dim_date`

One row per local calendar date covered by the fact table. 90 rows.

| Column | Type | Description |
|---|---|---|
| `date_key` | INTEGER | **Primary key**, `yyyymmdd`. |
| `full_date` | TEXT | `yyyy-mm-dd`. Mark the Power BI table as a date table on this. |
| `year`, `quarter`, `month_number` | INTEGER | Calendar parts. |
| `month_name` | TEXT | Full month name. Sort by `month_number` in Power BI. |
| `day_of_month` | INTEGER | 1–31. |
| `weekday_number` | INTEGER | 1 = Monday … 7 = Sunday (ISO). |
| `weekday_name` | TEXT | Full weekday name. Sort by `weekday_number`. |
| `is_weekend` | INTEGER | 1 for Saturday and Sunday. |
| `iso_week` | INTEGER | ISO week number. |

---

## `dim_time`

The 48 half-hour slots of a local day. Fixed at 48 rows, generated rather than
derived from the data, so a slot with no observations still appears on an axis.

| Column | Type | Description |
|---|---|---|
| `time_key` | INTEGER | **Primary key**, 1–48. Slot 1 is 00:00–00:30. |
| `time_label` | TEXT | `HH:MM` start of the slot. |
| `hour_of_day` | INTEGER | 0–23. The heatmap axis. |
| `half_hour_of_hour` | INTEGER | 1 for `:00`, 2 for `:30`. |
| `day_part` | TEXT | `Night (00-06)`, `Morning (06-12)`, `Afternoon (12-17)`, `Evening peak (17-21)`, `Late evening (21-24)`. |

**Known caveat.** `time_key` is derived from local clock time, so on the two clock
change days a local day has 46 or 50 periods: two slots are absent in March and two
occur twice in October. The affected dates are visible on the Data Quality page as
bars that miss the constant line at 48. The UTC sequence is unbroken throughout, so
no data is lost — only the local-day grouping is uneven.

---

## `data_quality_results`

One row per check per pipeline run. Written by `validate.py` via `load.py`.

| Column | Type | Description |
|---|---|---|
| `run_id` | TEXT | **Composite PK** with `check_name`. 12-character run identifier. |
| `run_at_utc` | TEXT | When the run started. |
| `check_name` | TEXT | e.g. `no_gaps_in_sequence`. |
| `passed` | INTEGER | 1 or 0. |
| `severity` | TEXT | `error` stops the run; `warning` is recorded and the run continues. |
| `issue_count` | INTEGER | Number of offending rows or periods. |
| `detail` | TEXT | Human-readable result, shown directly on the dashboard. |

---

## `pipeline_runs`

One row per run — the audit trail, and where the CV numbers come from.

| Column | Type | Description |
|---|---|---|
| `run_id` | TEXT | **Primary key.** |
| `run_at_utc` | TEXT | Run start. |
| `window_start_utc`, `window_end_utc` | TEXT | Requested window, half-open `[start, end)`. |
| `days_requested` | REAL | Window length in days. |
| `api_requests` | INTEGER | Live calls made. 0 on a cached re-run. |
| `cache_hits` | INTEGER | Chunks served from `data/raw/`. |
| `retries` | INTEGER | Retries triggered across all chunks. |
| `periods_loaded` | INTEGER | Fact rows written by the upsert. |
| `checks_run`, `checks_failed` | INTEGER | Validation summary. |
| `missing_actuals` | INTEGER | Periods with no settled actual. |
| `status` | TEXT | `success` or `failed`. |

---

## Views (`sql/views.sql`)

| View | Purpose |
|---|---|
| `v_intensity_enriched` | Fact joined to both dimensions. The flat table for ad-hoc SQL. |
| `v_intensity_by_half_hour` | Average actual and forecast per local half-hour slot. Answers "when is it greenest". |
| `v_intensity_weekday_hour` | Weekday × hour average. The heatmap source. |
| `v_forecast_accuracy_daily` | Daily MAE, bias and worst half-hour error. |
| `v_daily_summary` | Daily averages plus the greenest and dirtiest period of each day. |
| `v_index_mix` | Share of periods in each published index band. |
| `v_latest_data_quality` | The most recent run's check results. |

---

## Units and conventions

- Carbon intensity is **gCO₂ per kWh** of electricity consumed, a GB national
  average. It is not regional and not demand-weighted by your own usage.
- All timestamps ending `Z` are UTC. `period_start_local` carries an explicit offset.
- Ranges are half-open: `--start 2026-08-08 --end 2026-11-06` is 90 full days and
  ends at the last period beginning before midnight on 5 November.
- Nulls mean "not known yet", never zero. No actual is ever imputed.
