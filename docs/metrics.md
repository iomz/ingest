# Metrics

## Activity Sources

Withings remains authoritative for daily step totals and body metrics.

Suunto is authoritative for workout distance, duration, activity type, heart
rate, TSS, and activity energy. Matching Withings activity rows
are suppressed from Physical Context aggregation so mirrored Apple Health or
Suunto workouts do not inflate movement totals or walking trends. Source CSVs
remain unchanged.

## Walking Trend

Compare current 7-day average daily walking distance with the previous 7-day
average.

- Threshold: 0.50 km/day
- Increasing: current average is at least 0.50 km/day higher
- Stable: difference is within +/-0.50 km/day
- Decreasing: current average is at least 0.50 km/day lower
- Unknown: insufficient data

## Weight Trend

Compare current 7-day average weight with the previous 7-day average.

- Threshold: 0.30 kg
- Increasing: current average is at least 0.30 kg higher
- Stable: difference is within +/-0.30 kg
- Decreasing: current average is at least 0.30 kg lower
- Unknown: insufficient data

## Data Coverage

Data coverage describes source roles used for generated context.

- Workout source: source names for primary workout records.
- Step source: Withings when daily steps are available.
- Body source: Withings when body measures are available.
- Activity count: primary workout count after source precedence and deduplication.

## Training Load

Workout-level TSS and calculation methods follow Suunto's
[Training Stress Score in Suunto app](https://www.suunto.com/sports/News-Articles-container-page/training-stress-score-in-suunto-app/)
concepts. CTL, ATL, and TSB terminology follows Suunto's
[training load guidance](https://www.suunto.com/sports/News-Articles-container-page/understand-and-manage-your-training-load-with-suunto/).

ingest calculates transparent training-load values from daily total Suunto TSS:

- `alpha = 1 - exp(-1 / time_constant)`
- `EWMA_today = EWMA_previous + alpha * (daily_TSS - EWMA_previous)`
- CTL uses a 42-day time constant.
- ATL uses a 7-day time constant.
- TSB is CTL minus ATL.
- Missing days contribute TSS 0 so load decays on rest days.
- Calculation starts from zero before the earliest locally available TSS date.

Physical Context reports CTL, ATL, and TSB at end of report date, after that
date's TSS has been applied. Values are intended to inform planning for
following day. They are ingest-defined and are not guaranteed to match Suunto
App internal values.

History coverage is reported with training load:

- fewer than 7 calendar days: ATL and TSB are warming up
- 7 to fewer than 42 calendar days: CTL is warming up
- 42 calendar days or more: training-load baseline is available

Coverage counts calendar days from earliest available Suunto TSS date through
report date, including zero-TSS rest days.

TSB labels use Suunto-style zones:

- below -30: Too high intensity
- -30 to below -10: Fatigue / Improving fitness
- -10 to below 15: Training balance
- 15 or above: Losing fitness or recovering

When fewer than 7 calendar days of TSS history are available, Physical Context
shows `warming up` instead of a TSB zone in Daily Snapshot and Machine Handoff.
After 7 days, TSB zones are shown while CTL may continue warming until day 42.

Suunto `recoveryTime` remains preserved in normalized and raw source data but is
not shown in Physical Context.

## Activity Trends

Activity trend rows follow report date's primary activity type, selected by
greatest total duration. Walking, running, cycling, and swimming use same-type
distance when available and duration. Other activity types fall back to workout
duration. A non-walking workout day does not emit a zero walking-distance trend.

Workout trend columns show the report date value, trailing 7-day total, and
trailing 30-day total normalized to a weekly average. Non-activity days do not
turn these metrics into per-day averages. Direction percentages are shown only
when at least three same-activity sessions with that metric exist in the
trailing 30 days. The first available observation is marked as first recorded;
other sparse histories are marked as baseline forming. Weight, steps, and
estimated-deficit trends retain their daily or rolling-average units.

Performance rows use distance-weighted aggregation: duration and distance are
summed before pace or speed is calculated. Walking and running use `min/km`,
swimming uses `min/100m`, and cycling uses `km/h`. Rows require positive
distance and duration; Withings swimming distance remains excluded. Fewer than
three valid sessions show `Baseline forming`. Pace direction treats lower as
faster, while cycling speed treats higher as faster.

TSS is activity-agnostic and appears with distance and duration under Workout,
not under primary activity type. Report-date and rolling values sum TSS from
all deduplicated Suunto workouts. TSS uses same weekly columns. Fewer than
three TSS-bearing workouts show `Baseline forming`; fewer than 42 days of
available TSS history show `Training load history limited`. Percentage
direction appears only after both checks pass.
