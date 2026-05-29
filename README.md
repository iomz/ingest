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
└── generated/
    └── today_context.md
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

Run:

```sh
life-log-sync strava sync
```

Without installing the console command, run through Poetry:

```sh
poetry run life-log-sync strava sync
```
