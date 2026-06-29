# ingest

`ingest` collects, normalizes, and prepares personal data so an AI assistant can review it.

It is an input pipeline for AI-assisted self-review. It is not a generic health dashboard, quantified-self UI, coach, task planner, or final interpretation layer.

`ingest` prepares personal state for interpretation; it does not replace the interpreter.

## Pipeline

```text
Sources
  -> Normalized Records
  -> DailyState
  -> Rendered Context
  -> AI Review
```

Current code imports Hevy workout exports, fetches Suunto activities through `suuntool`, fetches Vitalsync Apple Health sleep, step, and blood-pressure records, fetches Withings body, activity, workout, and sleep-summary data, writes local records, builds a `DailyState`, and renders AI-readable daily context.

Sleep summaries are assigned to local wake date. Vitalsync starts with Sleep Cycle-derived `sleep_analysis` records and derives daily sleep summaries inside ingest.

## Derived Metrics

Derived metrics are calculated during report generation and are not persisted. The Body section keeps measured body composition values first, then a visual separator and explicit derived metrics rows so downstream LLM consumers can use computed values without re-deriving them.

BMI uses current weight and height:

```text
BMI = weight_kg / (height_m * height_m)
```

BMR uses the Katch-McArdle equation from fat-free mass:

```text
BMR = 370 + (21.6 * fat_free_mass_kg)
```

Estimated deficit uses observed weight change over the previous 30 days:

`weight_30_days_ago` means the latest weight measurement on or before the date 30 days before the report date, not necessarily a measurement from that exact date.

```text
weight_change_kg = weight_30_days_ago - current_weight
estimated_deficit_kcal_per_day = (weight_change_kg * 7700) / 30
```

Physical Context also computes ingest-defined CTL, ATL, and TSB from daily Suunto TSS history. Values include report date TSS and support planning following day.

## Context Sources

Plugins fetch and normalize source-specific data. Physical Context source precedence is configured by metric domain under `[context]`. Context source values use plugin IDs.

| plugin id | plugin name | context sources | sync mechanism |
| --- | --- | --- | --- |
| `hevy` | Hevy Export | strength workouts, strength sets | CSV export or browser export |
| `suunto` | Suunto App via suuntool | workouts, workout load | `suuntool` CLI |
| `vitalsync` | Vitalsync HealthKit Bridge | steps, sleep, blood pressure | Vitalsync receiver API |
| `withings` | Withings Health Cloud | body composition, blood pressure; legacy steps, sleep, workouts | Withings API |

A lower-level context setting overrides a higher-level `default`.

```toml
[context.activity]
default = "suunto"

[context.activity.workout]
default = "suunto"
sets = "hevy"
load = "suunto"

[context.measurement]
default = "withings"
steps = "vitalsync"
blood_pressure = "vitalsync"

[context.recovery]
default = "vitalsync"
sleep = "vitalsync"
```

Defaults favor Suunto for primary workouts because it has stronger distance, duration, activity type, heart-rate, energy, and TSS telemetry than mirrored Withings rows. Hevy is set-level strength detail, not primary workout telemetry. Training-load metrics use Suunto TSS.

Withings remains the body-composition source and can supply blood pressure when its measure export includes paired systolic/diastolic rows. Vitalsync is the default for steps because Withings step totals have been unreliable. Vitalsync is also the default for sleep and blood pressure because those records are closest to Apple Health source data.

Invalid or unavailable configured context sources emit warnings and skip that metric. Missing rows for a report date are treated as missing data, not config errors. Source CSVs remain unchanged.

## Metric Semantics

Physical Context assigns sleep to the wake date using the configured ingest timezone. Sleep appears in the Daily Snapshot and Machine Handoff, but does not get a detailed report section. Sleep remains separate from TSS, CTL, ATL, and TSB.

Activity trend rows follow report date's primary activity type, selected by greatest total duration. Walking, running, cycling, and swimming use same-type distance when available and duration. Other activity types fall back to workout duration. A non-walking workout day does not emit a zero walking-distance trend.

Workout trend columns show report date value, trailing 7-day total, and trailing 30-day total normalized to a weekly average. Non-activity days do not turn these metrics into per-day averages. Direction percentages are shown only when at least three same-activity sessions with that metric exist in the trailing 30 days. Weight, steps, and estimated-deficit trends retain daily or rolling-average units.

Performance rows use distance-weighted aggregation: duration and distance are summed before pace or speed is calculated. Walking and running use `min/km`, swimming uses `min/100m`, and cycling uses `km/h`. Rows require positive distance and duration. Withings swimming distance remains excluded. Pace direction treats lower as faster, while cycling speed treats higher as faster.

ingest calculates transparent training-load values from daily total Suunto TSS:

```text
alpha = 1 - exp(-1 / time_constant)
EWMA_today = EWMA_previous + alpha * (daily_TSS - EWMA_previous)
```

CTL uses a 42-day time constant. ATL uses a 7-day time constant. TSB is CTL minus ATL. Missing days contribute TSS 0 so load decays on rest days. Calculation starts from zero before the earliest locally available TSS date.

Physical Context reports CTL, ATL, and TSB at end of report date, after that date's TSS has been applied. Values are ingest-defined and are not guaranteed to match Suunto App internal values.

History coverage is reported with training load:

- fewer than 7 calendar days: ATL and TSB are warming up
- 7 to fewer than 42 calendar days: CTL is warming up
- 42 calendar days or more: training-load baseline is available

TSS is activity-agnostic and appears with distance and duration under Workout, not under primary activity type. Report-date and rolling values sum TSS from configured load-source workouts. TSS uses same weekly columns. Fewer than three TSS-bearing workouts show `Baseline forming`; fewer than 42 days of available TSS history show `Training load history limited`.

## Commands

Primary daily workflow:

```sh
ingest today
```

Specific date:

```sh
ingest day 2026-05-29
```

Previous day:

```sh
ingest yesterday
```

Source maintenance:

```sh
ingest sync hevy
ingest sync suunto
ingest sync vitalsync
ingest sync withings
ingest sync all
ingest backfill withings --from 2024-01-01
```

Hevy import from CSV export:

```sh
ingest import hevy --csv ~/Downloads/hevy-workouts.csv
```

The Hevy public API currently requires Hevy Pro. Without Pro, use the app export: Profile > Settings > Export & Import Data > Export Data > Export Workouts. `ingest auth hevy` asks for credentials with interactive prompts, logs in through Playwright, and stores only the browser session under the application data directory. `ingest sync hevy` reuses that browser session and does not read Hevy credentials from config.

Suunto sync uses the user-managed [`suuntool`](https://github.com/tajchert/suuntool) command. Install it and run `suuntool login` separately, then enable `[plugin.suunto]` in the config file. `plugin.suunto.command` accepts an absolute executable path and otherwise defaults to `suuntool` from PATH.

Vitalsync sync fetches Apple Health records from the configured `plugin.vitalsync.endpoint`. Enable `[plugin.vitalsync]` and register ingest with a Vitalsync pairing token:

```sh
ingest auth vitalsync register-client --pairing-token "<PAIRING_TOKEN>" --client-label "ingest"
```

This saves `client_id`, `refresh_token`, `access_token`, and `expires_at` to `${XDG_DATA_HOME:-~/.local/share}/ingest/vitalsync/auth.json`, not the config file. `ingest sync vitalsync` refreshes the access token automatically when needed. Supported record types are `sleep_analysis`, `blood_pressure`, and `step_count`; sleep is filtered to Sleep Cycle (`com.lexwarelabs.goodmorning`) unless `plugin.vitalsync.source_bundle_id` is set to an empty string. Sync writes sleep, blood-pressure, and step CSV headers even when no matching records are returned, so context generation can distinguish "no rows yet" from "plugin has not synced."

Withings OAuth helpers:

```sh
ingest auth withings
```

The guided flow prompts for client credentials, opens the Withings authorization URL, captures localhost redirects when the registered redirect URI allows it, and otherwise accepts a pasted redirect URL or authorization code. Low-level helpers remain available:

```sh
ingest auth withings auth-url --redirect-uri "https://your-registered-callback"
ingest auth withings exchange-code --redirect-uri "https://your-registered-callback" --code "<code>"
```

Withings client credentials and OAuth tokens are stored in `${XDG_DATA_HOME:-~/.local/share}/ingest/withings/auth.json`, not the config file.

## Files

Repository contains code only:

```text
ingest repository = source code, tests, config templates
configuration file = credentials and local settings
application data directory = telemetry, cache, generated files
```

Default config path:

```text
${XDG_CONFIG_HOME:-~/.config}/ingest/config.toml
```

Create local config:

```sh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/ingest"
cp config.example.toml "${XDG_CONFIG_HOME:-$HOME/.config}/ingest/config.toml"
```

When the default config file is missing, `ingest` creates the config directory and exits with the copy instruction. It does not write a placeholder `config.toml`; the file contains local source choices, so an explicit copy/edit step keeps first-run setup visible.

`app.timezone` defines local report dates and interpretation of source timestamps without an explicit UTC offset. It defaults to `Asia/Tokyo`.

Terminal styling supports calm and colorful named themes. Body-weight direction color can follow a simple goal without changing report calculations:

```toml
[ui]
theme = "colorful"
body_weight_goal = "loss"
```

Valid weight goals are `loss`, `maintenance`, and `gain`. Rich continues to respect non-TTY output and `NO_COLOR`.

Default application data directory:

```text
${XDG_DATA_HOME:-~/.local/share}/ingest
```

Override data directory:

```toml
[app]
data_dir = "/path/to/ingest-data"
```

Current generated layout:

```text
${XDG_DATA_HOME:-~/.local/share}/ingest/
├── hevy/
│   ├── browser/
│   ├── raw/
│   ├── workouts.csv
│   └── sets.csv
├── suunto/
│   ├── raw/
│   └── workouts.csv
├── vitalsync/
│   ├── auth.json
│   ├── raw/
│   ├── sleep.csv
│   ├── steps.csv
│   └── blood_pressure.csv
├── withings/
│   ├── auth.json
│   ├── raw/
│   ├── body_measures.csv
│   └── workouts.csv
└── generated/
    └── daily_context.md
```

Raw API responses, normalized CSVs, generated context, OAuth tokens, and personal health data stay outside this repository.

## Boundaries

Ingestion owns:

- data fetching
- plugins
- normalization
- deduplication
- aggregation
- daily state construction
- context rendering

Ingestion does not own:

- coaching logic
- motivational summaries
- long-term interpretation
- task planning
- personal advice

Those belong to the assistant/review layer.

## Development

Install dependencies:

```sh
poetry install
```

Run CLI from Poetry:

```sh
poetry run ingest --help
poetry run ingest today
poetry run ingest day 2026-05-29
```

Run tests:

```sh
poetry run pytest
```

Build package:

```sh
poetry build
```
