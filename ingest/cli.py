from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from ingest.config import load_config
from ingest.context import (
    generate_daily_context,
    measures_for_date,
    read_withings_activities,
    read_withings_measures,
    withings_activities_for_date,
)
from ingest.sources import withings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ingest")
    parser.add_argument(
        "--config",
        default=None,
        type=Path,
        help="Path to config file. Defaults to XDG_CONFIG_HOME/ingest.toml.",
    )

    subparsers = parser.add_subparsers(dest="source", required=True)

    today_parser = subparsers.add_parser("today", help="Gather data and render generated/daily_context.md.")
    today_parser.add_argument(
        "--date",
        dest="target_date",
        type=_date_arg,
        help="Target date in YYYY-MM-DD format. Defaults to today.",
    )

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
    sync_subparsers.add_parser("withings", help="Sync recent Withings measurements.")
    sync_subparsers.add_parser("all", help="Sync recent data from all configured sources.")

    oauth_parser = subparsers.add_parser("oauth", help="OAuth helper commands.")
    oauth_subparsers = oauth_parser.add_subparsers(dest="service", required=True)
    withings_oauth_parser = oauth_subparsers.add_parser("withings", help="Withings OAuth helpers.")
    withings_oauth_subparsers = withings_oauth_parser.add_subparsers(dest="command", required=True)
    withings_auth_url_parser = withings_oauth_subparsers.add_parser(
        "auth-url",
        help="Print a Withings OAuth URL with metrics and activity scopes.",
    )
    withings_auth_url_parser.add_argument("--redirect-uri", required=True, help="Registered Withings redirect URI.")
    withings_auth_url_parser.add_argument("--state", default="ingest", help="OAuth state value.")
    withings_exchange_parser = withings_oauth_subparsers.add_parser(
        "exchange-code",
        help="Exchange a Withings OAuth code and save tokens.",
    )
    withings_exchange_parser.add_argument("--redirect-uri", required=True, help="Registered Withings redirect URI.")
    withings_exchange_parser.add_argument("--code", required=True, help="Authorization code from the redirect URL.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.source == "backfill" and args.command == "withings":
        written_paths = withings.backfill(config, start_date=args.from_date, end_date=args.end_date)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "sync" and args.command == "withings":
        written_paths = withings.sync(config)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "sync" and args.command == "all":
        written_paths = withings.sync(config)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "oauth" and args.service == "withings" and args.command == "auth-url":
        print(withings.authorization_url(config, redirect_uri=args.redirect_uri, state=args.state))
        return 0

    if args.source == "oauth" and args.service == "withings" and args.command == "exchange-code":
        withings.exchange_authorization_code(config, code=args.code, redirect_uri=args.redirect_uri)
        print(config.path)
        return 0

    if args.source == "today":
        target = args.target_date or date.today()
        withings_measures = measures_for_date(read_withings_measures(config.withings.measures_csv), target)
        withings_workouts = withings_activities_for_date(read_withings_activities(config.withings.workouts_csv), target)
        if target == date.today() or not withings_measures or not withings_workouts:
            withings.sync(config)
        path = generate_daily_context(config, target)
        print(path)
        print(path.read_text(encoding="utf-8"), end="")
        return 0

    parser.error("Unsupported command.")
    return 2


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be in YYYY-MM-DD format") from exc


if __name__ == "__main__":
    raise SystemExit(main())
