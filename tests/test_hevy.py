from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ingest.config import load_config
from ingest.plugins import hevy


class HevySourceTest(unittest.TestCase):
    def test_imports_hevy_workout_export_to_normalized_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            export_path = root / "hevy.csv"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            export_path.write_text(
                "\n".join(
                    [
                        "title,start_time,end_time,description,exercise_title,set_index,set_type,weight_lbs,reps,distance_miles,duration_seconds,rpe",
                        "Push Day,\"28 Mar 2025, 17:29\",\"28 Mar 2025, 18:45\",,Bench Press,1,normal,185,8,,,8",
                        "Push Day,\"28 Mar 2025, 17:29\",\"28 Mar 2025, 18:45\",,OHP,1,warmup,45,10,,,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            paths = hevy.import_workouts_csv(config, export_path)

            self.assertEqual(paths, [data_dir / "hevy/workouts.csv", data_dir / "hevy/sets.csv"])
            output = paths[0].read_text(encoding="utf-8")
            sets_output = paths[1].read_text(encoding="utf-8")
            self.assertIn("source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type,name,notes", output)
            self.assertIn("hevy,2025-03-28T17:29:00-push-day,2025-03-28T17:29:00,2025-03-28T18:45:00,76.00,,strength,strength,Push Day", output)
            self.assertIn("Bench Press,1,normal,83.91,8", sets_output)
            self.assertIn(",671.32", sets_output)

    def test_skips_rows_without_parseable_start_time(self) -> None:
        rows = hevy.normalize_workout_rows([{"title": "Bad", "start_time": "not a date"}])

        self.assertEqual(rows, [])

    def test_parses_current_hevy_export_timestamps(self) -> None:
        rows = hevy.normalize_workout_rows(
            [
                {
                    "title": "Push Day",
                    "start_time": "Jun 5, 2026, 1:52 PM",
                    "end_time": "Jun 5, 2026, 3:25 PM",
                    "exercise_title": "Bench Press",
                    "set_index": "1",
                    "weight_kg": "80",
                    "reps": "8",
                }
            ]
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["start_time"], "2026-06-05T13:52:00")
        self.assertEqual(rows[0]["end_time"], "2026-06-05T15:25:00")
        self.assertEqual(rows[0]["duration_min"], "93.00")

    def test_mixed_aware_and_naive_timestamps_use_zero_duration(self) -> None:
        rows = hevy.normalize_workout_rows(
            [
                {
                    "title": "Mixed timestamps",
                    "start_time": "2026-06-05T13:52:00+00:00",
                    "end_time": "2026-06-05T15:25:00",
                    "exercise_title": "Bench Press",
                    "set_index": "1",
                }
            ]
        )

        self.assertEqual(rows[0]["duration_min"], "0.00")

    def test_sync_exports_then_imports_workouts(self) -> None:
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
            config = load_config(config_path)

            with mock.patch("ingest.plugins.hevy.export_workouts_csv", return_value=export_path):
                paths = hevy.sync(config)

            self.assertEqual(paths, [data_dir / "hevy/workouts.csv", data_dir / "hevy/sets.csv"])
            self.assertIn("Push Day", paths[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
