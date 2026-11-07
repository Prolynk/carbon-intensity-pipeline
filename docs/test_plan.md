# Test plan — GB carbon intensity pipeline

**Version** 1.1 · **Date** 7 November 2026 · **Author** [your name] · **Status** active

---

## Purpose

The pipeline pulls half-hourly carbon intensity readings from a public REST API and
loads them into a star schema that a Power BI report reads. The report is used to
answer when the grid is greenest and how reliable the published forecast is.

If the data is wrong, the report is confidently wrong, and nothing on the dashboard
says so. This plan sets out how the loaded data is tested, what counts as a pass,
and what is deliberately not covered.

## Scope

**In scope**

- The contents of the loaded database: `fact_intensity`, `dim_date`, `dim_time`.
- Agreement between the database and the saved raw API responses it was built from.
- Behaviour of the load on a repeat run (idempotency and late-arriving values).

**Out of scope**

- Accuracy of the source data itself. The published actual is treated as truth;
  this project has no independent measurement to check it against.
- The Power BI report. DAX measures are checked by hand against the SQL views in
  `sql/views.sql`, not by automated test.
- Performance and load. The dataset is 4,320 rows and the pipeline runs in seconds.
- Security. The API is public, read-only and needs no key.

## Data sources

| | |
|---|---|
| Source | Carbon Intensity API, `https://api.carbonintensity.org.uk` |
| Publisher | National Energy System Operator, licensed CC BY 4.0 |
| Endpoint | `/intensity/{from}/{to}`, capped at 14 days per request |
| Grain | One row per half-hourly settlement period |
| Fields | `from`, `to`, `intensity.forecast`, `intensity.actual`, `intensity.index` |
| Volume tested | 90 days = 4,320 periods |
| Raw responses | Saved to `data/raw/`, so every test can reconcile against the source offline |

## Risks

Ordered by what would do the most damage if it got through.

| # | Risk | Why it matters | Mitigating tests |
|---|---|---|---|
| 1 | Silently dropped periods | A gap biases every average without changing anything visible on the dashboard | DQ-01, DQ-05, DQ-14, DQ-21 |
| 2 | Duplicated periods from chunk seams | The pipeline fetches in 7 overlapping chunks; a duplicate would double-count | DQ-01, DQ-07 |
| 3 | Nulls imputed as zero | Would drag averages down and flatter the forecast | DQ-13 |
| 4 | Unsettled actuals mistaken for missing data | Leads to either a false alarm or a real gap being waved through | DQ-03, DQ-04 |
| 5 | Transform producing internally consistent but wrong values | Every rule-based test would still pass | DQ-23, DQ-24, DQ-25 |
| 6 | A re-run duplicating or corrupting rows | The pipeline is designed to be re-run | DQ-26, DQ-27 |
| 7 | Clock changes breaking the time key | Europe/London has 46- and 50-period local days | DQ-17, DQ-18 |

## Test types

| Type | Question it answers | Tests |
|---|---|---|
| **Completeness** | Is everything that should be here, here? | DQ-01 … DQ-06 |
| **Validity** | Is each value a legal value? | DQ-07 … DQ-13 |
| **Consistency** | Do rows agree with each other and with the schema? | DQ-14 … DQ-20 |
| **Reconciliation** | Does the warehouse still agree with the source? | DQ-21 … DQ-27 |

Completeness and validity are row-level rules. Consistency is about relationships
between rows. Reconciliation is the only category that compares the database with
something outside it, and it is the only one that can catch a transform that is
wrong but self-consistent.

## Test catalogue

27 automated tests in `tests/data_quality/`.

| ID | Area | Check | Expected |
|---|---|---|---|
| DQ-01 | Completeness | Row count equals days × 48 | Exactly 4,320 for 90 days |
| DQ-02 | Completeness | Forecast present on every period | Zero nulls |
| DQ-03 | Completeness | Null actuals only within 24h of the window end | No older nulls |
| DQ-04 | Completeness | Null actuals form one contiguous tail | Not scattered |
| DQ-05 | Completeness | Every calendar day in the range is present | No absent days |
| DQ-06 | Completeness | Dimensions populated, `dim_time` exactly 48 rows | Match |
| DQ-07 | Uniqueness | No duplicate period start times | Zero duplicates |
| DQ-08 | Validity | Forecast numeric and within 0–1000 gCO₂/kWh | All pass |
| DQ-09 | Validity | Actual never negative | All pass |
| DQ-10 | Validity | Actual within 0–1000 gCO₂/kWh | All pass |
| DQ-11 | Validity | Index is one of the five documented categories | All pass |
| DQ-12 | Validity | `has_actual` flag agrees with `actual` being null | Zero mismatches |
| DQ-13 | Validity | Unknown actuals stored as NULL, never zero | Zero zeros |
| DQ-14 | Continuity | Each period starts 30 minutes after the last | No gaps |
| DQ-15 | Consistency | End minus start is 30 minutes | All pass |
| DQ-16 | Continuity | Periods do not overlap | Zero overlaps |
| DQ-17 | Consistency | Timestamps stored in UTC with a `Z` suffix | All pass |
| DQ-18 | Consistency | Local column is the same instant as the UTC column | Zero mismatches |
| DQ-19 | Structure | Fact keys resolve in both dimensions | Zero orphans |
| DQ-20 | Consistency | `forecast_error` equals actual minus forecast | Within 1e-6 |
| DQ-21 | Reconciliation | Row count matches the saved raw files | Equal |
| DQ-22 | Reconciliation | No warehouse period absent from the raw files | Zero invented |
| DQ-23 | Reconciliation | Average actual recomputes from the raw JSON | Within 0.01 |
| DQ-24 | Reconciliation | Every forecast and actual matches the raw record | Within 0.01 |
| DQ-25 | Reconciliation | Index bands match the raw record | Exact |
| DQ-26 | Idempotency | Loading twice leaves the row count unchanged | Equal |
| DQ-27 | Idempotency | A settled actual replaces an existing null in place | Updated, not inserted |

## Pass and fail criteria

- **Pass:** all 27 tests pass against the loaded warehouse.
- **Fail:** any test fails. The pipeline exits non-zero and the CI run goes red.
- **Known exception:** none currently open. DEF-001 was closed by the 48-hour
  backfill on 7 November 2026, and DQ-03 and DQ-04 now pass on live data.

A failure is treated as a stop, not a warning. The one judgement call in the suite
is DQ-03, which distinguishes an acceptable unsettled actual at the tail of the
window from an unacceptable null in the middle of it.

## Proving the tests can fail

A suite that only ever passes proves nothing. `tests/fixtures/sample_with_defects.json`
is a real 48-period day with **7 planted defects**: a duplicate period, a missing
period, a negative actual, an implausibly large actual, an undocumented index
category, a null forecast, and a period that is not 30 minutes long.

```bash
python scripts/verify_defect_detection.py
```

The script runs the suite against the clean fixture and then the defective one, and
checks each planted defect tripped the tests nominated for it. Latest result:
**27 passed on clean data, 9 failed on the defect fixture, 7 of 7 defects caught.**
Full output in `docs/defect_detection_report.md`.

## How and when tests run
First CI run: 7 November 2026, commit 89ed6f8, 27 seconds, all steps green.

| Trigger | What runs |
|---|---|
| `pytest` locally | All 74 tests: 47 pipeline unit tests plus 27 data-quality tests |
| `pytest tests/data_quality --target=warehouse` | Data-quality tests against the loaded database |
| `pytest tests/data_quality --target=defects` | The same tests against the defect fixture, expected to fail |
| `python scripts/verify_defect_detection.py` | The clean-then-defective comparison above |
| After every pipeline run | `validate.py` runs 9 in-pipeline checks before anything is loaded |
| GitHub Actions, on push | `.github/workflows/tests.yml` — unit tests, then defect detection |
| GitHub Actions, daily | `.github/workflows/pipeline-daily.yml` — live pipeline run, then the data-quality suite against the result |

The in-pipeline checks in `validate.py` and the tests in `tests/data_quality/` are
deliberately separate. `validate.py` runs on the DataFrame *before* the load and
stops a bad batch getting in. The data-quality tests run on the database *after*
the load, so they also cover anything the load itself could break.

## Known limitations

1. **Plausible ranges are judgement, not physics.** The 0–1000 gCO₂/kWh band is
   wide enough to admit any real reading and narrow enough to catch a unit error.
   It would not catch a value that is wrong but reasonable.
2. **DQ-03's 24-hour window is a chosen threshold.** Observed settlement lag is one
   to three hours. Twenty-four hours is a deliberately loose bound; tightening it
   would catch problems sooner and produce more false alarms.
3. **Reconciliation compares the warehouse with the saved raw files, not with the
   live API.** If a raw file were corrupted on disk, both sides would agree. The
   raw files are treated as the retained source of record.
4. **The source revises its own data.** Forecasts for unsettled periods are revised,
   and settled actuals are refined by up to ±2 gCO₂/kWh for roughly 24 hours. See
   DEF-002. Reconciliation therefore compares against the raw files captured at
   load time, not against whatever the API returns today.
5. **No test covers the Power BI report.** A DAX measure could disagree with the SQL
   and nothing here would notice. `powerbi/BUILD_GUIDE.md` lists expected values to
   check by hand.
6. **Source duplicates are absorbed before the tests can see them.** See DEF-003.

## Sign-off

| Role | Name | Date | Status |
|---|---|---|---|
| Author | [your name] | 7 Nov 2026 | Written |
| Reviewer | — | — | Pending |

Test results at time of writing: **27 of 27 data-quality tests passing** against
4,320 loaded periods, **47 of 47 pipeline unit tests passing**, **7 of 7 planted
defects caught**, **1 defect closed** (DEF-001), **2 open observations**
(DEF-002, DEF-003).
