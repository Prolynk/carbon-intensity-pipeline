-- Star schema for GB carbon intensity.
-- One fact table at half-hourly grain, two conformed dimensions.
-- SQLite dialect; ON CONFLICT ... DO UPDATE gives us the upsert.

CREATE TABLE IF NOT EXISTS dim_date (
    date_key        INTEGER PRIMARY KEY,   -- yyyymmdd, local calendar date
    full_date       TEXT    NOT NULL,
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,
    month_number    INTEGER NOT NULL,
    month_name      TEXT    NOT NULL,
    day_of_month    INTEGER NOT NULL,
    weekday_number  INTEGER NOT NULL,      -- Monday = 1
    weekday_name    TEXT    NOT NULL,
    is_weekend      INTEGER NOT NULL,
    iso_week        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_time (
    time_key           INTEGER PRIMARY KEY, -- 1..48, local half-hour slot
    time_label         TEXT    NOT NULL,
    hour_of_day        INTEGER NOT NULL,
    half_hour_of_hour  INTEGER NOT NULL,
    day_part           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_intensity (
    period_start_utc    TEXT    PRIMARY KEY,  -- natural key; the grain
    period_end_utc      TEXT    NOT NULL,
    period_start_local  TEXT    NOT NULL,
    date_key            INTEGER NOT NULL REFERENCES dim_date(date_key),
    time_key            INTEGER NOT NULL REFERENCES dim_time(time_key),
    forecast            REAL,
    actual              REAL,
    intensity_index     TEXT,
    forecast_error      REAL,                -- actual - forecast
    abs_forecast_error  REAL,
    has_actual          INTEGER NOT NULL,
    loaded_at_utc       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_fact_date ON fact_intensity(date_key);
CREATE INDEX IF NOT EXISTS ix_fact_time ON fact_intensity(time_key);
CREATE INDEX IF NOT EXISTS ix_fact_index ON fact_intensity(intensity_index);

-- Validation output, so the dashboard's Data Quality page reads from the warehouse
-- rather than from a log file. One row per check per run.
CREATE TABLE IF NOT EXISTS data_quality_results (
    run_id        TEXT    NOT NULL,
    run_at_utc    TEXT    NOT NULL,
    check_name    TEXT    NOT NULL,
    passed        INTEGER NOT NULL,
    severity      TEXT    NOT NULL,
    issue_count   INTEGER NOT NULL,
    detail        TEXT,
    PRIMARY KEY (run_id, check_name)
);

-- One row per pipeline run: the numbers the CV quotes.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id            TEXT PRIMARY KEY,
    run_at_utc        TEXT    NOT NULL,
    window_start_utc  TEXT    NOT NULL,
    window_end_utc    TEXT    NOT NULL,
    days_requested    REAL    NOT NULL,
    api_requests      INTEGER NOT NULL,
    cache_hits        INTEGER NOT NULL,
    retries           INTEGER NOT NULL,
    periods_loaded    INTEGER NOT NULL,
    checks_run        INTEGER NOT NULL,
    checks_failed     INTEGER NOT NULL,
    missing_actuals   INTEGER NOT NULL,
    status            TEXT    NOT NULL
);
