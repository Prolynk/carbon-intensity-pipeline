# When is Britain's electricity cleanest, and can we trust the forecast?

**90 days of grid data, 8 August to 5 November 2026. 4,320 half-hourly readings.**

---

## The short version

Electricity in Great Britain is roughly **twice as clean at lunchtime as it is at
teatime**. Over the last 90 days the average half-hour at midday carried 86 grams of
CO₂ per kilowatt-hour; the average half-hour at 7pm carried 156. Anything that can
be moved — charging, heating water, running a batch job, scheduling a wash — is
worth moving into the middle of the day.

The published forecast is good enough to plan against. It is within 20 grams of the
real number in **84% of half-hours**, and within 50 grams in **97%**.

---

## When the grid is greenest

| | Average intensity (gCO₂/kWh) |
|---|---|
| Afternoon, 12:00–17:00 | **100** |
| Morning, 06:00–12:00 | 110 |
| Overnight, 00:00–06:00 | 116 |
| Late evening, 21:00–24:00 | 131 |
| **Evening peak, 17:00–21:00** | **151** |

The cleanest single half-hour of the day is **12:00–12:30** at 86. The dirtiest is
**19:00–19:30** at 156. That is an 81% swing within a single day, and it repeats
almost every day.

Two other patterns worth knowing:

- **Weekends are cleaner than weekdays** — 104 against 125, about 16% lower.
  Demand is lower, so less gas generation is called on.
- **Overnight is not the green option it is often assumed to be.** It is cleaner
  than the evening, but worse than the afternoon. Advice to "run it overnight" is
  out of date on the current grid.

Over the 90 days, **36% of half-hours were rated low** carbon by the official index,
43% moderate, 21% high or very high.

---

## How reliable the forecast is

Measured against the settled actual for every half-hour:

- **Average error: 12 gCO₂/kWh** — about 10% of a typical reading.
- **Within 20 grams: 84% of the time. Within 50: 97%.**
- **Slight optimism: +2.8 grams.** The forecast tends to say the grid will be
  marginally cleaner than it turns out to be. Small, but consistent, and it points
  the same way every month.
- **Worst single half-hour: 165 grams out.** Large errors are rare and cluster
  around rapid changes in wind output.

In practice: trust it for *choosing a time of day*. Do not trust it to the gram for
reporting or billing — use the settled actual for that.

---

## What this does not tell you

Four limits, stated plainly:

1. **Ninety days is one season.** The window runs from late summer into early
   autumn. November already looks worse than September in the data (165 against
   108), and a full winter is not covered. Do not read an annual pattern into it.
2. **It is a national average.** The real figure in Scotland and the real figure in
   the south of England differ considerably. A regional endpoint exists and is not
   used here.
3. **There is no weather in the model.** Wind output drives most of the variation,
   and nothing here explains *why* a given day was clean or dirty.
4. **The most recent hours are forecast only.** Six of 4,320 periods had no settled
   actual at the time of loading. They are left empty rather than estimated, so
   averages are computed on settled data only.

---

## If we acted on this

The practical recommendation is narrow and defensible: **shift discretionary
electricity use from the 17:00–21:00 window into 11:00–14:00.** On the numbers
above, that is a reduction of roughly 45% in the carbon intensity of whatever is
being moved, with no reduction in how much is used.

For anything measured or reported externally, use the settled actuals rather than
the forecast, and wait for settlement before publishing a figure.

---

*Source: Carbon Intensity API, National Energy System Operator, licensed CC BY 4.0.
Figures from the pipeline run of 5 November 2026 covering 8 August – 5 November 2026.
Full method, code and data-quality results in the project repository.*
