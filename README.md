# life-log-sync

Scripts for syncing personal activity data.

## Architecture

`life-log-sync` keeps code, configuration, and runtime data separate.

```text
life-log-sync repository = source code, tests, docs, config templates
configuration file = credentials and local settings
application data directory = telemetry, cache, generated files
```

The package is split into small responsibilities:

- `life_log_sync.app_data` resolves and writes inside the application data directory.
- `life_log_sync.config` loads TOML config and persists refreshed tokens.
- `life_log_sync.sources` contains service-specific sync code.
- `life_log_sync.cli` wires commands together.

This keeps Strava simple today while leaving a clear place for Withings,
Superlist, and other sources later.

Sync and context generation are separate:

- `life-log-sync backfill withings` fetches historical Withings data.
- `life-log-sync backfill strava` fetches historical Strava data.
- `life-log-sync sync withings` fetches a recent Withings window for daily use.
- `life-log-sync context today` reads local CSV data only and does not call external APIs.

## Configuration

`life-log-sync` keeps personal configuration and generated data out of this
repository. The default configuration file on Unix-like systems is:

```text
${XDG_CONFIG_HOME:-~/.config}/life-log-sync.toml
```

Copy the example config there and fill in your private values:

```sh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}"
cp config.example.toml "${XDG_CONFIG_HOME:-$HOME/.config}/life-log-sync.toml"
```

Never commit your filled-in config file. It contains OAuth credentials and
refreshed tokens. The repository should contain only `config.example.toml`.

The default application data directory on Unix-like systems is:

```text
${XDG_DATA_HOME:-~/.local/share}/life-log-sync
```

You can override the application data directory in the config file:

```toml
[app]
data_dir = "/path/to/life-log-sync-data"
```

Generated data is written under the resolved application data directory:

```text
${XDG_DATA_HOME:-~/.local/share}/life-log-sync/
├── strava/
│   ├── raw/
│   │   └── <activity-id>.json
│   └── activities.csv
├── withings/
│   ├── raw/
│   │   ├── body_measures_backfill.json
│   │   └── body_measures_recent.json
│   └── body_measures.csv
└── generated/
    └── today_context.md
```

Generate today's context from synced data:

```sh
life-log-sync context today
```

For a specific date:

```sh
life-log-sync context today --date 2026-05-29
```

## Installation

Install the command with pipx:

```sh
pipx install -e .
```

Then run:

```sh
life-log-sync --help
```

For Poetry-based development, install Poetry if it is not already available:

```sh
python3 -m pip install poetry
```

Install dependencies:

```sh
poetry install
```

Run the CLI from the Poetry environment:

```sh
poetry run life-log-sync --help
```

Build an installable package:

```sh
poetry build
```

Install the project into another environment from the repository:

```sh
python3 -m pip install .
```

## Strava

The Strava sync reads recent activities and writes both raw JSON and a
normalized CSV into the application data directory.

The script refreshes the Strava access token automatically at startup and
writes the latest `access_token`, `refresh_token`, and `expires_at` back to
the configured `life-log-sync.toml`.

The authorization must include Strava's `activity:read` scope. A token that can
read `/athlete` is not enough for `/athlete/activities`; Strava returns
`activity:read_permission` as missing when that scope is absent.

For one-off use, you can set `strava.access_token` in the config file when
refresh credentials are not configured.

Backfill historical activities:

```sh
life-log-sync backfill strava --from 2024-01-01
```

Run daily incremental sync:

```sh
life-log-sync sync strava
```

Without installing the console command, run through Poetry:

```sh
poetry run life-log-sync sync strava
```

The legacy `life-log-sync strava sync` command is still accepted.

## Withings

The Withings sync reads recent body measurements and writes both raw JSON and a
normalized CSV into the application data directory.

Withings requires OAuth user tokens. `client_id` and `client_secret` identify
the app, but user data access requires `withings.refresh_token` or
`withings.access_token` in the config file.

Backfill historical measurements:

```sh
life-log-sync backfill withings --from 2024-01-01
```

Run daily incremental sync:

```sh
life-log-sync sync withings
```

The daily sync uses a conservative recent window and merges rows into
`withings/body_measures.csv`. Backfill uses fixed date windows and the same
merge path, so rerunning either command does not duplicate normalized rows.

The legacy command is still accepted:

```sh
life-log-sync withings sync
```
