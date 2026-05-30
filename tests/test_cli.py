from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from life_log_sync.cli import build_parser, main


class CliTest(unittest.TestCase):
    def test_parser_accepts_new_sync_and_backfill_commands(self) -> None:
        parser = build_parser()

        sync_args = parser.parse_args(["sync", "withings"])
        sync_all_args = parser.parse_args(["sync", "all"])
        backfill_args = parser.parse_args(["backfill", "withings", "--from", "2026-01-01"])
        strava_backfill_args = parser.parse_args(["backfill", "strava", "--from", "2026-01-01"])

        self.assertEqual(sync_args.source, "sync")
        self.assertEqual(sync_args.command, "withings")
        self.assertEqual(sync_all_args.source, "sync")
        self.assertEqual(sync_all_args.command, "all")
        self.assertEqual(backfill_args.source, "backfill")
        self.assertEqual(backfill_args.command, "withings")
        self.assertEqual(backfill_args.from_date.isoformat(), "2026-01-01")
        self.assertEqual(strava_backfill_args.source, "backfill")
        self.assertEqual(strava_backfill_args.command, "strava")
        self.assertEqual(strava_backfill_args.from_date.isoformat(), "2026-01-01")

    def test_context_today_prints_generated_content_without_sync_when_data_exists(self) -> None:
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
            withings_csv_path = data_dir / "withings/body_measures.csv"
            withings_csv_path.parent.mkdir(parents=True)
            withings_csv_path.write_text(
                "\n".join(
                    [
                        "grpid,date,datetime_local,type,type_name,value,unit",
                        "1,2026-05-29,2026-05-29T06:00:00,1,weight,70.50,kg",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            workouts_csv_path = data_dir / "withings/workouts.csv"
            workouts_csv_path.write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type,dedup_group_id,is_primary",
                        "withings,1,2026-05-29T08:00:00,2026-05-29T08:30:00,30.00,1.00,walk,walk,,true",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                mock.patch("life_log_sync.cli.strava.sync") as strava_sync,
                mock.patch("life_log_sync.cli.withings.sync") as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
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
            strava_sync.assert_not_called()
            withings_sync.assert_not_called()
            output = stdout.getvalue()
            context_path = data_dir / "generated/today_context.md"
            self.assertTrue(output.startswith(f"{context_path}\n"))
            self.assertIn("# Today Context - 2026-05-29", output)
            self.assertIn("Morning Run", output)
            self.assertEqual(output, f"{context_path}\n{context_path.read_text(encoding='utf-8')}")

    def test_context_today_syncs_only_missing_sources(self) -> None:
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
            with (
                mock.patch("life_log_sync.cli.strava.sync") as strava_sync,
                mock.patch("life_log_sync.cli.withings.sync") as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
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
            strava_sync.assert_not_called()
            withings_sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
