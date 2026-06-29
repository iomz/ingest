from __future__ import annotations

import csv
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from ingest.config import load_config
from ingest.plugins.withings import (
    authorization_url,
    fetch_activity_windowed,
    fetch_body_measures_windowed,
    fetch_latest_height,
    fetch_sleep_summaries_windowed,
    fetch_workouts_windowed_if_available,
    fetch_workouts_windowed,
    has_cached_height,
    lagging_local_date,
    merge_activity_rows,
    merge_measure_rows,
    merge_sleep_rows,
    merge_workout_rows,
    normalize_activity_summaries,
    normalize_measure_groups,
    normalize_sleep_summaries,
    normalize_workouts,
    parse_authorization_code,
    sync,
    sync_range,
    summarize_sleep_states,
    write_activity,
    write_measures,
    write_sleep,
    write_workouts,
)


def write_auth_state(data_dir: Path, state: dict[str, object]) -> None:
    path = data_dir / "withings/auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


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
        if isinstance(data, dict) and data.get("action") == "getsummary":
            return FakeResponse({"series": []})
        if isinstance(data, dict) and data.get("action") == "get":
            return FakeResponse({"model": 16, "series": []})
        return FakeResponse({"measuregrps": []})


class HeightSession(FakeSession):
    def post(self, *args: object, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        data = kwargs.get("data", {})
        if isinstance(data, dict) and data.get("action") == "getactivity":
            return FakeResponse({"activities": []})
        if isinstance(data, dict) and data.get("action") == "getworkouts":
            return FakeResponse({"series": []})
        if isinstance(data, dict) and data.get("action") == "getsummary":
            return FakeResponse({"series": []})
        if isinstance(data, dict) and data.get("action") == "get":
            return FakeResponse({"model": 16, "series": []})
        if isinstance(data, dict) and data.get("meastypes") == "4":
            return FakeResponse(
                {
                    "measuregrps": [
                        {"grpid": 1, "date": 1704067200, "measures": [{"type": 4, "value": 179, "unit": -2}]},
                        {"grpid": 2, "date": 1780041600, "measures": [{"type": 4, "value": 180, "unit": -2}]},
                    ]
                }
            )
        return FakeResponse({"measuregrps": []})


class SessionProvider:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def Session(self) -> "SessionProvider":
        return self

    def __enter__(self) -> FakeSession:
        return self.session

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None


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
    sleep: list[str] | None = None,
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
    if sleep is not None:
        _write_csv_lines(
            withings_dir / "sleep.csv",
            "source,source_id,start_time,end_time,timezone,wake_date,total_sleep_min,time_in_bed_min,awake_min,awake_count,sleep_score,sleep_efficiency,light_sleep_min,deep_sleep_min,rem_sleep_min",
            sleep,
        )


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
                        {"type": 9, "value": 7900, "unit": -2},
                        {"type": 10, "value": 12100, "unit": -2},
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
        self.assertEqual(rows[3]["type_name"], "diastolic_blood_pressure")
        self.assertEqual(rows[3]["value"], "79.00")
        self.assertEqual(rows[3]["unit"], "mmHg")
        self.assertEqual(rows[4]["type_name"], "systolic_blood_pressure")
        self.assertEqual(rows[4]["value"], "121.00")

    def test_builds_authorization_url_with_activity_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n\n[plugin.withings]\n', encoding="utf-8")
            config = load_config(config_path)

            url = authorization_url(
                config,
                redirect_uri="https://example.test/callback",
                state="state",
                client_id="client-id",
            )

            self.assertIn("client_id=client-id", url)
            self.assertIn("scope=user.metrics%2Cuser.activity", url)
            self.assertIn("redirect_uri=https%3A%2F%2Fexample.test%2Fcallback", url)

    def test_parses_authorization_code_from_redirect_url_or_raw_code(self) -> None:
        self.assertEqual(
            parse_authorization_code("https://callback.example/withings?state=x&code=abc123", expected_state="x"),
            "abc123",
        )
        self.assertEqual(parse_authorization_code("abc123"), "abc123")

    def test_rejects_raw_authorization_code_when_state_expected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "full Withings redirect URL"):
            parse_authorization_code("abc123", expected_state="expected")

    def test_rejects_authorization_code_with_mismatched_state(self) -> None:
        with self.assertRaisesRegex(SystemExit, "state mismatch"):
            parse_authorization_code(
                "https://callback.example/withings?state=wrong&code=abc123",
                expected_state="expected",
            )

    def test_rejects_redirect_url_without_authorization_code(self) -> None:
        with self.assertRaisesRegex(SystemExit, "missing authorization code"):
            parse_authorization_code("https://callback.example/withings?state=expected", expected_state="expected")

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

    def test_normalizes_sleep_summary_to_configured_timezone_and_wake_date(self) -> None:
        start = int(datetime.fromisoformat("2026-06-24T15:46:00+00:00").timestamp())
        end = int(datetime.fromisoformat("2026-06-24T22:28:00+00:00").timestamp())

        rows = normalize_sleep_summaries(
            [
                {
                    "id": 42,
                    "startdate": start,
                    "enddate": end,
                    "timezone": "Asia/Tokyo",
                    "data": {
                        "asleepduration": 24120,
                        "total_timeinbed": 25020,
                        "wakeupduration": 900,
                        "wakeupcount": 2,
                        "sleep_score": 81,
                        "sleep_efficiency": 0.96,
                    },
                }
            ]
        )

        self.assertEqual(rows[0]["source_id"], "42")
        self.assertEqual(rows[0]["start_time"], "2026-06-25T00:46:00+09:00")
        self.assertEqual(rows[0]["end_time"], "2026-06-25T07:28:00+09:00")
        self.assertEqual(rows[0]["wake_date"], "2026-06-25")
        self.assertEqual(rows[0]["total_sleep_min"], "402.00")
        self.assertEqual(rows[0]["awake_min"], "15.00")
        self.assertEqual(rows[0]["sleep_efficiency"], "0.96")

    def test_fetches_withings_sleep_summaries_in_date_windows(self) -> None:
        session = FakeSession()

        body = fetch_sleep_summaries_windowed(
            session,
            "access",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 4, 15),
        )

        self.assertEqual(body, {"series": []})
        sleep_calls = [
            call
            for call in session.calls
            if call.get("data", {}).get("action") == "getsummary"
        ]
        self.assertEqual(len(sleep_calls), 2)
        self.assertIn("total_sleep_time", sleep_calls[0]["data"]["data_fields"])

    def test_fetches_withings_sleep_summaries_with_pagination(self) -> None:
        class PagedSleepSession(FakeSession):
            def post(self, *args: object, **kwargs: object) -> FakeResponse:
                self.calls.append(kwargs)
                data = kwargs.get("data", {})
                if isinstance(data, dict) and data.get("action") == "getsummary":
                    start = int(datetime.fromisoformat("2026-06-24T23:00:00+09:00").timestamp())
                    if data.get("offset") == 10:
                        return FakeResponse(
                            {
                                "series": [
                                    {
                                        "id": "summary-2",
                                        "startdate": start + 86400,
                                        "enddate": start + 90000,
                                        "data": {"total_sleep_time": 3600},
                                    }
                                ],
                                "more": False,
                                "offset": 10,
                            }
                        )
                    return FakeResponse(
                        {
                            "series": [
                                {
                                    "id": "summary-1",
                                    "startdate": start,
                                    "enddate": start + 3600,
                                    "data": {"total_sleep_time": 3600},
                                }
                            ],
                            "more": True,
                            "offset": 10,
                        }
                    )
                if isinstance(data, dict) and data.get("action") == "get":
                    return FakeResponse({"model": 16, "series": []})
                return FakeResponse({"measuregrps": []})

        session = PagedSleepSession()

        body = fetch_sleep_summaries_windowed(
            session,
            "access",
            start_date=date(2026, 6, 25),
            end_date=date(2026, 6, 26),
        )

        self.assertEqual([summary["id"] for summary in body["series"]], ["summary-1", "summary-2"])
        summary_calls = [
            call
            for call in session.calls
            if call.get("data", {}).get("action") == "getsummary"
        ]
        self.assertEqual(summary_calls[1]["data"]["offset"], 10)

    def test_summarizes_sleep_states_for_local_wake_date(self) -> None:
        start = int(datetime.fromisoformat("2026-06-24T23:00:00+09:00").timestamp())
        states = [
            {"startdate": start, "enddate": start + 600, "state": 0},
            {"startdate": start + 600, "enddate": start + 4200, "state": 1},
            {"startdate": start + 4200, "enddate": start + 6000, "state": 2},
            {"startdate": start + 6000, "enddate": start + 7800, "state": 3},
        ]

        summary = summarize_sleep_states(states, date(2026, 6, 25))

        assert summary is not None
        self.assertEqual(summary["startdate"], start)
        self.assertEqual(summary["enddate"], start + 7800)
        self.assertEqual(summary["timezone"], "Asia/Tokyo")
        self.assertEqual(summary["data"]["total_timeinbed"], 7800)
        self.assertEqual(summary["data"]["total_sleep_time"], 7200)
        self.assertEqual(summary["data"]["wakeupduration"], 600)
        self.assertEqual(summary["data"]["lightsleepduration"], 3600)
        self.assertEqual(summary["data"]["deepsleepduration"], 1800)
        self.assertEqual(summary["data"]["remsleepduration"], 1800)

    def test_sleep_summary_fetch_falls_back_to_state_data(self) -> None:
        class SleepStateSession(FakeSession):
            def post(self, *args: object, **kwargs: object) -> FakeResponse:
                self.calls.append(kwargs)
                data = kwargs.get("data", {})
                if isinstance(data, dict) and data.get("action") == "getsummary":
                    return FakeResponse({"series": []})
                if isinstance(data, dict) and data.get("action") == "get":
                    start = int(datetime.fromisoformat("2026-06-24T23:00:00+09:00").timestamp())
                    return FakeResponse(
                        {
                            "model": 16,
                            "series": [
                                {"startdate": start, "enddate": start + 600, "state": 0},
                                {"startdate": start + 600, "enddate": start + 4200, "state": 1},
                            ],
                        }
                    )
                return FakeResponse({"measuregrps": []})

        session = SleepStateSession()

        body = fetch_sleep_summaries_windowed(
            session,
            "access",
            start_date=date(2026, 6, 25),
            end_date=date(2026, 6, 25),
        )

        self.assertEqual(len(body["series"]), 1)
        self.assertEqual(body["series"][0]["data"]["total_sleep_time"], 3600)
        self.assertEqual(body["fallback_source"], "sleep_get")
        self.assertEqual(
            [call["data"]["action"] for call in session.calls],
            ["getsummary", "get"],
        )

    def test_merges_sleep_rows_idempotently(self) -> None:
        rows = merge_sleep_rows(
            [{"source": "withings", "source_id": "1", "wake_date": "2026-06-24"}],
            [
                {"source": "withings", "source_id": "1", "wake_date": "2026-06-25"},
                {"source": "withings", "source_id": "2", "wake_date": "2026-06-26"},
            ],
        )

        self.assertEqual([row["source_id"] for row in rows], ["1", "2"])
        self.assertEqual(rows[0]["wake_date"], "2026-06-25")

    def test_empty_sleep_response_preserves_existing_csv_and_reports_api_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config = load_config(write_config(root))
            write_withings_csvs(
                data_dir,
                sleep=[
                    "withings,1,2026-06-24T23:00:00+09:00,2026-06-25T07:00:00+09:00,Asia/Tokyo,2026-06-25,450.00,,,,,,,,",
                ],
            )
            existing = config.withings.sleep_csv.read_text(encoding="utf-8")
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                written = write_sleep(
                    config,
                    {"series": []},
                    raw_name="sleep_sync.json",
                    merge=True,
                )

            self.assertEqual(written, [config.withings.raw_dir / "sleep_sync.json"])
            self.assertEqual(config.withings.sleep_csv.read_text(encoding="utf-8"), existing)
            self.assertIn("may not be available through the public API", stderr.getvalue())
            self.assertIn("imported from Apple Health", stderr.getvalue())
            self.assertIn("Existing sleep.csv was preserved", stderr.getvalue())

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

[plugin.withings]
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

[plugin.withings]
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

[plugin.withings]
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

            with mock.patch("ingest.plugins.withings.sync_range", return_value=[]) as sync_range:
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
            config_path = write_config(root, "[plugin.withings]\nsync_days = 4")
            config = load_config(config_path)

            with mock.patch("ingest.plugins.withings.sync_range", return_value=[]) as sync_range:
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

            with mock.patch("ingest.plugins.withings.sync_range", return_value=[]) as sync_range:
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

    def test_fetches_latest_height_from_full_history(self) -> None:
        session = HeightSession()

        body = fetch_latest_height(session, "access", end_date=date(2026, 5, 29))

        self.assertEqual(
            body,
            {"measuregrps": [{"grpid": 2, "date": 1780041600, "measures": [{"type": 4, "value": 180, "unit": -2}]}]},
        )
        self.assertEqual(len(session.calls), 67)
        self.assertEqual(date.fromtimestamp(session.calls[0]["data"]["startdate"]), date(2010, 1, 1))
        for call in session.calls:
            self.assertEqual(call["data"]["meastypes"], "4")
            self.assertLessEqual(call["data"]["enddate"] - call["data"]["startdate"], 90 * 24 * 60 * 60)

    def test_detects_cached_height(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            measures_csv = Path(temp_dir) / "body_measures.csv"
            _write_csv_lines(
                measures_csv,
                "grpid,date,datetime_local,type,type_name,value,unit",
                ["1,2020-01-01,2020-01-01T00:00:00,4,height,1.80,m"],
            )

            self.assertTrue(has_cached_height(measures_csv))

    def test_sync_range_merges_latest_height_into_recent_measures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = write_config(root, extra="[plugin.withings]\n")
            write_auth_state(data_dir, {"access_token": "access"})
            config = load_config(config_path)
            session = HeightSession()

            with mock.patch("ingest.plugins.withings._requests", return_value=SessionProvider(session)):
                written = sync_range(
                    config,
                    date(2026, 5, 29),
                    date(2026, 5, 29),
                    raw_name="body_measures_sync.json",
                )

            self.assertIn(data_dir / "withings/body_measures.csv", written)
            with (data_dir / "withings/body_measures.csv").open(encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["type_name"], "height")
            self.assertEqual(rows[0]["value"], "1.80")

    def test_sync_range_skips_height_fetch_when_height_is_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = write_config(root, extra="[plugin.withings]\n")
            write_auth_state(data_dir, {"access_token": "access"})
            write_withings_csvs(
                data_dir,
                measures=["1,2020-01-01,2020-01-01T00:00:00,4,height,1.80,m"],
            )
            config = load_config(config_path)
            session = HeightSession()

            with mock.patch("ingest.plugins.withings._requests", return_value=SessionProvider(session)):
                sync_range(
                    config,
                    date(2026, 5, 29),
                    date(2026, 5, 29),
                    raw_name="body_measures_sync.json",
                )

            height_calls = [call for call in session.calls if call.get("data", {}).get("meastypes") == "4"]
            self.assertEqual(height_calls, [])

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
