# Defect detection report

Generated 2026-11-07 14:39 UTC by `scripts/verify_defect_detection.py`. Do not edit by hand.

## Result

- Tests passing on the clean fixture: **27**
- Running the same tests against the defect fixture: **9 failed, 18 passed**
- Planted defects caught: **7 of 7**

## Defect by defect

| ID | Planted defect | Period | Caught | Tripped |
|---|---|---|---|---|
| D1 | duplicate_period | `2026-09-22T05:00Z` | yes | `test_row_count_equals_days_times_48` |
| D2 | missing_period_gap | `2026-09-22T09:30Z` | yes | `test_no_gaps_in_the_period_sequence`<br>`test_row_count_equals_days_times_48` |
| D3 | negative_actual | `2026-09-22T02:30Z` | yes | `test_actual_values_are_non_negative` |
| D4 | actual_out_of_range | `2026-09-22T03:00Z` | yes | `test_actual_values_are_within_a_plausible_range` |
| D5 | unknown_index_category | `2026-09-22T04:00Z` | yes | `test_index_values_are_documented_categories` |
| D6 | null_forecast | `2026-09-22T05:30Z` | yes | `test_forecast_values_are_numeric_and_in_range`<br>`test_no_missing_forecast_values` |
| D7 | period_not_30_minutes | `2026-09-22T07:00Z` | yes | `test_every_period_spans_exactly_30_minutes`<br>`test_periods_do_not_overlap` |

## Notes

- **D1 (duplicate_period)** — Caught only by the row-count test. transform.py de-duplicates on period start by design, to absorb the overlap at chunk seams, so a duplicate never reaches the warehouse for DQ-07 to see. See docs/defects/DEF-003.md.
