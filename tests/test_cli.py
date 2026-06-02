from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ingest.cli import build_parser, main


class CliTest(unittest.TestCase):
    def test_parser_accepts_new_sync_and_backfill_commands(self) -> None:
        parser = build_parser()

        today_args = parser.parse_args(["today"])
        sync_args = parser.parse_args(["sync", "withings"])
        sync_all_args = parser.parse_args(["sync", "all"])
        backfill_args = parser.parse_args(["backfill", "withings", "--from", "2026-01-01"])
        oauth_args = parser.parse_args(
            ["oauth", "withings", "auth-url", "--redirect-uri", "https://callback.example"]
        )

        self.assertEqual(today_args.source, "today")
        self.assertEqual(sync_args.source, "sync")
        self.assertEqual(sync_args.command, "withings")
        self.assertEqual(sync_all_args.source, "sync")
        self.assertEqual(sync_all_args.command, "all")
        self.assertEqual(backfill_args.source, "backfill")
        self.assertEqual(backfill_args.command, "withings")
        self.assertEqual(backfill_args.from_date.isoformat(), "2026-01-01")
        self.assertEqual(oauth_args.source, "oauth")
        self.assertEqual(oauth_args.service, "withings")
        self.assertEqual(oauth_args.command, "auth-url")

    def test_parser_rejects_removed_alias_commands(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["context", "today"])

        with self.assertRaises(SystemExit):
            parser.parse_args(["withings", "sync"])

    def test_oauth_withings_auth_url_prints_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            config_path.write_text('[withings]\nclient_id = "client"\n', encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "oauth",
                        "withings",
                        "auth-url",
                        "--redirect-uri",
                        "https://callback.example",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("https://account.withings.com/oauth2_user/authorize2", output)
            self.assertIn("client_id=client", output)

    def test_ingest_today_prints_generated_content_without_sync_when_data_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
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
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type",
                        "withings,1,2026-05-29T08:00:00,2026-05-29T08:30:00,30.00,1.00,walk,walk",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.withings.sync") as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "today",
                        "--date",
                        "2026-05-29",
                    ]
                )

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            output = stdout.getvalue()
            context_path = data_dir / "generated/daily_context.md"
            self.assertTrue(output.startswith(f"{context_path}\n"))
            self.assertIn("# Daily Context - 2026-05-29", output)
            self.assertIn("walk: withings:1", output)
            self.assertEqual(output, f"{context_path}\n{context_path.read_text(encoding='utf-8')}")

    def test_ingest_today_syncs_only_missing_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")

            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.withings.sync") as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "today",
                        "--date",
                        "2026-05-29",
                    ]
                )

            self.assertEqual(exit_code, 0)
            withings_sync.assert_called_once()

    def test_ingest_today_syncs_current_day_even_when_local_data_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
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
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type",
                        "withings,1,2026-05-29T08:00:00,2026-05-29T08:30:00,30.00,1.00,walk,walk",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            fake_date = mock.Mock()
            fake_date.today.return_value = __import__("datetime").date(2026, 5, 29)
            fake_date.fromisoformat = __import__("datetime").date.fromisoformat
            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.date", fake_date),
                mock.patch("ingest.cli.withings.sync") as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "today"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
