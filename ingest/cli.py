from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

import anyio
import typer
from typer._click import exceptions as click_exceptions

from ingest.config import AppConfig, load_config
from ingest.context import (
    build_daily_state,
    generate_daily_context,
    render_daily_terminal_context,
)
from ingest.plugins import PluginCliRegistry, PluginManifest, PluginSyncScope, load_plugin, load_repository_plugins
from ingest.sync_scope import build_plugin_sync_scope

app = typer.Typer(help="Collect and render personal data for AI-assisted self-review.")
sync_app = typer.Typer(help="Run daily incremental sync.")
import_app = typer.Typer(help="Import exported source data.")
auth_app = typer.Typer(help="Authentication helper commands.")

app.add_typer(sync_app, name="sync")
app.add_typer(import_app, name="import")
app.add_typer(auth_app, name="auth")


@app.callback()
def _root(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to config file. Defaults to XDG_CONFIG_HOME/ingest/config.toml.",
    ),
) -> None:
    ctx.obj = load_config(config)


@app.command()
def today(
    ctx: typer.Context,
    sync: bool = typer.Option(False, "--sync", help="Run `ingest sync all` before rendering context."),
    markdown: bool = typer.Option(False, "--markdown", help="Print Markdown instead of the terminal view."),
) -> None:
    config = _config(ctx)
    target = _local_today(config)
    _sync_for_daily_context(config, sync)
    raise typer.Exit(_print_daily_context(config, target) if markdown else _print_daily_terminal_context(config, target))


@app.command()
def day(
    ctx: typer.Context,
    target_date: str = typer.Argument(..., help="Target date in YYYY-MM-DD format."),
    sync: bool = typer.Option(False, "--sync", help="Run `ingest sync all` before rendering context."),
    markdown: bool = typer.Option(False, "--markdown", help="Print Markdown instead of the terminal view."),
) -> None:
    config = _config(ctx)
    target = _date_arg(target_date)
    _sync_for_daily_context(config, sync)
    raise typer.Exit(
        _print_daily_context(config, target) if markdown else _print_daily_terminal_context(config, target)
    )


@app.command()
def yesterday(
    ctx: typer.Context,
    sync: bool = typer.Option(False, "--sync", help="Run `ingest sync all` before rendering context."),
    markdown: bool = typer.Option(False, "--markdown", help="Print Markdown instead of the terminal view."),
) -> None:
    config = _config(ctx)
    _sync_for_daily_context(config, sync)
    target = _local_today(config) - timedelta(days=1)
    raise typer.Exit(_print_daily_context(config, target) if markdown else _print_daily_terminal_context(config, target))


@sync_app.command("all")
def sync_all(ctx: typer.Context) -> None:
    _print_paths(_sync_all(_config(ctx)))


def main(argv: list[str] | None = None) -> int:
    command = typer.main.get_command(app)
    try:
        command.main(args=argv, prog_name="ingest", standalone_mode=False)
    except click_exceptions.Exit as exc:
        return int(exc.exit_code)
    except click_exceptions.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click_exceptions.Abort:
        print("Aborted!", file=sys.stderr)
        return 1
    return 0


def _config(ctx: typer.Context) -> AppConfig:
    return ctx.obj


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("date must be in YYYY-MM-DD format") from exc


def _optional_date_arg(value: str | None) -> date | None:
    return _date_arg(value) if value is not None else None


def _local_today(config: AppConfig) -> date:
    return datetime.now(config.timezone).date()


def _sync_for_daily_context(config: AppConfig, enabled: bool) -> None:
    if enabled:
        _sync_all(config)


def _sync_plugin(config: AppConfig, plugin: str) -> list[Path]:
    manifest = load_plugin(plugin)
    if manifest.sync is None and manifest.sync_scoped is None:
        raise SystemExit(f"Plugin {plugin!r} does not support sync.")
    scope = build_plugin_sync_scope(config, plugin)
    return _run_explicit_sync(config, plugin, lambda: _run_manifest_sync(manifest, config, scope))


def _sync_all(config: AppConfig) -> list[Path]:
    return anyio.run(_sync_all_async, config)


async def _sync_all_async(config: AppConfig) -> list[Path]:
    config_update_lock = anyio.Lock()
    plugins: list[tuple[str, PluginManifest, Callable[[], Awaitable[list[Path]]]]] = []
    for manifest in load_repository_plugins():
        if not _plugin_sync_ready(config, manifest.name, explicit=False):
            continue
        if manifest.sync is None and manifest.sync_scoped is None:
            continue
        lock = config_update_lock if manifest.serial_sync else None
        scope = build_plugin_sync_scope(config, manifest.name)
        plugins.append(
            (
                manifest.name,
                manifest,
                lambda manifest=manifest, scope=scope, lock=lock: _run_sync_source(
                    lambda sync_config: _run_manifest_sync(manifest, sync_config, scope),
                    config,
                    lock,
                ),
            )
        )

    results: dict[str, list[Path]] = {}
    errors: dict[str, Exception | SystemExit] = {}

    async def run_plugin(name: str, sync_plugin: Callable[[], Awaitable[list[Path]]]) -> None:
        try:
            results[name] = await sync_plugin()
        except (Exception, SystemExit) as exc:
            errors[name] = exc

    async with anyio.create_task_group() as task_group:
        for name, _manifest, sync_plugin in plugins:
            task_group.start_soon(run_plugin, name, sync_plugin)

    for name, _manifest, _sync_plugin in plugins:
        if name in errors:
            raise errors[name]

    return [path for name, _manifest, _sync_plugin in plugins for path in results[name]]


def _run_explicit_sync(config: AppConfig, plugin: str, sync_func: Callable[[], list[Path]]) -> list[Path]:
    if not _plugin_sync_ready(config, plugin, explicit=True):
        return []
    return sync_func()


def _run_manifest_sync(manifest: PluginManifest, config: AppConfig, scope: PluginSyncScope) -> list[Path]:
    if manifest.sync_scoped is not None:
        return manifest.sync_scoped(config, scope)
    if manifest.sync is None:
        raise SystemExit(f"Plugin {manifest.name!r} does not support sync.")
    return manifest.sync(config)


def _plugin_sync_ready(config: AppConfig, plugin: str, *, explicit: bool) -> bool:
    if not _plugin_enabled(config, plugin):
        if explicit:
            _sync_warning(f"plugin.{plugin} is disabled; skipping.")
        return False
    reason = _plugin_unavailable_reason(config, plugin)
    if reason:
        _sync_warning(f"plugin.{plugin} unavailable; skipping: {reason}")
        return False
    return True


def _plugin_enabled(config: AppConfig, plugin: str) -> bool:
    return bool(getattr(getattr(config, plugin), "enabled"))


def _plugin_unavailable_reason(config: AppConfig, plugin: str) -> str:
    manifest = load_plugin(plugin)
    if manifest.sync_unavailable_reason is None:
        return ""
    return manifest.sync_unavailable_reason(config)


def _sync_warning(message: str) -> None:
    print(f"ingest sync warning: {message}", file=sys.stderr)


async def _run_sync_source(
    sync_source: Callable[[AppConfig], list[Path]],
    config: AppConfig,
    config_update_lock: anyio.Lock | None = None,
) -> list[Path]:
    if config_update_lock is not None:
        async with config_update_lock:
            return await anyio.to_thread.run_sync(sync_source, config)
    return await anyio.to_thread.run_sync(sync_source, config)


def _print_paths(paths: list[Path]) -> None:
    for path in paths:
        print(path)


def _print_daily_context(config: AppConfig, target: date) -> int:
    path = generate_daily_context(config, target)
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def _print_daily_terminal_context(config: AppConfig, target: date) -> int:
    render_daily_terminal_context(build_daily_state(config, target), ui=config.ui)
    return 0


def _register_plugin_cli() -> None:
    registry = PluginCliRegistry(
        sync_app=sync_app,
        import_app=import_app,
        auth_app=auth_app,
        get_config=_config,
        run_sync=_sync_plugin,
        sync_ready=lambda config, plugin, explicit: _plugin_sync_ready(config, plugin, explicit=explicit),
        sync_scope=build_plugin_sync_scope,
        print_paths=_print_paths,
        date_arg=_date_arg,
        optional_date_arg=_optional_date_arg,
    )
    for manifest in load_repository_plugins():
        if manifest.register_cli is not None:
            manifest.register_cli(registry)


_register_plugin_cli()


if __name__ == "__main__":
    raise SystemExit(main())
