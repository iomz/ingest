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

Data coverage describes the Withings activity records used for the generated context.

- Sources: source names present.
- Activities: count of activities for the target date.
