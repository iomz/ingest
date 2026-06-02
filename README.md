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

Current code fetches Withings data, writes local records, builds a `DailyState`,
and renders AI-readable daily context. Future OpenAI API calls and Brain vault
writes belong after rendered context.

## Commands

Primary daily workflow:

```sh
ingest today
```

Specific date:

```sh
ingest today --date 2026-05-29
```

Source maintenance:

```sh
ingest sync withings
ingest sync all
ingest backfill withings --from 2024-01-01
```

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
```

Run tests:

```sh
poetry run pytest
```

Build package:

```sh
poetry build
```
