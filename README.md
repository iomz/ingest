# ingest

`ingest` collects, normalizes, and prepares personal data so an AI assistant can
review it.

It is an input pipeline for AI-assisted self-review. It is not a generic health
dashboard, quantified-self UI, coach, task planner, or final interpretation
layer.

`ingest` prepares personal state for interpretation; it does not replace the interpreter.

## Pipeline

```text
Sources
  -> Normalized Records
  -> DailyState
  -> Rendered Context
  -> AI Review
  -> Brain
```

Current code fetches Withings data, imports Hevy workout exports, writes local
records, builds a `DailyState`, and renders AI-readable daily context. Future
OpenAI API calls and Brain vault writes belong after rendered context.

## Derived Metrics

Derived metrics are calculated during report generation and are not persisted.
The Body section keeps measured body composition values first, then a visual
separator and explicit derived metrics rows so downstream LLM consumers can use
computed values without re-deriving them.

BMI uses current weight and height:

```text
BMI = weight_kg / (height_m * height_m)
```

BMR uses the Katch-McArdle equation from fat-free mass:

```text
BMR = 370 + (21.6 * fat_free_mass_kg)
```

Estimated deficit uses observed weight change over the previous 30 days:

```text
weight_change_kg = weight_30_days_ago - current_weight
estimated_deficit_kcal_per_day = (weight_change_kg * 7700) / 30
```

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
ingest sync withings
ingest sync hevy
ingest sync all
ingest backfill withings --from 2024-01-01
```

Hevy import from CSV export:

```sh
ingest import hevy --csv ~/Downloads/hevy-workouts.csv
```

The Hevy public API currently requires Hevy Pro. Without Pro, use the app export:
Profile > Settings > Export & Import Data > Export Data > Export Workouts.
`ingest sync hevy` automates that export with a dedicated Playwright browser
profile stored under the application data directory. On the first run, log in to
Hevy in the opened browser window, then rerun the command.

Withings OAuth helpers:

```sh
ingest oauth withings auth-url --redirect-uri "https://your-registered-callback"
ingest oauth withings exchange-code --redirect-uri "https://your-registered-callback" --code "<code>"
```

## Files

Repository contains code only:

```text
ingest repository = source code, tests, docs, config templates
configuration file = credentials and local settings
application data directory = telemetry, cache, generated files
```

Default config path:

```text
${XDG_CONFIG_HOME:-~/.config}/ingest.toml
```

Create local config:

```sh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}"
cp config.example.toml "${XDG_CONFIG_HOME:-$HOME/.config}/ingest.toml"
```

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
├── withings/
│   ├── raw/
│   ├── body_measures.csv
│   └── workouts.csv
├── hevy/
│   ├── browser/
│   ├── raw/
│   ├── workouts.csv
│   └── sets.csv
└── generated/
    └── daily_context.md
```

Raw API responses, normalized CSVs, generated context, OAuth tokens, and personal
health data stay outside this repository.

## Boundaries

Ingestion owns:

- data fetching
- source adapters
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
