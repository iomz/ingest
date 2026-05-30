# Metrics

## Activity Level

- None: no activities or 0 km
- Light: >0 km and <=5 km
- Moderate: >5 km and <=12 km
- High: >12 km

## Recovery Compatibility

- Good: None or Light
- Acceptable: Moderate
- Poor: High

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

## Deduplication

Activities from multiple sources are normalized before daily context generation.
`source` and `source_id` identify the original record. Dedupe is deterministic
and compares normalized activity rows.

Two activities are treated as the same activity when all of these are true:

- Start times are within 10 minutes.
- Durations differ by no more than 10 minutes or 15%.
- Distances differ by no more than 15% when both activities have distance.
- Activity types are compatible.

Compatible walking activity types include:

- walk
- walking
- indoor walking
- hike

Primary activity selection:

- For overlapping walking activities, Strava is primary.
- For swimming activities, Withings is primary.
- If only one source has an activity, that activity is primary.

Only primary activities are aggregated in `today_context.md`.

## Data Coverage

Data coverage describes the normalized activity records used for the generated
context.

- Sources: source names present before deduplication.
- Primary activities: count of activities after deduplication.
- Deduplicated activities: count removed by deduplication.
