from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

import anyio

from ingest.config import AppConfig, load_config
from ingest.context import (
    build_daily_state,
    generate_daily_context,
    render_daily_terminal_context,
)
from ingest.plugins import hevy, suunto, vitalsync, withings
from ingest import prompts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ingest")
    parser.add_argument(
        "--config",
        default=None,
        type=Path,
        help="Path to config file. Defaults to XDG_CONFIG_HOME/ingest/config.toml.",
    )

    subparsers = parser.add_subparsers(dest="source", required=True)

    today_parser = subparsers.add_parser("today", help="Gather data and render context for today.")
    _add_daily_options(today_parser)

    day_parser = subparsers.add_parser("day", help="Gather data and render context for a date.")
    day_parser.add_argument("target_date", type=_date_arg, help="Target date in YYYY-MM-DD format.")
    _add_daily_options(day_parser)

    yesterday_parser = subparsers.add_parser("yesterday", help="Gather data and render context for yesterday.")
    _add_daily_options(yesterday_parser)

    backfill_parser = subparsers.add_parser("backfill", help="Backfill historical data.")
    backfill_subparsers = backfill_parser.add_subparsers(dest="command", required=True)
    withings_backfill_parser = backfill_subparsers.add_parser("withings", help="Backfill Withings measurements.")
    withings_backfill_parser.add_argument(
        "--from",
        dest="from_date",
        required=True,
        type=_date_arg,
        help="Historical start date in YYYY-MM-DD format.",
    )
    withings_backfill_parser.add_argument(
        "--end-date",
        type=_date_arg,
        help="Historical end date in YYYY-MM-DD format. Defaults to today.",
    )

    sync_parser = subparsers.add_parser("sync", help="Run daily incremental sync.")
    sync_subparsers = sync_parser.add_subparsers(dest="command", required=True)
    sync_subparsers.add_parser("hevy", help="Sync Hevy workouts from CSV export.")
    sync_subparsers.add_parser("suunto", help="Sync Suunto activities through suuntool.")
    sync_subparsers.add_parser("vitalsync", help="Sync Apple Health records through Vitalsync.")
    sync_subparsers.add_parser("withings", help="Sync recent Withings measurements.")
    sync_subparsers.add_parser("all", help="Sync recent data from all configured sources.")

    import_parser = subparsers.add_parser("import", help="Import exported source data.")
    import_subparsers = import_parser.add_subparsers(dest="command", required=True)
    hevy_import_parser = import_subparsers.add_parser("hevy", help="Import Hevy workout CSV export.")
    hevy_import_parser.add_argument("--csv", required=True, type=Path, help="Path to Hevy workout CSV export.")

    auth_parser = subparsers.add_parser("auth", help="Authentication helper commands.")
    auth_subparsers = auth_parser.add_subparsers(dest="service", required=True)
    auth_subparsers.add_parser("hevy", help="Log in to Hevy and save the Playwright browser session.")
    withings_auth_parser = auth_subparsers.add_parser("withings", help="Withings OAuth helpers.")
    withings_auth_parser.add_argument("--client-id", help="Withings OAuth client id.")
    withings_auth_parser.add_argument("--client-secret", help="Withings OAuth client secret.")
    withings_auth_parser.add_argument(
        "--redirect-uri",
        help="Registered Withings redirect URI. Defaults to local callback.",
    )
    withings_auth_parser.add_argument(
        "--no-local-callback",
        action="store_true",
        help="Do not start the local OAuth callback server; prompt for pasted code instead.",
    )
    withings_auth_parser.add_argument(
        "--callback-timeout-seconds",
        type=int,
        default=withings.LOCAL_CALLBACK_TIMEOUT_SECONDS,
        help="Seconds to wait for the local OAuth callback.",
    )
    withings_auth_subparsers = withings_auth_parser.add_subparsers(dest="command", required=False)
    withings_auth_url_parser = withings_auth_subparsers.add_parser(
        "auth-url",
        help="Print a Withings OAuth URL with metrics and activity scopes.",
    )
    withings_auth_url_parser.add_argument("--redirect-uri", required=True, help="Registered Withings redirect URI.")
    withings_auth_url_parser.add_argument("--state", default="ingest", help="OAuth state value.")
    withings_auth_url_parser.add_argument("--client-id", help="Withings OAuth client id.")
    withings_exchange_parser = withings_auth_subparsers.add_parser(
        "exchange-code",
        help="Exchange a Withings OAuth code and save tokens.",
    )
    withings_exchange_parser.add_argument("--redirect-uri", required=True, help="Registered Withings redirect URI.")
    withings_exchange_parser.add_argument("--code", required=True, help="Authorization code from the redirect URL.")
    withings_exchange_parser.add_argument("--client-id", help="Withings OAuth client id.")
    withings_exchange_parser.add_argument("--client-secret", help="Withings OAuth client secret.")
    vitalsync_auth_parser = auth_subparsers.add_parser("vitalsync", help="Vitalsync token helpers.")
    vitalsync_auth_subparsers = vitalsync_auth_parser.add_subparsers(dest="command", required=True)
    vitalsync_register_parser = vitalsync_auth_subparsers.add_parser(
        "register-client",
        help="Register ingest as a Vitalsync read client with a pairing token.",
    )
    vitalsync_register_parser.add_argument("--pairing-token", help="One-time Vitalsync pairing token.")
    vitalsync_register_parser.add_argument(
        "--client-label",
        default="ingest",
        help="Label stored by the Vitalsync receiver.",
    )
    vitalsync_auth_subparsers.add_parser(
        "refresh-token",
        help="Refresh and save the configured Vitalsync access token.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.source == "backfill" and args.command == "withings":
        written_paths = _run_explicit_sync(
            config,
            "withings",
            lambda: withings.backfill(config, start_date=args.from_date, end_date=args.end_date),
        )
        for path in written_paths:
            print(path)
        return 0

    if args.source == "sync" and args.command == "withings":
        written_paths = _run_explicit_sync(config, "withings", lambda: withings.sync(config))
        for path in written_paths:
            print(path)
        return 0

    if args.source == "sync" and args.command == "hevy":
        written_paths = _run_explicit_sync(config, "hevy", lambda: hevy.sync(config))
        for path in written_paths:
            print(path)
        return 0

    if args.source == "sync" and args.command == "suunto":
        written_paths = _run_explicit_sync(config, "suunto", lambda: suunto.sync(config))
        for path in written_paths:
            print(path)
        return 0

    if args.source == "sync" and args.command == "vitalsync":
        written_paths = _run_explicit_sync(config, "vitalsync", lambda: vitalsync.sync(config))
        for path in written_paths:
            print(path)
        return 0

    if args.source == "sync" and args.command == "all":
        written_paths = _sync_all(config)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "import" and args.command == "hevy":
        written_paths = hevy.import_workouts_csv(config, args.csv)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "auth" and args.service == "hevy":
        email = prompts.text("Hevy email or username")
        password = prompts.password("Hevy password")
        hevy.authenticate(config, email=email, password=password)
        print(f"Hevy browser session ready: {config.hevy.browser_dir}")
        return 0

    if args.source == "auth" and args.service == "withings" and args.command is None:
        _auth_withings(config, args)
        print(config.withings.auth_state_path)
        return 0

    if args.source == "auth" and args.service == "withings" and args.command == "auth-url":
        client_id = args.client_id or config.withings.client_id or prompts.text("Withings client id")
        print(withings.authorization_url(config, redirect_uri=args.redirect_uri, state=args.state, client_id=client_id))
        return 0

    if args.source == "auth" and args.service == "withings" and args.command == "exchange-code":
        client_id = args.client_id or config.withings.client_id or prompts.text("Withings client id")
        client_secret = (
            args.client_secret or config.withings.client_secret or prompts.password("Withings client secret")
        )
        withings.exchange_authorization_code(
            config,
            code=args.code,
            redirect_uri=args.redirect_uri,
            client_id=client_id,
            client_secret=client_secret,
        )
        print(config.withings.auth_state_path)
        return 0

    if args.source == "auth" and args.service == "vitalsync" and args.command == "register-client":
        pairing_token = args.pairing_token or prompts.password("Vitalsync pairing token")
        client_label = args.client_label or prompts.text("Vitalsync client label", default="ingest")
        vitalsync.register_client(
            config,
            pairing_token=pairing_token,
            client_label=client_label,
        )
        print(config.vitalsync.auth_state_path)
        return 0

    if args.source == "auth" and args.service == "vitalsync" and args.command == "refresh-token":
        vitalsync.refresh_configured_access_token(config)
        print(config.vitalsync.auth_state_path)
        return 0

    if args.source == "today":
        target = _local_today(config)
        _sync_for_daily_context(config, args.sync)
        if args.markdown:
            return _print_daily_context(config, target)
        return _print_daily_terminal_context(config, target)

    if args.source == "day":
        _sync_for_daily_context(config, args.sync)
        if args.markdown:
            return _print_daily_context(config, args.target_date)
        return _print_daily_terminal_context(config, args.target_date)

    if args.source == "yesterday":
        _sync_for_daily_context(config, args.sync)
        target = _local_today(config) - timedelta(days=1)
        if args.markdown:
            return _print_daily_context(config, target)
        return _print_daily_terminal_context(config, target)

    parser.error("Unsupported command.")
    return 2


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be in YYYY-MM-DD format") from exc


def _local_today(config: AppConfig) -> date:
    return datetime.now(config.timezone).date()


def _add_daily_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Run `ingest sync all` before rendering context.",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Print Markdown instead of the terminal view.",
    )


def _auth_withings(config: AppConfig, args: argparse.Namespace) -> None:
    client_id = args.client_id or config.withings.client_id or prompts.text("Withings client id")
    client_secret = args.client_secret or config.withings.client_secret or prompts.password("Withings client secret")
    default_redirect_uri = "http://127.0.0.1:8000/callback"
    redirect_uri = args.redirect_uri or prompts.text("Withings redirect URI", default=default_redirect_uri)
    auth_url = withings.authorization_url(config, redirect_uri=redirect_uri, client_id=client_id)
    code = ""
    if not args.no_local_callback and _local_http_redirect(redirect_uri):
        use_local_callback = prompts.confirm(
            f"Open browser and wait for local callback at {redirect_uri}?",
            default=True,
        )
        if use_local_callback:
            print(auth_url)
            code = withings.capture_local_oauth_code(
                auth_url,
                redirect_uri,
                timeout_seconds=args.callback_timeout_seconds,
            )
    if not code:
        print(auth_url)
        pasted = prompts.text("Paste Withings redirect URL or authorization code")
        code = withings.parse_authorization_code(pasted)
    withings.exchange_authorization_code(
        config,
        code=code,
        redirect_uri=redirect_uri,
        client_id=client_id,
        client_secret=client_secret,
    )


def _local_http_redirect(redirect_uri: str) -> bool:
    parsed = urlparse(redirect_uri)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port is not None


def _sync_for_daily_context(config: AppConfig, enabled: bool) -> None:
    if enabled:
        _sync_all(config)


def _sync_all(config: AppConfig) -> list[Path]:
    return anyio.run(_sync_all_async, config)


async def _sync_all_async(config: AppConfig) -> list[Path]:
    config_update_lock = anyio.Lock()
    plugins: list[tuple[str, Callable[[], Awaitable[list[Path]]]]] = []
    if _plugin_sync_ready(config, "hevy", explicit=False):
        plugins.append(("hevy", lambda: _run_sync_source(hevy.sync, config)))
    if _plugin_sync_ready(config, "suunto", explicit=False):
        plugins.append(("suunto", lambda: suunto.sync_async(config)))
    if _plugin_sync_ready(config, "vitalsync", explicit=False):
        plugins.append(("vitalsync", lambda: _run_sync_source(vitalsync.sync, config, config_update_lock)))
    if _plugin_sync_ready(config, "withings", explicit=False):
        plugins.append(("withings", lambda: _run_sync_source(withings.sync, config, config_update_lock)))
    results: dict[str, list[Path]] = {}
    errors: dict[str, Exception | SystemExit] = {}

    async def run_plugin(name: str, sync_plugin: Callable[[], Awaitable[list[Path]]]) -> None:
        try:
            results[name] = await sync_plugin()
        except (Exception, SystemExit) as exc:
            errors[name] = exc

    async with anyio.create_task_group() as task_group:
        for name, sync_plugin in plugins:
            task_group.start_soon(run_plugin, name, sync_plugin)

    for name, _sync_plugin in plugins:
        if name in errors:
            raise errors[name]

    return [path for name, _sync_plugin in plugins for path in results[name]]


def _run_explicit_sync(config: AppConfig, plugin: str, sync_func: Callable[[], list[Path]]) -> list[Path]:
    if not _plugin_sync_ready(config, plugin, explicit=True):
        return []
    return sync_func()


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
    if plugin == "withings":
        if config.withings.access_token:
            return ""
        if config.withings.refresh_token and config.withings.client_id and config.withings.client_secret:
            return ""
        return f"run `ingest auth withings exchange-code`; missing auth state at {config.withings.auth_state_path}"
    if plugin == "hevy":
        if not config.hevy.configured:
            return "missing [plugin.hevy] config table"
        return ""
    if plugin == "suunto":
        if not config.suunto.configured:
            return "missing [plugin.suunto] config table"
        return "" if shutil.which(config.suunto.command) else f"command not found: {config.suunto.command}"
    if plugin == "vitalsync":
        if config.vitalsync.access_token:
            return ""
        if config.vitalsync.refresh_token and config.vitalsync.client_id:
            return ""
        return f"run `ingest auth vitalsync register-client`; missing auth state at {config.vitalsync.auth_state_path}"
    return f"unknown plugin {plugin!r}"


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


def _print_daily_context(config: AppConfig, target: date) -> int:
    path = generate_daily_context(config, target)
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def _print_daily_terminal_context(config: AppConfig, target: date) -> int:
    render_daily_terminal_context(build_daily_state(config, target), ui=config.ui)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
