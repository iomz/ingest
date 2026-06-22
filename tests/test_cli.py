from __future__ import annotations

import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ingest.cli import build_parser, main


class CliTest(unittest.TestCase):
    def test_parser_accepts_new_sync_and_backfill_commands(self) -> None:
        parser = build_parser()

        today_args = parser.parse_args(["today"])
        today_sync_args = parser.parse_args(["today", "--sync"])
        today_markdown_args = parser.parse_args(["today", "--markdown"])
        day_args = parser.parse_args(["day", "2026-06-02"])
        day_sync_args = parser.parse_args(["day", "2026-06-02", "--sync"])
        day_markdown_args = parser.parse_args(["day", "2026-06-02", "--markdown"])
        yesterday_args = parser.parse_args(["yesterday"])
        yesterday_markdown_args = parser.parse_args(["yesterday", "--markdown"])
        sync_args = parser.parse_args(["sync", "withings"])
        sync_hevy_args = parser.parse_args(["sync", "hevy"])
        sync_suunto_args = parser.parse_args(["sync", "suunto"])
        sync_all_args = parser.parse_args(["sync", "all"])
        import_args = parser.parse_args(["import", "hevy", "--csv", "hevy.csv"])
        backfill_args = parser.parse_args(["backfill", "withings", "--from", "2026-01-01"])
        oauth_args = parser.parse_args(
            ["oauth", "withings", "auth-url", "--redirect-uri", "https://callback.example"]
        )

        self.assertEqual(today_args.source, "today")
        self.assertFalse(today_args.sync)
        self.assertFalse(today_args.markdown)
        self.assertTrue(today_sync_args.sync)
        self.assertTrue(today_markdown_args.markdown)
        self.assertEqual(day_args.source, "day")
        self.assertFalse(day_args.markdown)
        self.assertEqual(day_args.target_date.isoformat(), "2026-06-02")
        self.assertTrue(day_sync_args.sync)
        self.assertTrue(day_markdown_args.markdown)
        self.assertEqual(yesterday_args.source, "yesterday")
        self.assertFalse(yesterday_args.markdown)
        self.assertTrue(yesterday_markdown_args.markdown)
        self.assertEqual(sync_args.source, "sync")
        self.assertEqual(sync_args.command, "withings")
        self.assertEqual(sync_hevy_args.source, "sync")
        self.assertEqual(sync_hevy_args.command, "hevy")
        self.assertEqual(sync_suunto_args.command, "suunto")
        self.assertEqual(sync_all_args.source, "sync")
        self.assertEqual(sync_all_args.command, "all")
        self.assertEqual(import_args.source, "import")
        self.assertEqual(import_args.command, "hevy")
        self.assertEqual(import_args.csv, Path("hevy.csv"))
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

    def test_ingest_day_prints_terminal_content_without_sync_when_data_exists(self) -> None:
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
                        "day",
                        "2026-05-29",
                    ]
                )

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            output = stdout.getvalue()
            context_path = data_dir / "generated/daily_context.md"
            self.assertIn("Physical Context — 2026-05-29", output)
            self.assertIn("Daily Snapshot", output)
            self.assertIn("    walk  withings:1 / 1.00 km / 30 min", output)
            self.assertFalse(context_path.exists())

    def test_ingest_day_does_not_sync_missing_sources(self) -> None:
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
                        "day",
                        "2026-05-29",
                    ]
                )

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            self.assertIn("No primary activities found", stdout.getvalue())

    def test_ingest_day_uses_hevy_activity_without_withings_workout(self) -> None:
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
            hevy_csv_path = data_dir / "hevy/workouts.csv"
            hevy_csv_path.parent.mkdir(parents=True)
            hevy_csv_path.write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type,name",
                        "hevy,push,2026-05-29T17:29:00,2026-05-29T18:45:00,76.00,,strength,strength,Push Day",
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
                exit_code = main(["--config", str(config_path), "day", "2026-05-29"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            self.assertIn("Sources         Hevy", stdout.getvalue())
            self.assertIn("Workout", stdout.getvalue())
            self.assertIn("    Push Day / 76 min", stdout.getvalue())
            self.assertNotIn("unknown distance", stdout.getvalue())

    def test_import_hevy_writes_normalized_workouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            export_path = root / "hevy.csv"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            export_path.write_text(
                "\n".join(
                    [
                        "title,start_time,end_time,exercise_title,set_index",
                        "Push Day,\"28 Mar 2025, 17:29\",\"28 Mar 2025, 18:45\",Bench Press,1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["--config", str(config_path), "import", "hevy", "--csv", str(export_path)])

            self.assertEqual(exit_code, 0)
            output_path = data_dir / "hevy/workouts.csv"
            sets_path = data_dir / "hevy/sets.csv"
            self.assertEqual(stdout.getvalue(), f"{output_path}\n{sets_path}\n")
            self.assertIn("Push Day", output_path.read_text(encoding="utf-8"))

    def test_sync_hevy_prints_written_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            output_path = data_dir / "hevy/workouts.csv"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")

            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.hevy.sync", return_value=[output_path]) as hevy_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "sync", "hevy"])

            self.assertEqual(exit_code, 0)
            hevy_sync.assert_called_once()
            self.assertEqual(stdout.getvalue(), f"{output_path}\n")

    def test_sync_all_includes_hevy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            withings_path = data_dir / "withings/body_measures.csv"
            hevy_path = data_dir / "hevy/workouts.csv"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")

            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.withings.sync", return_value=[withings_path]) as withings_sync,
                mock.patch("ingest.cli.hevy.sync", return_value=[hevy_path]) as hevy_sync,
                mock.patch("ingest.cli.suunto.sync_async", new=mock.AsyncMock()) as suunto_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "sync", "all"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_called_once()
            hevy_sync.assert_called_once()
            suunto_sync.assert_not_awaited()
            self.assertEqual(stdout.getvalue(), f"{withings_path}\n{hevy_path}\n")

    def test_sync_all_includes_enabled_suunto(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            withings_path = data_dir / "withings/body_measures.csv"
            hevy_path = data_dir / "hevy/workouts.csv"
            suunto_path = data_dir / "suunto/workouts.csv"
            config_path.write_text(
                f'[app]\ndata_dir = "{data_dir}"\n\n[suunto]\nenabled = true\n',
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.withings.sync", return_value=[withings_path]),
                mock.patch("ingest.cli.hevy.sync", return_value=[hevy_path]),
                mock.patch(
                    "ingest.cli.suunto.sync_async",
                    new=mock.AsyncMock(return_value=[suunto_path]),
                ) as suunto_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "sync", "all"])

            self.assertEqual(exit_code, 0)
            suunto_sync.assert_awaited_once()
            self.assertEqual(stdout.getvalue(), f"{withings_path}\n{hevy_path}\n{suunto_path}\n")

    def test_sync_all_fetches_sources_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            config_path.write_text("", encoding="utf-8")
            barrier = threading.Barrier(2, timeout=1)

            def sync_source(_config: object) -> list[Path]:
                barrier.wait()
                return []

            with (
                mock.patch("ingest.cli.withings.sync", side_effect=sync_source),
                mock.patch("ingest.cli.hevy.sync", side_effect=sync_source),
            ):
                exit_code = main(["--config", str(config_path), "sync", "all"])

            self.assertEqual(exit_code, 0)

    def test_today_sync_runs_all_sources_before_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            withings_path = data_dir / "withings/body_measures.csv"
            hevy_path = data_dir / "hevy/workouts.csv"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")

            fake_date = mock.Mock()
            fake_date.today.return_value = __import__("datetime").date(2026, 5, 29)
            fake_date.fromisoformat = __import__("datetime").date.fromisoformat
            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.date", fake_date),
                mock.patch("ingest.cli.withings.sync", return_value=[withings_path]) as withings_sync,
                mock.patch("ingest.cli.hevy.sync", return_value=[hevy_path]) as hevy_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "today", "--sync"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_called_once()
            hevy_sync.assert_called_once()
            self.assertIn("Physical Context — 2026-05-29", stdout.getvalue())
            self.assertIn("Daily Snapshot", stdout.getvalue())
            self.assertNotIn("# Physical Context - 2026-05-29", stdout.getvalue())

    def test_ingest_yesterday_uses_previous_day(self) -> None:
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
                        "1,2026-06-02,2026-06-02T06:00:00,1,weight,70.50,kg",
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
                        "withings,1,2026-06-02T08:00:00,2026-06-02T08:30:00,30.00,1.00,walk,walk",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            fake_date = mock.Mock()
            fake_date.today.return_value = __import__("datetime").date(2026, 6, 3)
            fake_date.fromisoformat = __import__("datetime").date.fromisoformat
            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.date", fake_date),
                mock.patch("ingest.cli.withings.sync") as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "yesterday"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            self.assertIn("Physical Context — 2026-06-02", stdout.getvalue())
            self.assertNotIn("# Physical Context - 2026-06-02", stdout.getvalue())

    def test_ingest_today_renders_without_sync(self) -> None:
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
            withings_sync.assert_not_called()
            output = stdout.getvalue()
            self.assertIn("Physical Context — 2026-05-29", output)
            self.assertIn("Daily Snapshot", output)
            self.assertIn("Activity  Light / score 3.5 / trend unknown", output)
            self.assertNotIn("·", output)
            self.assertNotIn("| Area | Status |", output)

    def test_ingest_today_markdown_prints_generated_markdown(self) -> None:
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

            fake_date = mock.Mock()
            fake_date.today.return_value = __import__("datetime").date(2026, 5, 29)
            fake_date.fromisoformat = __import__("datetime").date.fromisoformat
            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.date", fake_date),
                mock.patch("ingest.cli.withings.sync") as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "today", "--markdown"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            output = stdout.getvalue()
            context_path = data_dir / "generated/daily_context.md"
            self.assertTrue(output.startswith("# Physical Context - 2026-05-29\n"))
            self.assertEqual(output, context_path.read_text(encoding="utf-8"))

    def test_ingest_day_markdown_prints_generated_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["--config", str(config_path), "day", "2026-05-29", "--markdown"])

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            context_path = data_dir / "generated/daily_context.md"
            self.assertTrue(output.startswith("# Physical Context - 2026-05-29\n"))
            self.assertEqual(output, context_path.read_text(encoding="utf-8"))

    def test_ingest_yesterday_markdown_prints_generated_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")

            fake_date = mock.Mock()
            fake_date.today.return_value = __import__("datetime").date(2026, 5, 30)
            fake_date.fromisoformat = __import__("datetime").date.fromisoformat
            stdout = io.StringIO()
            with (
                mock.patch("ingest.cli.date", fake_date),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "yesterday", "--markdown"])

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            context_path = data_dir / "generated/daily_context.md"
            self.assertTrue(output.startswith("# Physical Context - 2026-05-29\n"))
            self.assertEqual(output, context_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
