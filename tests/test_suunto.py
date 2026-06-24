from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import anyio

from ingest.config import load_config
from ingest.context import build_daily_state
from ingest.sources import suunto


class SuuntoSourceTest(unittest.TestCase):
    def test_sync_invokes_configured_command_and_merges_workout_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            command_path = root / "bin/suuntool"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[suunto]
enabled = true
command = "{command_path}"
""".strip(),
                encoding="utf-8",
            )
            workouts_path = data_dir / "suunto/workouts.csv"
            workouts_path.parent.mkdir(parents=True)
            workouts_path.write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,old,2026-06-01T08:00:00+09:00,2026-06-01T08:30:00+09:00,30.00,5.00,0,run,RUNNING,Running,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            start_ms = int(datetime(2026, 6, 2, 3, tzinfo=timezone.utc).timestamp() * 1000)
            workout = {
                "key": "new",
                "activityId": 21,
                "startTime": start_ms,
                "stopTime": start_ms + 1_800_000,
                "totalTime": 1800,
                "totalDistance": 1000,
                "stepCount": 12,
            }
            process = SimpleNamespace(
                returncode=0,
                stdout=(json.dumps(workout) + "\n").encode(),
                stderr=b"",
            )
            config = load_config(config_path)

            with mock.patch(
                "ingest.sources.suunto.anyio.run_process",
                new=mock.AsyncMock(return_value=process),
            ) as run_process:
                paths = anyio.run(suunto.sync_async, config)

            run_process.assert_awaited_once_with(
                [
                    str(command_path),
                    "workouts",
                    "list",
                    "--since",
                    "2026-06-01",
                    "--stream",
                ],
                check=False,
            )
            self.assertEqual(
                paths,
                [data_dir / "suunto/raw/workouts_sync.json", workouts_path],
            )
            output = workouts_path.read_text(encoding="utf-8")
            self.assertIn("suunto,old,", output)
            self.assertIn("suunto,new,", output)
            self.assertIn(",1.00,12,swim,SWIMMING,Swimming,", output)
            self.assertEqual(json.loads(paths[0].read_text(encoding="utf-8")), [workout])

    def test_sync_uses_fallback_window_without_existing_workouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            config_path.write_text(
                f'[app]\ndata_dir = "{root / "app-data"}"\n\n[sync.suunto]\ndays = 3\n',
                encoding="utf-8",
            )
            config = load_config(config_path)

            with mock.patch(
                "ingest.sources.suunto.fetch_workouts",
                new=mock.AsyncMock(return_value=[]),
            ) as fetch_workouts:
                anyio.run(lambda: suunto.sync_async(config, end_date=date(2026, 6, 10)))

            fetch_workouts.assert_awaited_once_with(config.suunto, date(2026, 6, 8))

    def test_missing_suuntool_reports_install_and_config_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text("", encoding="utf-8")
            config = load_config(config_path)

            with mock.patch(
                "ingest.sources.suunto.anyio.run_process",
                new=mock.AsyncMock(side_effect=FileNotFoundError),
            ):
                with self.assertRaisesRegex(SystemExit, "Install and log in to suuntool"):
                    anyio.run(suunto.fetch_workouts, config.suunto, date(2026, 6, 1))

    def test_failed_suuntool_surfaces_machine_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text("", encoding="utf-8")
            config = load_config(config_path)
            process = SimpleNamespace(
                returncode=4,
                stdout=b"",
                stderr=b'{"error":{"code":"AUTH_EXPIRED","message":"login required"}}\n',
            )

            with mock.patch(
                "ingest.sources.suunto.anyio.run_process",
                new=mock.AsyncMock(return_value=process),
            ):
                with self.assertRaisesRegex(SystemExit, "AUTH_EXPIRED"):
                    anyio.run(suunto.fetch_workouts, config.suunto, date(2026, 6, 1))

    def test_rejects_malformed_ndjson(self) -> None:
        with self.assertRaisesRegex(SystemExit, "line 2"):
            suunto.parse_workouts('{"key":"ok"}\nnot-json\n')

    def test_rejects_workout_without_key(self) -> None:
        with self.assertRaisesRegex(SystemExit, "missing key"):
            suunto.normalize_workouts([{"activityId": 1, "startTime": 1_782_255_600_000}])

    def test_rejects_workout_with_invalid_start_time(self) -> None:
        with self.assertRaisesRegex(SystemExit, "invalid startTime"):
            suunto.normalize_workouts([{"key": "bad-start", "activityId": 1, "startTime": "invalid"}])

    def test_rejects_workout_without_activity_identity(self) -> None:
        with self.assertRaisesRegex(SystemExit, "invalid activityId"):
            suunto.normalize_workouts([{"key": "missing-activity", "startTime": 1_782_255_600_000}])

    def test_preserves_unknown_numeric_activity_id(self) -> None:
        rows = suunto.normalize_workouts(
            [{"key": "unknown", "activityId": 999, "startTime": 1_782_255_600_000}]
        )

        self.assertEqual(rows[0]["raw_type"], "activity_999")
        self.assertEqual(rows[0]["activity_type"], "activity 999")

    def test_daily_state_includes_normalized_suunto_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            workouts_path = data_dir / "suunto/workouts.csv"
            workouts_path.parent.mkdir(parents=True)
            workouts_path.write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,run-1,2026-06-02T12:00:00+09:00,2026-06-02T12:45:00+09:00,45.00,8.00,7000,run,RUNNING,Running,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            state = build_daily_state(load_config(config_path), date(2026, 6, 2))

            self.assertEqual(len(state.activities), 1)
            self.assertEqual(state.activities[0].source, "suunto")
            self.assertEqual(state.activities[0].activity_type, "run")
            self.assertEqual(state.activities[0].distance_km, 8.0)


if __name__ == "__main__":
    unittest.main()
