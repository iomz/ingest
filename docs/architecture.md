# Architecture

## Purpose

`ingest` is a local-first input pipeline for AI-assisted self-review.

It collects personal data from sources, stores raw and normalized records
outside the repository, builds a coherent daily state, and renders context an AI
assistant can read.

It is not a dashboard, coach, journal plugin, database server, or meaning-making
layer.

## Core Principle

Separate code, telemetry, context, and narrative.

- Code: `ingest` repository
- Telemetry: application data directory
- Context: generated AI-readable files
- Narrative: Brain vault / personal journals

## Data Flow

```text
Sources
  -> Raw Source Data
  -> Normalized Records
  -> DailyState
  -> Rendered Context
  -> AI Review
  -> Brain
```

Current sources:

- Withings
- Hevy
- Suunto through the optional external `suuntool` adapter
- Vitalsync Apple Health sleep records

Likely future sources:

- manual measurements
- workout tracking app / Setlist
- weather
- Obsidian or local files

Source adapters should contribute generic normalized records. They should not
assume Strava-style activities, social feeds, segments, or athlete concepts.

## Repository

Repository contains:

- source code
- tests
- documentation
- configuration examples

Repository must not contain:

- OAuth tokens
- raw API responses
- generated CSV files
- generated context files
- personal health or activity data

## Application Data

Default location:

```text
${XDG_DATA_HOME:-~/.local/share}/ingest
```

Example:

```text
ingest/
├── withings/
│   ├── raw/
│   ├── body_measures.csv
│   ├── sleep.csv
│   └── workouts.csv
├── hevy/
│   ├── browser/
│   ├── raw/
│   ├── workouts.csv
│   └── sets.csv
├── suunto/
│   ├── raw/
│   └── workouts.csv
└── generated/
    └── daily_context.md
```

This directory contains telemetry and generated artifacts. It may be deleted and
regenerated where sources allow.

## Daily State

`DailyState` is structured normalized data for one date. It is internal to the
ingestion layer.

Rendered daily context is disposable AI-readable text derived from `DailyState`.
It is not a final daily review.

## Boundaries

`ingest` owns:

- fetching source data
- normalizing source records
- deduplicating and merging records
- aggregating daily state
- rendering context for an assistant

`ingest` does not own:

- coaching logic
- motivational summaries
- long-term interpretation
- task planning
- personal advice

Those belong to the assistant/review layer.

## Brain Vault

Brain vault and personal journals are narrative layers. `ingest` may eventually
write an AI-produced review there, but raw telemetry and intermediate generated
context should stay in application data.

## Guiding Sentence

`ingest` prepares personal state for interpretation; it does not replace the interpreter.
