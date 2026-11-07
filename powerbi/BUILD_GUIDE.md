# Power BI build guide

Everything here is reproducible from the repository in about 45 minutes in Power BI
Desktop. The pipeline has already produced the model tables as CSV in
`powerbi/model_data/`, and the measures are in `measures.dax`.

## 1. Load the tables

Home → Get data → Text/CSV, four times, against `powerbi/model_data/`:

| File | Table name in the model |
|---|---|
| `dim_date.csv` | `DimDate` |
| `dim_time.csv` | `DimTime` |
| `fact_intensity.csv` | `FactIntensity` |
| `data_quality_results.csv` | `DataQuality` |

Or paste the queries from `power_query.m` into the Advanced Editor, which also does
the column renaming and typing in one go.

Two things that are easy to skip and annoying to fix later:

- **Mark `DimDate` as a date table** on `FullDate` (Table tools → Mark as date table).
  Without this, `DATEADD` and `DATESINPERIOD` either refuse to run or silently
  return the wrong window.
- **Set sort-by columns**: `MonthName` by `MonthNumber`, `WeekdayName` by
  `WeekdayNumber`, `TimeLabel` by `TimeKey`. Otherwise everything sorts
  alphabetically and the heatmap reads April, August, December.

## 2. Relationships

Model view. Two relationships, both one-to-many, single direction, from the
dimension to the fact:

```
DimDate[DateKey]  1 ──< *  FactIntensity[date_key]
DimTime[TimeKey]  1 ──< *  FactIntensity[time_key]
```

`DataQuality` stays disconnected — it describes the run, not the periods, so
joining it would only create a path that filters the fact table by accident.

Hide `FactIntensity[date_key]` and `FactIntensity[time_key]` from report view:
nobody should drag a surrogate key onto a chart.

## 3. Measures

Create a blank table called `_Measures` (Home → Enter data, one dummy column, then
delete the column) and put every measure there, so they sit at the top of the field
list instead of being scattered across three tables.

Paste the 18 measures from `measures.dax`. Formatting:

| Measure | Format |
|---|---|
| `Avg Actual Intensity`, `Avg Forecast Intensity` | 0 dp, suffix gCO₂/kWh in the visual title |
| `Mean Absolute Forecast Error`, `Forecast Bias` | 1 dp |
| `High Or Very High %`, `Low Or Very Low %`, `Missing Actuals %`, `Forecast Within 20 %`, `Forecast MAPE %` | percentage, 1 dp |
| `Change vs Last Week` | 1 dp, conditional colour: **negative is good** (greener grid) |

## 4. Page 1 — Overview

Six KPI cards across the top:

1. `Avg Actual Intensity`
2. `Greenest Half Hour` (card, text) with `Greenest Half Hour Value` as the subtitle
3. `Dirtiest Half Hour` with `Dirtiest Half Hour Value`
4. `Low Or Very Low %`
5. `High Or Very High %`
6. `Mean Absolute Forecast Error`

Below them:

- **Line chart** — X: `DimDate[FullDate]`, Y: `Avg Actual Intensity` and
  `Rolling 7 Day Avg Actual`. The rolling line is the one to talk about; the daily
  line on its own is noise.
- **Donut** — `FactIntensity[intensity_index]` by `Periods`, with the four bands
  coloured from green to red in the published order, not alphabetically.
- **Slicers** — `DimDate[FullDate]` (between), `DimDate[IsWeekend]`, `DimTime[DayPart]`.
  Sync the date slicer across all four pages (View → Sync slicers).

## 5. Page 2 — Time Patterns

- **Matrix as heatmap** — Rows: `DimDate[WeekdayName]`, Columns: `DimTime[HourOfDay]`,
  Values: `Avg Actual Intensity`. Then Format → Cell elements → Background colour →
  on, Minimum green, Maximum red. That is the headline visual of the whole report.
- **Column chart** — X: `DimTime[TimeLabel]` (all 48), Y: `Avg Actual Intensity`,
  sorted by `TimeKey` not by value, so the daily shape is visible.
- **Bar chart** — `DimTime[DayPart]` by `Avg Actual Intensity`.
- **Card** — `Peak To Trough Gap`, titled "Difference between the greenest and
  dirtiest half-hour".

## 6. Page 3 — Forecast Accuracy

- **Scatter** — X: `Avg Forecast Intensity`, Y: `Avg Actual Intensity`, details:
  `FactIntensity[period_start_local]`. Add a 1:1 reference line (Analytics pane →
  Y-axis constant line won't do it; use a calculated `Forecast` column on both axes,
  or just set equal axis ranges and note it in the title).
- **Line chart** — X: `DimDate[FullDate]`, Y: `Rolling 7 Day MAE`.
- **Column chart** — X: `DimTime[HourOfDay]`, Y: `Mean Absolute Forecast Error`.
  This shows *when* the forecast struggles, which is the interesting part.
- **Cards** — `Forecast Within 20 %`, `Forecast MAPE %`, `Forecast Bias`.

## 7. Page 4 — Data Quality

- **Table** — `DataQuality[check_name]`, `severity`, `issue_count`, `detail`, with
  conditional icons on `passed`.
- **Cards** — `Periods`, `Missing Actuals %`, `Checks Failed`.
- **Column chart** — X: `DimDate[FullDate]`, Y: `Periods`, with a constant line at
  48. Any bar that isn't on the line is a day with a gap — or a clock-change day,
  which is the honest caveat to point at in an interview.
- **Text box** — the three limitations from the manager note. A data-quality page
  that only reports green is not a data-quality page.

## 8. Before you call it finished

- Set page size to 16:9, turn on a consistent theme (View → Themes).
- Give every visual a title that states the unit.
- File → Options → Current file → Report settings → turn off "persistent filters"
  so the report opens in a known state.
- Save as `powerbi/carbon_dashboard.pbix` and export each page to PNG into
  `powerbi/screenshots/` so the repository shows the work without needing Power BI.

## Numbers to expect

If the model is wired up correctly, the 90-day window loaded on 5 November 2026
gives these. They are a useful check that the DAX matches the SQL.

| Measure | Value |
|---|---|
| `Periods` | 4,320 |
| `Avg Actual Intensity` | 118.9 |
| `Avg Forecast Intensity` | 116.2 |
| `Mean Absolute Forecast Error` | 12.0 |
| `Forecast Bias` | +2.8 |
| `Forecast Within 20 %` | 84.3% |
| `Forecast MAPE %` | 11.6% |
| `Low Or Very Low %` | 36.0% |
| `High Or Very High %` | 20.6% |
| `Missing Actuals %` | 0.14% |
| `Greenest Half Hour` | 12:00 (86.0) |
| `Dirtiest Half Hour` | 19:00 (155.9) |
