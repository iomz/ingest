from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from life_log_sync.cli import build_parser, main


class CliTest(unittest.TestCase):
    def test_parser_accepts_new_sync_and_backfill_commands(self) -> None:
        parser = build_parser()

        sync_args = parser.parse_args(["sync", "withings"])
        backfill_args = parser.parse_args(["backfill", "withings", "--from", "2026-01-01"])
        strava_backfill_args = parser.parse_args(["backfill", "strava", "--from", "2026-01-01"])

        self.assertEqual(sync_args.source, "sync")
        self.assertEqual(sync_args.command, "withings")
        self.assertEqual(backfill_args.source, "backfill")
        self.assertEqual(backfill_args.command, "withings")
        self.assertEqual(backfill_args.from_date.isoformat(), "2026-01-01")
        self.assertEqual(strava_backfill_args.source, "backfill")
        self.assertEqual(strava_backfill_args.command, "strava")
        self.assertEqual(strava_backfill_args.from_date.isoformat(), "2026-01-01")

    def test_context_today_prints_generated_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "life-log-sync.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[strava]
access_token = "access"
""".strip(),
                encoding="utf-8",
            )
            csv_path = data_dir / "strava/activities.csv"
            csv_path.parent.mkdir(parents=True)
            csv_path.write_text(
                "\n".join(
                    [
                        "id,start_date_local,name,sport_type,distance_km,moving_time_min",
                        "1,2026-05-29T06:30:00Z,Morning Run,Run,5.00,30.00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "context",
                        "today",
                        "--date",
                        "2026-05-29",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            context_path = data_dir / "generated/today_context.md"
            self.assertTrue(output.startswith(f"{context_path}\n"))
            self.assertIn("# Today Context - 2026-05-29", output)
            self.assertIn("Morning Run", output)
            self.assertEqual(output, f"{context_path}\n{context_path.read_text(encoding='utf-8')}")


if __name__ == "__main__":
    unittest.main()
