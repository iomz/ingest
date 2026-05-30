from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from life_log_sync.config import load_config
from life_log_sync.context import generate_today_context
from life_log_sync.sources import strava, withings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="life-log-sync")
    parser.add_argument(
        "--config",
        default=None,
        type=Path,
        help="Path to config file. Defaults to XDG_CONFIG_HOME/life-log-sync.toml.",
    )

    subparsers = parser.add_subparsers(dest="source", required=True)

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
    strava_backfill_parser = backfill_subparsers.add_parser("strava", help="Backfill Strava activities.")
    strava_backfill_parser.add_argument(
        "--from",
        dest="from_date",
        required=True,
        type=_date_arg,
        help="Historical start date in YYYY-MM-DD format.",
    )
    strava_backfill_parser.add_argument(
        "--end-date",
        type=_date_arg,
        help="Historical end date in YYYY-MM-DD format. Defaults to today.",
    )

    sync_parser = subparsers.add_parser("sync", help="Run daily incremental sync.")
    sync_subparsers = sync_parser.add_subparsers(dest="command", required=True)
    sync_subparsers.add_parser("withings", help="Sync recent Withings measurements.")
    sync_subparsers.add_parser("strava", help="Sync recent Strava activities.")

    strava_parser = subparsers.add_parser("strava", help="Sync Strava data.")
    strava_subparsers = strava_parser.add_subparsers(dest="command", required=True)
    strava_subparsers.add_parser("sync", help="Fetch Strava activities into the application data directory.")

    withings_parser = subparsers.add_parser("withings", help="Sync Withings body measurements.")
    withings_subparsers = withings_parser.add_subparsers(dest="command", required=True)
    withings_subparsers.add_parser("sync", help="Fetch Withings body measurements into the application data directory.")

    context_parser = subparsers.add_parser("context", help="Generate context files from synced data.")
    context_subparsers = context_parser.add_subparsers(dest="command", required=True)
    today_parser = context_subparsers.add_parser("today", help="Generate generated/today_context.md.")
    today_parser.add_argument(
        "--date",
        dest="target_date",
        type=_date_arg,
        help="Target date in YYYY-MM-DD format. Defaults to today.",
    )

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

    if args.source == "backfill" and args.command == "strava":
        written_paths = strava.backfill(config, start_date=args.from_date, end_date=args.end_date)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "sync" and args.command == "withings":
        written_paths = withings.sync(config)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "sync" and args.command == "strava":
        written_paths = strava.sync(config)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "strava" and args.command == "sync":
        written_paths = strava.sync(config)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "withings" and args.command == "sync":
        written_paths = withings.sync(config)
        for path in written_paths:
            print(path)
        return 0

    if args.source == "context" and args.command == "today":
        path = generate_today_context(config, args.target_date)
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
