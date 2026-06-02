# Rename To ingest

Old name: `life-log-sync`

New name: `ingest`

## Reason

Project no longer means sync tool. It is AI input pipeline.

`ingest` collects, normalizes, integrates, and renders personal state so another
intelligence can interpret it.

`ingest` prepares personal state for interpretation; it does not replace the interpreter.

## Command Change

Old primary workflow:

```sh
life-log-sync context today
```

New primary workflow:

```sh
ingest today
```

No compatibility alias is kept. `ingest today` is only daily context command.

Withings OAuth helper commands moved under `oauth`:

```sh
ingest oauth withings auth-url
ingest oauth withings exchange-code
```

## File Changes

Default config path changes:

```text
${XDG_CONFIG_HOME:-~/.config}/life-log-sync.toml
```

to:

```text
${XDG_CONFIG_HOME:-~/.config}/ingest.toml
```

Default application data directory changes:

```text
${XDG_DATA_HOME:-~/.local/share}/life-log-sync
```

to:

```text
${XDG_DATA_HOME:-~/.local/share}/ingest
```

Generated context changes from:

```text
generated/today_context.md
```

to:

```text
generated/daily_context.md
```

## Source Model

Strava source code has been removed. Current source support is Withings.

Future sources should contribute generic normalized records into daily state.
They should not assume Strava-like activities.

## Future Direction

Next pipeline stages may call OpenAI API, produce daily review, and write that
review to Brain vault. Those stages remain separate from ingestion.
