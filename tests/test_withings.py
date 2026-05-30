from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from life_log_sync.config import load_config
from life_log_sync.sources.withings import (
    authorization_url,
    fetch_body_measures_windowed,
    fetch_workouts_windowed_if_available,
    fetch_workouts_windowed,
    merge_measure_rows,
    merge_workout_rows,
    normalize_measure_groups,
    normalize_workouts,
    write_measures,
    write_workouts,
)


class FakeResponse:
    def __init__(self, body: object) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return {"status": 0, "body": self.body}


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def post(self, *args: object, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        data = kwargs.get("data", {})
        if isinstance(data, dict) and data.get("action") == "getworkouts":
            return FakeResponse({"series": []})
        return FakeResponse({"measuregrps": []})


class UnavailableWorkoutSession(FakeSession):
    def post(self, *args: object, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponseWithStatus({"error": "Not implemented"}, status=2554)


class InsufficientScopeWorkoutSession(FakeSession):
    def post(self, *args: object, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponseWithStatus({}, status=403, error="Insufficient_scope")


class FakeResponseWithStatus(FakeResponse):
    def __init__(self, body: object, *, status: int, error: str = "Not implemented") -> None:
        super().__init__(body)
        self.status = status
        self.error = error

    def json(self) -> object:
        return {"status": self.status, "body": self.body, "error": self.error}


class WithingsTest(unittest.TestCase):
    def test_normalizes_measure_groups(self) -> None:
        rows = normalize_measure_groups(
            [
                {
                    "grpid": 123,
                    "date": 1780041600,
                    "measures": [
                        {"type": 1, "value": 7050, "unit": -2},
                        {"type": 6, "value": 1842, "unit": -2},
                    ],
                }
            ]
        )

        self.assertEqual(rows[0]["grpid"], 123)
        self.assertEqual(rows[0]["type_name"], "weight")
        self.assertEqual(rows[0]["value"], "70.50")
        self.assertEqual(rows[1]["type_name"], "fat_ratio")
        self.assertEqual(rows[1]["unit"], "%")

    def test_builds_authorization_url_with_activity_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "life-log-sync.toml"
            config_path.write_text(
                """
[withings]
client_id = "client-id"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            url = authorization_url(config, redirect_uri="https://example.test/callback", state="state")

            self.assertIn("client_id=client-id", url)
            self.assertIn("scope=user.metrics%2Cuser.activity", url)
            self.assertIn("redirect_uri=https%3A%2F%2Fexample.test%2Fcallback", url)

    def test_normalizes_workouts(self) -> None:
        rows = normalize_workouts(
            [
                {
                    "id": 123,
                    "category": 7,
                    "startdate": 1780041600,
                    "enddate": 1780045200,
                    "data": {"effduration": 3300, "manual_distance": 1000},
                }
            ]
        )

        self.assertEqual(rows[0]["source"], "withings")
        self.assertEqual(rows[0]["source_id"], "123")
        self.assertEqual(rows[0]["activity_type"], "swim")
        self.assertEqual(rows[0]["duration_min"], "55.00")
        self.assertEqual(rows[0]["distance_km"], "1.00")

    def test_writes_raw_json_and_csv_to_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "life-log-sync.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[withings]
access_token = "access"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = write_measures(
                config,
                {
                    "measuregrps": [
                        {
                            "grpid": 123,
                            "date": 1780041600,
                            "measures": [{"type": 1, "value": 7050, "unit": -2}],
                        }
                    ]
                },
            )

            raw_path = data_dir / "withings/raw/body_measures.json"
            csv_path = data_dir / "withings/body_measures.csv"
            self.assertIn(raw_path, written)
            self.assertIn(csv_path, written)
            self.assertEqual(json.loads(raw_path.read_text(encoding="utf-8"))["measuregrps"][0]["grpid"], 123)
            with csv_path.open(encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(rows[0]["type_name"], "weight")

    def test_writes_raw_workout_json_and_csv_to_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "life-log-sync.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[withings]
access_token = "access"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = write_workouts(
                config,
                {
                    "series": [
                        {
                            "id": 123,
                            "category": 1,
                            "startdate": 1780041600,
                            "enddate": 1780043400,
                            "data": {"effduration": 1800, "distance": 2000},
                        }
                    ]
                },
            )

            raw_path = data_dir / "withings/raw/workouts.json"
            csv_path = data_dir / "withings/workouts.csv"
            self.assertIn(raw_path, written)
            self.assertIn(csv_path, written)
            self.assertEqual(json.loads(raw_path.read_text(encoding="utf-8"))["series"][0]["id"], 123)
            with csv_path.open(encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(rows[0]["activity_type"], "walk")

    def test_merges_measure_rows_idempotently(self) -> None:
        existing_rows = [
            {
                "grpid": "123",
                "date": "2026-05-29",
                "datetime_local": "2026-05-29T06:00:00",
                "type": "1",
                "type_name": "weight",
                "value": "70.50",
                "unit": "kg",
            }
        ]
        new_rows = [
            {
                "grpid": "123",
                "date": "2026-05-29",
                "datetime_local": "2026-05-29T06:00:00",
                "type": "1",
                "type_name": "weight",
                "value": "70.50",
                "unit": "kg",
            },
            {
                "grpid": "124",
                "date": "2026-05-30",
                "datetime_local": "2026-05-30T06:00:00",
                "type": "1",
                "type_name": "weight",
                "value": "70.40",
                "unit": "kg",
            },
        ]

        rows = merge_measure_rows(existing_rows, new_rows)

        self.assertEqual(len(rows), 2)
        self.assertEqual([row["grpid"] for row in rows], ["123", "124"])

    def test_merges_workout_rows_idempotently(self) -> None:
        existing_rows = [
            {
                "source": "withings",
                "source_id": "123",
                "start_time": "2026-05-29T06:00:00",
                "activity_type": "walk",
            }
        ]
        new_rows = [
            {
                "source": "withings",
                "source_id": "123",
                "start_time": "2026-05-29T06:00:00",
                "activity_type": "walk",
            },
            {
                "source": "withings",
                "source_id": "124",
                "start_time": "2026-05-30T06:00:00",
                "activity_type": "swim",
            },
        ]

        rows = merge_workout_rows(existing_rows, new_rows)

        self.assertEqual(len(rows), 2)
        self.assertEqual([row["source_id"] for row in rows], ["123", "124"])

    def test_fetches_withings_backfill_in_date_windows(self) -> None:
        session = FakeSession()

        body = fetch_body_measures_windowed(
            session,
            "access",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 4, 15),
        )

        self.assertEqual(body, {"measuregrps": []})
        self.assertEqual(len(session.calls), 2)

    def test_fetches_withings_workouts_in_date_windows(self) -> None:
        session = FakeSession()

        body = fetch_workouts_windowed(
            session,
            "access",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 4, 15),
        )

        self.assertEqual(body, {"series": []})
        self.assertEqual(len(session.calls), 2)

    def test_skips_workouts_when_endpoint_is_unavailable(self) -> None:
        session = UnavailableWorkoutSession()

        body = fetch_workouts_windowed_if_available(
            session,
            "access",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 1),
        )

        self.assertEqual(body, {"series": []})

    def test_reports_missing_activity_scope_for_workouts(self) -> None:
        session = InsufficientScopeWorkoutSession()

        with self.assertRaisesRegex(SystemExit, "user.activity OAuth scope"):
            fetch_workouts_windowed_if_available(
                session,
                "access",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()
