from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from life_log_sync.config import load_config
from life_log_sync.sources.strava import normalize_activity, write_activities


class StravaTest(unittest.TestCase):
    def test_normalizes_activity(self) -> None:
        row = normalize_activity(
            {
                "id": 123,
                "start_date_local": "2026-05-29T06:30:00Z",
                "name": "Morning Run",
                "sport_type": "Run",
                "distance": 5432.1,
                "moving_time": 1800,
                "elapsed_time": 1900,
                "total_elevation_gain": 42,
                "average_speed": 3.01,
                "max_speed": 5.5,
            }
        )

        self.assertEqual(row["id"], 123)
        self.assertEqual(row["distance_km"], "5.43")
        self.assertEqual(row["moving_time_min"], "30.00")
        self.assertEqual(row["elapsed_time_min"], "31.67")

    def test_writes_raw_json_and_csv_to_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "life-log-sync.toml"
            data_dir = root / "app-data"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[strava]
access_token = "access"

[sync.strava]
days = 30
per_page = 100
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = write_activities(
                config,
                [
                    {
                        "id": 123,
                        "name": "Morning Run",
                        "sport_type": "Run",
                        "distance": 1000,
                        "moving_time": 300,
                    }
                ],
            )

            raw_path = data_dir / "strava/raw/123.json"
            csv_path = data_dir / "strava/activities.csv"
            self.assertIn(raw_path, written)
            self.assertIn(csv_path, written)
            self.assertEqual(json.loads(raw_path.read_text(encoding="utf-8"))["id"], 123)
            with csv_path.open(encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(rows[0]["distance_km"], "1.00")


if __name__ == "__main__":
    unittest.main()
