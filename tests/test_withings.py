from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from ingest.config import load_config
from ingest.sources.withings import (
    authorization_url,
    fetch_activity_windowed,
    fetch_body_measures_windowed,
    fetch_workouts_windowed_if_available,
    fetch_workouts_windowed,
    lagging_local_date,
    merge_activity_rows,
    merge_measure_rows,
    merge_workout_rows,
    normalize_activity_summaries,
    normalize_measure_groups,
    normalize_workouts,
    sync,
    write_activity,
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
        if isinstance(data, dict) and data.get("action") == "getactivity":
            return FakeResponse({"activities": []})
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


def write_config(root: Path, extra: str = "") -> Path:
    data_dir = root / "app-data"
    config_path = root / "ingest.toml"
    content = f'[app]\ndata_dir = "{data_dir}"\n'
    if extra:
        content = f"{content}\n{extra.strip()}\n"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def write_withings_csvs(
    data_dir: Path,
    *,
    measures: list[str] | None = None,
    workouts: list[str] | None = None,
    activity: list[str] | None = None,
) -> None:
    withings_dir = data_dir / "withings"
    withings_dir.mkdir(parents=True, exist_ok=True)
    if measures is not None:
        _write_csv_lines(
            withings_dir / "body_measures.csv",
            "grpid,date,datetime_local,type,type_name,value,unit",
            measures,
        )
    if workouts is not None:
        _write_csv_lines(
            withings_dir / "workouts.csv",
            "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type",
            workouts,
        )
    if activity is not None:
        _write_csv_lines(withings_dir / "activity.csv", "date,step_count,distance_km", activity)


def _write_csv_lines(path: Path, header: str, rows: list[str]) -> None:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


class WithingsTest(unittest.TestCase):
    def test_normalizes_measure_groups(self) -> None:
        rows = normalize_measure_groups(
            [
                {
                    "grpid": 123,
                    "date": 1780041600,
                    "measures": [
                        {"type": 1, "value": 7050, "unit": -2},
                        {"type": 4, "value": 180, "unit": -2},
                        {"type": 6, "value": 1842, "unit": -2},
                    ],
                }
            ]
        )

        self.assertEqual(rows[0]["grpid"], 123)
        self.assertEqual(rows[0]["type_name"], "weight")
        self.assertEqual(rows[0]["value"], "70.50")
        self.assertEqual(rows[1]["type_name"], "height")
        self.assertEqual(rows[1]["value"], "1.80")
        self.assertEqual(rows[1]["unit"], "m")
        self.assertEqual(rows[2]["type_name"], "fat_ratio")
        self.assertEqual(rows[2]["unit"], "%")

    def test_builds_authorization_url_with_activity_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
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
                    "data": {"effduration": 3300, "manual_distance": 1000, "steps": 1234},
                }
            ]
        )

        self.assertEqual(rows[0]["source"], "withings")
        self.assertEqual(rows[0]["source_id"], "123")
        self.assertEqual(rows[0]["activity_type"], "swim")
        self.assertEqual(rows[0]["duration_min"], "55.00")
        self.assertEqual(rows[0]["distance_km"], "1.00")
        self.assertEqual(rows[0]["step_count"], "1234")

    def test_normalizes_activity_summaries(self) -> None:
        rows = normalize_activity_summaries(
            [
                {
                    "date": "2026-05-29",
                    "steps": 3456,
                    "distance": 2100,
                }
            ]
        )

        self.assertEqual(
            rows,
            [{"date": "2026-05-29", "step_count": "3456", "distance_km": "2.10"}],
        )

    def test_ignores_strength_training_category_duplicate(self) -> None:
        rows = normalize_workouts(
            [
                {
                    "id": 123,
                    "category": 16,
                    "startdate": 1780041600,
                    "enddate": 1780045200,
                    "data": {"effduration": 3600},
                }
            ]
        )

        self.assertEqual(rows, [])

    def test_writes_raw_json_and_csv_to_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
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
            config_path = root / "ingest.toml"
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
                reader = csv.DictReader(csv_file)
                rows = list(reader)
            self.assertEqual(
                reader.fieldnames,
                [
                    "source",
                    "source_id",
                    "start_time",
                    "end_time",
                    "duration_min",
                    "distance_km",
                    "step_count",
                    "activity_type",
                    "raw_type",
                ],
            )
            self.assertEqual(rows[0]["activity_type"], "walk")

    def test_writes_raw_activity_json_and_csv_to_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
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

            written = write_activity(
                config,
                {"activities": [{"date": "2026-05-29", "steps": 3456, "distance": 2100}]},
            )

            raw_path = data_dir / "withings/raw/activity.json"
            csv_path = data_dir / "withings/activity.csv"
            self.assertIn(raw_path, written)
            self.assertIn(csv_path, written)
            self.assertEqual(json.loads(raw_path.read_text(encoding="utf-8"))["activities"][0]["steps"], 3456)
            with csv_path.open(encoding="utf-8", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, ["date", "step_count", "distance_km"])
            self.assertEqual(rows[0]["step_count"], "3456")

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

    def test_merges_activity_rows_idempotently(self) -> None:
        existing_rows = [{"date": "2026-05-29", "step_count": "1000"}]
        new_rows = [
            {"date": "2026-05-29", "step_count": "1200"},
            {"date": "2026-05-30", "step_count": "1500"},
        ]

        rows = merge_activity_rows(existing_rows, new_rows)

        self.assertEqual(
            rows,
            [
                {"date": "2026-05-29", "step_count": "1200"},
                {"date": "2026-05-30", "step_count": "1500"},
            ],
        )

    def test_lagging_local_date_uses_oldest_source_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = write_config(root)
            write_withings_csvs(
                data_dir,
                measures=[
                    "1,not-a-date,,1,weight,70.50,kg",
                    "2,2026-06-02,2026-06-02T06:00:00,1,weight,70.40,kg",
                ],
                workouts=[
                    "withings,1,2026-05-31T08:00:00,2026-05-31T08:30:00,30.00,1.00,walk,walk",
                ],
                activity=["2026-06-01,1200,1.00"],
            )
            config = load_config(config_path)

            self.assertEqual(lagging_local_date(config), date(2026, 5, 31))

    def test_sync_refreshes_from_lagging_local_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = write_config(root)
            write_withings_csvs(
                data_dir,
                measures=["1,2026-06-02,2026-06-02T06:00:00,1,weight,70.50,kg"],
                workouts=[
                    "withings,1,2026-06-02T08:00:00,2026-06-02T08:30:00,30.00,1.00,walk,walk",
                ],
                activity=["2026-06-02,1200,1.00"],
            )
            config = load_config(config_path)

            with mock.patch("ingest.sources.withings.sync_range", return_value=[]) as sync_range:
                written = sync(config, end_date=date(2026, 6, 5))

            self.assertEqual(written, [])
            sync_range.assert_called_once_with(
                config,
                date(2026, 6, 2),
                date(2026, 6, 5),
                raw_name="body_measures_sync.json",
            )

    def test_sync_uses_configured_recent_days_when_no_local_data_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = write_config(root, "[sync.withings]\ndays = 4")
            config = load_config(config_path)

            with mock.patch("ingest.sources.withings.sync_range", return_value=[]) as sync_range:
                written = sync(config, end_date=date(2026, 6, 5))

            self.assertEqual(written, [])
            sync_range.assert_called_once_with(
                config,
                date(2026, 6, 2),
                date(2026, 6, 5),
                raw_name="body_measures_sync.json",
            )

    def test_sync_skips_when_local_data_is_newer_than_today(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = write_config(root)
            write_withings_csvs(data_dir, activity=["2026-06-06,1200,1.00"])
            config = load_config(config_path)

            with mock.patch("ingest.sources.withings.sync_range", return_value=[]) as sync_range:
                written = sync(config, end_date=date(2026, 6, 5))

            self.assertEqual(written, [])
            sync_range.assert_not_called()

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

    def test_fetches_withings_activity_in_date_windows(self) -> None:
        session = FakeSession()

        body = fetch_activity_windowed(
            session,
            "access",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 4, 15),
        )

        self.assertEqual(body, {"activities": []})
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
