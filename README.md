# GB Carbon Intensity — API to dashboard

<!-- Add after the first green run:
![Tests](https://github.com/YOUR-USERNAME/carbon-intensity-pipeline/actions/workflows/tests.yml/badge.svg)
-->

A Python pipeline that pulls half-hourly carbon intensity data for the Great
Britain electricity grid from a public REST API, cleans and validates it, loads it
into a relational star schema, and feeds a Power BI report.

**The question it answers:** *when is the grid greenest, and how good is the
published forecast?*

**Headline findings from 90 days (8 Aug – 5 Nov 2026, 4,320 half-hourly periods):**

- The grid is roughly **twice as clean at midday (86 gCO₂/kWh) as at 7pm (156)**.
- **Weekends run 16% cleaner than weekdays** — 104 against 125.
- Overnight is *not* the greenest window. The afternoon is.
- The published forecast lands **within 20 gCO₂/kWh in 84% of half-hours**
  (MAE 12.0, MAPE 11.6%), with a small consistent optimism of +2.8.

---

## Quick start

```bash
git clone https://github.com/<you>/carbon-intensity-pipeline.git
cd carbon-intensity-pipeline
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# fetch, validate and load 90 days
python src/run_pipeline.py --start 2026-08-08 --end 2026-11-06

# re-run entirely from the saved raw responses, no API calls
python src/run_pipeline.py --start 2026-08-08 --end 2026-11-06 --offline

pytest

# rebuild the model CSVs and the static dashboard preview from the warehouse
python src/build_preview.py
```

`--end` is exclusive, so the command above is exactly 90 full days. Output lands in
`data/carbon_intensity.db`, with a log in `logs/pipeline.log` and the run's numbers
in `docs/run_summary.json`.

### Options

| Flag | Effect |
|---|---|
| `--start` / `--end` | Window, `YYYY-MM-DD`, UTC, half-open |
| `--days N` | Days back from `--end` when `--start` is omitted (default 90) |
| `--offline` | Replay `data/raw/` only; never touch the API |
| `--no-cache` | Ignore saved responses and refetch |
| `--no-strict` | Record check failures instead of stopping |
| `--db`, `--raw-dir`, `--log-file`, `--summary-file` | Path overrides |
| `--verbose` | Debug logging |

---

## Data source

The [Carbon Intensity API](https://carbonintensity.org.uk) for Great Britain —
free, open, no API key. Data is published by the **National Energy System
Operator** and licensed **CC BY 4.0**.

Each record is one half-hourly settlement period with a start and end time, a
forecast and an actual carbon intensity in gCO₂/kWh, and a published index band
(`very low` through `very high`).

Three properties of the source that the pipeline handles explicitly:

1. **The date range per request is capped at 14 days**, so a 90-day window is
   fetched in 7 chunks.
2. **The API returns the settlement period that *contains* `from`**, so a request
   for 2 days comes back with 97 periods rather than 96. `clip_to_window` trims it;
   left in, that stray period creates a calendar date holding one half-hour and
   quietly skews daily averages.
3. **Recent periods have no `actual` yet.** They settle later. Nulls are stored as
   nulls, never zero-filled, and the upsert refreshes them on a later run.

---

## How it works

```
Carbon Intensity API
        │  7 chunked GETs, retry + backoff
        ▼
  data/raw/*.json ──────────── saved; re-runs replay from here
        │
   transform.py   flatten → dedupe seams → UTC grain + Europe/London column
        │
   validate.py    9 checks; errors stop the run, warnings are recorded
        │
   load.py        upsert into SQLite star schema
        ▼
  data/carbon_intensity.db  →  Power BI (DimDate, DimTime, FactIntensity)
```

| Module | Responsibility |
|---|---|
| `src/extract.py` | Chunked API client. Timeout, 3 retries with doubling backoff, retries only on timeouts / connection errors / 429 / 5xx. Writes every raw response to `data/raw/`. |
| `src/transform.py` | Flattens JSON, de-duplicates chunk seams, keeps UTC as the grain, derives the local-time columns and builds `DimDate` / `DimTime`. |
| `src/validate.py` | Nine checks. Duplicates, gaps, period length, numeric ranges, known index values, missing actuals, future periods. |
| `src/load.py` | `INSERT … ON CONFLICT DO UPDATE` against SQLite. Idempotent by construction. |
| `src/run_pipeline.py` | One command, file logging, non-zero exit on failure, run summary written to JSON and to `pipeline_runs`. |
| `sql/schema.sql` | Star schema DDL plus the two audit tables. |
| `sql/views.sql` | Seven analytical views — the same numbers as the DAX, proved in SQL. |

---

## The model

A star schema, not one flat table:

```
DimDate ──1:*── FactIntensity ──*:1── DimTime
(90 rows)        (4,320 rows)         (48 rows)
```

`FactIntensity` is at the half-hourly grain, keyed on `period_start_utc`.
`DimTime` is generated at a fixed 48 rows rather than derived from the data, so a
slot with no observations still appears on an axis.

**Times are stored in UTC** because `Europe/London` has a 46-period day in March
and a 50-period day in October. A local-time primary key would be ambiguous on the
autumn clock change, where 01:30 occurs twice. The local column is derived for
reporting, since nobody plans a wash in UTC. The consequence — two local days have
46 or 50 periods — is visible on the Data Quality page and documented in
`docs/data_dictionary.md`.

Full column-by-column reference: **[`docs/data_dictionary.md`](docs/data_dictionary.md)**.

---

## Data quality

Nine checks run on every pipeline run, written to `data_quality_results` so the
dashboard reads them from the warehouse rather than from a log file.

| Check | Severity | Result on the 90-day run |
|---|---|---|
| `not_empty` | error | pass |
| `no_duplicate_periods` | error | pass — 6 seam duplicates removed in transform |
| `no_gaps_in_sequence` | error | pass — unbroken 30-minute sequence across 4,320 periods |
| `period_is_30_minutes` | error | pass |
| `forecast_numeric_in_range` | error | pass |
| `actual_in_range` | error | pass — 4,314 actuals, all 0–1000 |
| `index_in_known_set` | error | pass |
| `missing_actuals` | warning | 6 periods (0.14%) unsettled |
| `no_future_periods` | warning | 4 forecast-only periods |

An `error` raises `ValidationError` and the run exits 1 before anything is loaded.
A `warning` is recorded and the run continues — unsettled actuals are a property of
the source, not a defect, and failing the build over them would be crying wolf.

---

## Tests

47 tests, `pytest`.

```
tests/test_extract.py     14   chunking at the 14-day cap, retry on timeout/503/429,
                               no retry on 404, backoff doubling, caching, replay
tests/test_transform.py   13   column contract, UTC handling, local keys, seam
                               de-duplication, nulls preserved, sorting, clipping
tests/test_validate.py    11   planted duplicate, planted gap, out-of-range values,
                               unknown index, warnings that must not stop the run
tests/test_load.py         9   upsert idempotency, late actual refreshed in place,
                               no orphan keys, nulls not zeros, views agree with pandas
```

The ones that matter in an interview:

- `test_loading_twice_gives_the_same_row_count` — proves the upsert.
- `test_upsert_refreshes_a_late_arriving_actual` — proves it also *corrects*, which
  is the whole reason the source needs an upsert rather than an append.
- `test_retry_succeeds_after_two_transient_failures` — asserts the backoff
  schedule, not just that it eventually worked.
- `test_raw_responses_are_saved_and_reused_without_calling_the_api` — asserts the
  API was not hit a second time.

---

## Power BI

`powerbi/` holds everything needed to build the report in about 45 minutes:

- **`BUILD_GUIDE.md`** — load, relationships, measure formatting, and the exact
  visuals for all four pages, plus the numbers to expect as a check.
- **`measures.dax`** — 18 DAX measures including `DATEADD` week-on-week comparison,
  a `DATESINPERIOD` rolling 7-day average, MAE computed row-wise with `AVERAGEX`,
  and the greenest/dirtiest half-hour via `ADDCOLUMNS` over `ALLSELECTED`.
- **`power_query.m`** — M queries for the four tables.
- **`model_data/`** — the model exported as CSV, so the report opens without a
  SQLite ODBC driver.

**Four pages:** Overview (6 KPIs, daily line with rolling average, index mix),
Time Patterns (weekday × hour heatmap — the headline visual), Forecast Accuracy
(forecast vs actual, rolling MAE, error by hour), Data Quality (check results,
periods per day against a constant line at 48).

`dashboard_preview.html` is a static rendering of the same four pages from the same
data — a stand-in for screenshots, and a check that the DAX and the SQL agree. It is
generated by `python src/build_preview.py` straight from the warehouse, so if a
number on the page disagrees with a DAX measure, one of the two is wrong.

---

## Testing

74 automated tests, split by what they protect.

| Suite | Count | Runs against | Purpose |
|---|---|---|---|
| `tests/test_*.py` | 47 | A 12-period fixture | Unit tests for extract, transform, validate, load |
| `tests/data_quality/` | 27 | The loaded database | Data-quality tests: completeness, validity, consistency, reconciliation |

```bash
pytest                                            # all 74
pytest tests/data_quality --target=warehouse      # 27, against the real database
pytest tests/data_quality --target=defects        # the same 27, expected to fail
python scripts/verify_defect_detection.py         # proves the tests can fail
```

The data-quality tests run against the **loaded database**, not a DataFrame, so
they also cover anything the load step could break. They are separate from the 9
in-pipeline checks in `validate.py`, which run *before* the load and stop a bad
batch getting in.

**Proof the tests can fail.** `tests/fixtures/sample_with_defects.json` is a real
48-period day with 7 planted defects — a duplicate, a gap, a negative value, an
out-of-range value, an undocumented index category, a null forecast and a period
of the wrong length. Latest run: **27 passed on clean data, 9 failed on the
defective fixture, 7 of 7 defects caught**
(`docs/defect_detection_report.md`).

Supporting documents:

- **[`docs/test_plan.md`](docs/test_plan.md)** — scope, risks, the 27-test
  catalogue, pass and fail criteria, known limitations.
- **[`docs/traceability.md`](docs/traceability.md)** — 18 requirements mapped to
  tests and defects, including the gaps.
- **[`docs/defects/`](docs/defects/)** — three defect reports, all from real
  observed behaviour.

## Defects found

| ID | Title | Severity | Status |
|---|---|---|---|
| [DEF-001](docs/defects/DEF-001.md) | Six periods loaded with no settled actual | Low | **Closed — fixed** |
| [DEF-002](docs/defects/DEF-002.md) | Source revises forecasts, so measured accuracy depends on fetch time | Medium | Open — accepted |
| [DEF-003](docs/defects/DEF-003.md) | Source duplicates absorbed before any check can see them | Low | Open — accepted |

**DEF-001 is fixed.** `extract.py` gained `--backfill-hours` (default 48): any
cached chunk overlapping the last N hours is re-fetched, and the existing upsert
corrects the rows in place. Verified against the live API on 7 November 2026 —
6 nulls became 0, the row count stayed at 4,320, and one API request did it.

**DEF-002 came out of fixing DEF-001.** Re-fetching showed the source revises the
forecast it publishes for a period that has not settled yet: 5 periods came back
revised upward by between 5 and 23 gCO₂/kWh. So "the forecast is accurate to 12
gCO₂/kWh" is a statement about a forecast captured at mixed lead times, and the
project says so rather than claiming forecast skill at a stated horizon.

## Limitations

- **90 days is one season.** Late summer into early autumn; no winter is covered.
- **National average.** A regional endpoint exists and is not used here.
- **No weather data.** Wind drives most of the variation and nothing here explains
  *why* a given day was clean.
- **Actuals lag.** The most recent periods are forecast-only until settlement.
- **Accuracy is measured at mixed lead times.** See DEF-002.
- **SQLite, not a server database.** Chosen deliberately so the repository is
  self-contained. No cloud services are used, and none are claimed.

---

## Repository layout

```
carbon-intensity-pipeline/
├── src/
│   ├── extract.py           chunked API client, retries, raw-response cache
│   ├── transform.py         JSON → tidy table, UTC grain, dimension builders
│   ├── validate.py          9 data-quality checks
│   ├── load.py              SQLite upsert into the star schema
│   ├── run_pipeline.py      CLI orchestrator, logging, exit codes
│   └── build_preview.py     exports the model CSVs + rebuilds the preview
├── sql/
│   ├── schema.sql           star schema + audit tables
│   └── views.sql            7 analytical views
├── tests/
│   ├── test_*.py            47 pipeline unit tests
│   ├── data_quality/        27 data-quality tests against the database
│   └── fixtures/            clean + deliberately defective sample data
├── data/
│   ├── raw/                 saved API responses (7 files)
│   ├── sample/              12-period fixture for the tests
│   └── carbon_intensity.db  the warehouse
├── powerbi/
│   ├── BUILD_GUIDE.md       step-by-step report build
│   ├── measures.dax         18 DAX measures
│   ├── power_query.m        M queries
│   ├── model_data/          CSV exports of the model
│   ├── _template.html       preview template, data injected at build time
│   └── dashboard_preview.html
├── docs/
│   ├── test_plan.md         scope, risks, catalogue, pass/fail criteria
│   ├── traceability.md      18 requirements mapped to tests and defects
│   ├── defects/             DEF-001, DEF-002, DEF-003
│   ├── defect_detection_report.md   generated evidence
│   ├── data_dictionary.md   every column, every view
│   ├── manager_note.pdf     one page, non-technical
│   └── run_summary.json     the last run's numbers
├── scripts/
│   └── verify_defect_detection.py
├── .github/workflows/
│   ├── tests.yml            unit tests + data-quality tests + defect detection
│   └── pipeline-daily.yml   scheduled live run, then tests
├── logs/pipeline.log
├── requirements.txt
└── README.md
```

---

## Attribution

Carbon intensity data © National Energy System Operator, made available through the
[Carbon Intensity API](https://carbonintensity.org.uk) under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
