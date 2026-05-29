from __future__ import annotations

import argparse
from pathlib import Path

from life_log_sync.config import load_config
from life_log_sync.sources import strava


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="life-log-sync")
    parser.add_argument(
        "--config",
        default=None,
        type=Path,
        help="Path to config file. Defaults to XDG_CONFIG_HOME/life-log-sync.toml.",
    )

    subparsers = parser.add_subparsers(dest="source", required=True)

    strava_parser = subparsers.add_parser("strava", help="Sync Strava data.")
    strava_subparsers = strava_parser.add_subparsers(dest="command", required=True)
    strava_subparsers.add_parser("sync", help="Fetch Strava activities into the application data directory.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.source == "strava" and args.command == "sync":
        written_paths = strava.sync(config)
        for path in written_paths:
            print(path)
        return 0

    parser.error("Unsupported command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
