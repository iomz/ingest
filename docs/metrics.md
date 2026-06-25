# Metrics

## Activity Sources

Withings remains authoritative for daily step totals and body metrics.

Suunto is authoritative for workout distance, duration, activity type, heart
rate, TSS, activity energy, and recovery time. Matching Withings activity rows
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

## Suunto Load Metrics

Physical Context reports only source-provided activity/load values:

- TSS
- Suunto recovery time
- average and maximum heart rate
- activity energy

ingest does not derive recovery compatibility, fatigue risk, or a recovery load
score.
