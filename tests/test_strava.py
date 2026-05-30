from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from life_log_sync.config import load_config
from life_log_sync.sources.strava import (
    fetch_activities_since,
    fetch_recent_activities,
    merge_activity_rows,
    normalize_activity,
    write_activities,
)


class FakeResponse:
    def __init__(self, body: object) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.body


class FakeSession:
    def __init__(self, pages: list[list[dict[str, object]]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, object]] = []

    def get(self, *args: object, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        params = kwargs["params"]
        if not isinstance(params, dict):
            raise AssertionError("Expected request params.")
        page = int(params["page"])
        return FakeResponse(self.pages[page - 1])


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

    def test_fetches_all_activity_pages(self) -> None:
        session = FakeSession(
            [
                [{"id": 1}, {"id": 2}],
                [{"id": 3}],
            ]
        )

        activities = fetch_recent_activities(session, "access", days=7, per_page=2)

        self.assertEqual(activities, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual([call["params"]["page"] for call in session.calls], [1, 2])

    def test_fetches_backfill_with_date_bounds(self) -> None:
        session = FakeSession([[{"id": 1}]])

        activities = fetch_activities_since(
            session,
            "access",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            per_page=10,
        )

        params = session.calls[0]["params"]
        self.assertEqual(activities, [{"id": 1}])
        self.assertIn("after", params)
        self.assertIn("before", params)

    def test_merges_activity_rows_idempotently(self) -> None:
        existing_rows = [
            {
                "id": "1",
                "start_date_local": "2026-05-29T06:30:00Z",
                "name": "Morning Run",
                "sport_type": "Run",
                "distance_km": "5.00",
            }
        ]
        new_rows = [
            {
                "id": "1",
                "start_date_local": "2026-05-29T06:30:00Z",
                "name": "Morning Run Updated",
                "sport_type": "Run",
                "distance_km": "5.10",
            },
            {
                "id": "2",
                "start_date_local": "2026-05-30T06:30:00Z",
                "name": "Morning Walk",
                "sport_type": "Walk",
                "distance_km": "2.00",
            },
        ]

        rows = merge_activity_rows(existing_rows, new_rows)

        self.assertEqual(len(rows), 2)
        self.assertEqual([row["id"] for row in rows], ["1", "2"])
        self.assertEqual(rows[0]["name"], "Morning Run Updated")


if __name__ == "__main__":
    unittest.main()
