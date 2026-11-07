-- Views used for sanity checks and as an alternative Power BI source.
-- The dashboard itself imports the three star-schema tables and does the
-- aggregation in DAX; these views prove the same numbers in SQL.

DROP VIEW IF EXISTS v_intensity_enriched;
CREATE VIEW v_intensity_enriched AS
SELECT
    f.period_start_utc,
    f.period_start_local,
    d.full_date,
    d.weekday_name,
    d.is_weekend,
    t.time_label,
    t.hour_of_day,
    t.day_part,
    f.forecast,
    f.actual,
    f.intensity_index,
    f.forecast_error,
    f.abs_forecast_error
FROM fact_intensity f
JOIN dim_date d ON d.date_key = f.date_key
JOIN dim_time t ON t.time_key = f.time_key;

-- Average actual intensity by local half-hour slot: the "when is it greenest" answer.
DROP VIEW IF EXISTS v_intensity_by_half_hour;
CREATE VIEW v_intensity_by_half_hour AS
SELECT
    t.time_key,
    t.time_label,
    t.day_part,
    COUNT(f.actual)                   AS periods_with_actual,
    ROUND(AVG(f.actual), 1)           AS avg_actual,
    ROUND(AVG(f.forecast), 1)         AS avg_forecast,
    ROUND(MIN(f.actual), 1)           AS min_actual,
    ROUND(MAX(f.actual), 1)           AS max_actual
FROM fact_intensity f
JOIN dim_time t ON t.time_key = f.time_key
GROUP BY t.time_key, t.time_label, t.day_part;

-- Heatmap source: weekday against hour of day.
DROP VIEW IF EXISTS v_intensity_weekday_hour;
CREATE VIEW v_intensity_weekday_hour AS
SELECT
    d.weekday_number,
    d.weekday_name,
    t.hour_of_day,
    ROUND(AVG(f.actual), 1) AS avg_actual,
    COUNT(f.actual)         AS periods_with_actual
FROM fact_intensity f
JOIN dim_date d ON d.date_key = f.date_key
JOIN dim_time t ON t.time_key = f.time_key
GROUP BY d.weekday_number, d.weekday_name, t.hour_of_day;

-- Forecast accuracy by day: MAE, bias and the worst half-hour.
DROP VIEW IF EXISTS v_forecast_accuracy_daily;
CREATE VIEW v_forecast_accuracy_daily AS
SELECT
    d.full_date,
    d.weekday_name,
    COUNT(f.actual)                        AS periods_scored,
    ROUND(AVG(f.abs_forecast_error), 2)    AS mae,
    ROUND(AVG(f.forecast_error), 2)        AS bias,
    ROUND(MAX(f.abs_forecast_error), 1)    AS worst_abs_error
FROM fact_intensity f
JOIN dim_date d ON d.date_key = f.date_key
WHERE f.actual IS NOT NULL
GROUP BY d.full_date, d.weekday_name;

-- Daily rollup, including the greenest and dirtiest half-hour of each day.
DROP VIEW IF EXISTS v_daily_summary;
CREATE VIEW v_daily_summary AS
SELECT
    d.full_date,
    d.weekday_name,
    d.is_weekend,
    COUNT(*)                     AS periods,
    COUNT(f.actual)              AS periods_with_actual,
    ROUND(AVG(f.actual), 1)      AS avg_actual,
    ROUND(MIN(f.actual), 1)      AS greenest_actual,
    ROUND(MAX(f.actual), 1)      AS dirtiest_actual
FROM fact_intensity f
JOIN dim_date d ON d.date_key = f.date_key
GROUP BY d.full_date, d.weekday_name, d.is_weekend;

-- Share of periods in each published index band.
DROP VIEW IF EXISTS v_index_mix;
CREATE VIEW v_index_mix AS
SELECT
    intensity_index,
    COUNT(*) AS periods,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM fact_intensity), 2) AS pct_of_periods
FROM fact_intensity
GROUP BY intensity_index;

-- Latest result of every data-quality check.
DROP VIEW IF EXISTS v_latest_data_quality;
CREATE VIEW v_latest_data_quality AS
SELECT q.*
FROM data_quality_results q
JOIN (SELECT run_id, MAX(run_at_utc) FROM data_quality_results GROUP BY run_id
      ORDER BY 2 DESC LIMIT 1) latest
  ON latest.run_id = q.run_id;
