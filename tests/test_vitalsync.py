from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from ingest.config import load_config
from ingest.plugins import vitalsync


def write_auth_state(data_dir: Path, state: dict[str, object]) -> None:
    path = data_dir / "vitalsync/auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


class FakeResponse:
    def __init__(self, data: dict[str, object], status_code: int = 200) -> None:
        self.data = data
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self.data


class FakeSession:
    def __init__(
        self,
        token_response: dict[str, object] | None = None,
        get_responses: list[FakeResponse] | None = None,
    ) -> None:
        self.get_urls: list[str] = []
        self.get_headers: list[dict[str, object]] = []
        self.post_urls: list[str] = []
        self.post_jsons: list[dict[str, object]] = []
        self.get_responses = get_responses or []
        self.token_response = token_response or {
            "access_token": "new-access",
            "expires_at": "2026-06-29T12:00:00Z",
        }

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.get_urls.append(url)
        headers = kwargs.get("headers")
        if isinstance(headers, dict):
            self.get_headers.append(headers)
        if self.get_responses:
            return self.get_responses.pop(0)
        if "sample_type=blood_pressure" in url:
            records = blood_pressure_records()
        elif "sample_type=daily_step_count" in url:
            records = daily_step_count_records()
        elif "sample_type=step_count" in url:
            records = step_count_records()
        else:
            records = sleep_records()
        return FakeResponse({"schema": "vitalsync.records.v1", "records": records})

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.post_urls.append(url)
        body = kwargs.get("json")
        if isinstance(body, dict):
            self.post_jsons.append(body)
        return FakeResponse(self.token_response)


class FakeRequests:
    RequestException = Exception

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def Session(self) -> FakeSession:
        return self.session


class VitalsyncTest(unittest.TestCase):
    def test_sync_warns_when_plugin_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text("[plugin.vitalsync]\nenabled = false\n", encoding="utf-8")
            config = load_config(config_path)
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                written = vitalsync.sync(config, end_date=date(2026, 6, 25))

            self.assertEqual(written, [])
            self.assertIn("plugin.vitalsync.enabled is false", stderr.getvalue())

    def test_normalizes_sleep_cycle_records_without_double_counting_in_bed(self) -> None:
        rows = vitalsync.normalize_sleep_analysis_records(
            sleep_records(),
            local_timezone=ZoneInfo("Asia/Tokyo"),
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "vitalsync")
        self.assertEqual(row["wake_date"], "2026-06-25")
        self.assertEqual(row["start_time"], "2026-06-24T23:00:00+09:00")
        self.assertEqual(row["end_time"], "2026-06-25T06:30:00+09:00")
        self.assertEqual(row["time_in_bed_min"], "450.00")
        self.assertEqual(row["total_sleep_min"], "390.00")
        self.assertEqual(row["awake_min"], "30.00")
        self.assertEqual(row["awake_count"], "1")
        self.assertEqual(row["light_sleep_min"], "210.00")
        self.assertEqual(row["deep_sleep_min"], "90.00")
        self.assertEqual(row["rem_sleep_min"], "90.00")
        self.assertEqual(row["sleep_efficiency"], "0.87")

    def test_filters_non_sleep_cycle_records_by_default(self) -> None:
        rows = vitalsync.normalize_sleep_analysis_records(
            [
                {
                    "sample_type": "sleep_analysis",
                    "source_id": "other-1",
                    "source_bundle_id": "com.example.other",
                    "source_name": "Other",
                    "start_time": "2026-06-24T14:00:00Z",
                    "end_time": "2026-06-24T15:00:00Z",
                    "value": {"category": "asleep_core"},
                }
            ],
            local_timezone=ZoneInfo("Asia/Tokyo"),
        )

        self.assertEqual(rows, [])

    def test_normalizes_blood_pressure_records_to_local_date(self) -> None:
        rows = vitalsync.normalize_blood_pressure_records(
            blood_pressure_records(),
            local_timezone=ZoneInfo("Asia/Tokyo"),
        )

        self.assertEqual(
            rows,
            [
                {
                    "source": "vitalsync",
                    "source_id": "bp-1",
                    "date": "2026-06-25",
                    "datetime_local": "2026-06-25T07:30:00+09:00",
                    "systolic_mmHg": "121",
                    "diastolic_mmHg": "79",
                    "source_name": "Vitalsync",
                    "source_bundle_id": "com.apple.Health",
                }
            ],
        )

    def test_normalizes_step_count_records_to_daily_totals(self) -> None:
        rows = vitalsync.normalize_step_count_records(
            step_count_records(),
            local_timezone=ZoneInfo("Asia/Tokyo"),
        )

        self.assertEqual(
            rows,
            [
                {
                    "source": "vitalsync",
                    "date": "2026-06-25",
                    "step_count": "579",
                    "distance_km": "",
                }
            ],
        )

    def test_normalizes_daily_step_count_records_to_daily_totals(self) -> None:
        rows = vitalsync.normalize_step_count_records(
            daily_step_count_records()
            + [
                {
                    **daily_step_count_records()[0],
                    "source_id": "daily_step_count_duplicate_2026-06-25",
                }
            ],
            local_timezone=ZoneInfo("Asia/Tokyo"),
        )

        self.assertEqual(rows[0]["date"], "2026-06-25")
        self.assertEqual(rows[0]["step_count"], "9123")

    def test_normalizes_daily_step_count_with_sample_fallback_by_date(self) -> None:
        rows = vitalsync.normalize_step_count_records(
            [
                *step_count_records(),
                {
                    "sample_type": "step_count",
                    "source_id": "steps-3",
                    "start_time": "2026-06-25T15:00:00Z",
                    "end_time": "2026-06-25T15:15:00Z",
                    "value": {"quantity": 456},
                },
                *daily_step_count_records(),
            ],
            local_timezone=ZoneInfo("Asia/Tokyo"),
        )

        self.assertEqual(
            rows,
            [
                {
                    "source": "vitalsync",
                    "date": "2026-06-25",
                    "step_count": "9123",
                    "distance_km": "",
                },
                {
                    "source": "vitalsync",
                    "date": "2026-06-26",
                    "step_count": "456",
                    "distance_km": "",
                },
            ],
        )

    def test_normalizes_naive_step_count_timestamp_with_local_timezone(self) -> None:
        rows = vitalsync.normalize_step_count_records(
            [
                {
                    "sample_type": "step_count",
                    "source_id": "steps-naive",
                    "start_time": "2026-06-25T23:45:00",
                    "end_time": "2026-06-26T00:15:00",
                    "value": {"quantity": 123},
                },
            ],
            local_timezone=ZoneInfo("Asia/Tokyo"),
        )

        self.assertEqual(rows[0]["date"], "2026-06-25")
        self.assertEqual(rows[0]["step_count"], "123")

    def test_sync_fetches_records_and_writes_raw_and_sleep_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"
timezone = "Asia/Tokyo"

[plugin.vitalsync]
enabled = true
""".strip(),
                encoding="utf-8",
            )
            write_auth_state(data_dir, {"access_token": "access"})
            config = load_config(config_path)
            session = FakeSession()

            with mock.patch("ingest.plugins.vitalsync._requests", return_value=FakeRequests(session)):
                written = vitalsync.sync(config, end_date=date(2026, 6, 25))

            self.assertEqual(
                written,
                [
                    data_dir / "vitalsync/raw/sleep_analysis_sync.json",
                    data_dir / "vitalsync/sleep.csv",
                    data_dir / "vitalsync/raw/blood_pressure_sync.json",
                    data_dir / "vitalsync/blood_pressure.csv",
                    data_dir / "vitalsync/raw/step_count_sync.json",
                    data_dir / "vitalsync/raw/daily_step_count_sync.json",
                    data_dir / "vitalsync/steps.csv",
                ],
            )
            self.assertIn("sample_type=sleep_analysis", session.get_urls[0])
            self.assertIn("sample_type=blood_pressure", session.get_urls[1])
            self.assertIn("sample_type=step_count", session.get_urls[2])
            self.assertIn("sample_type=daily_step_count", session.get_urls[3])
            sleep_csv = (data_dir / "vitalsync/sleep.csv").read_text(encoding="utf-8")
            self.assertIn("vitalsync,", sleep_csv)
            self.assertIn("390.00,450.00,30.00", sleep_csv)
            blood_pressure_csv = (data_dir / "vitalsync/blood_pressure.csv").read_text(encoding="utf-8")
            self.assertIn("121,79", blood_pressure_csv)
            steps_csv = (data_dir / "vitalsync/steps.csv").read_text(encoding="utf-8")
            self.assertIn("vitalsync,2026-06-25,9123,", steps_csv)

    def test_sync_writes_empty_csv_headers_when_records_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"
timezone = "Asia/Tokyo"

[plugin.vitalsync]
enabled = true
""".strip(),
                encoding="utf-8",
            )
            write_auth_state(data_dir, {"access_token": "access"})
            config = load_config(config_path)
            session = FakeSession(
                get_responses=[
                    FakeResponse({"schema": "vitalsync.records.v1", "records": []}),
                    FakeResponse({"schema": "vitalsync.records.v1", "records": []}),
                    FakeResponse({"schema": "vitalsync.records.v1", "records": []}),
                    FakeResponse({"schema": "vitalsync.records.v1", "records": []}),
                ],
            )

            with mock.patch("ingest.plugins.vitalsync._requests", return_value=FakeRequests(session)):
                written = vitalsync.sync(config, end_date=date(2026, 6, 25))

            self.assertIn(data_dir / "vitalsync/sleep.csv", written)
            self.assertIn(data_dir / "vitalsync/blood_pressure.csv", written)
            self.assertIn(data_dir / "vitalsync/steps.csv", written)
            self.assertEqual(
                (data_dir / "vitalsync/sleep.csv").read_text(encoding="utf-8").splitlines(),
                [
                    "source,source_id,start_time,end_time,timezone,wake_date,total_sleep_min,time_in_bed_min,awake_min,awake_count,sleep_score,sleep_efficiency,light_sleep_min,deep_sleep_min,rem_sleep_min"
                ],
            )
            self.assertEqual(
                (data_dir / "vitalsync/blood_pressure.csv").read_text(encoding="utf-8").splitlines(),
                ["source,source_id,date,datetime_local,systolic_mmHg,diastolic_mmHg,source_name,source_bundle_id"],
            )
            self.assertEqual(
                (data_dir / "vitalsync/steps.csv").read_text(encoding="utf-8").splitlines(),
                ["source,date,step_count,distance_km"],
            )

    def test_sync_refreshes_and_retries_once_after_records_401(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"
timezone = "Asia/Tokyo"

[plugin.vitalsync]
enabled = true
""".strip(),
                encoding="utf-8",
            )
            write_auth_state(
                data_dir,
                {
                    "client_id": "client",
                    "refresh_token": "refresh",
                    "access_token": "revoked-access",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
            config = load_config(config_path)
            session = FakeSession(
                token_response={
                    "access_token": "new-access",
                    "expires_at": "2099-01-02T00:00:00Z",
                },
                get_responses=[
                    FakeResponse({"message": "token missing, expired, or revoked"}, status_code=401),
                    FakeResponse({"schema": "vitalsync.records.v1", "records": sleep_records()}),
                    FakeResponse({"schema": "vitalsync.records.v1", "records": blood_pressure_records()}),
                    FakeResponse({"schema": "vitalsync.records.v1", "records": step_count_records()}),
                    FakeResponse({"schema": "vitalsync.records.v1", "records": daily_step_count_records()}),
                ],
            )

            with mock.patch("ingest.plugins.vitalsync._requests", return_value=FakeRequests(session)):
                written = vitalsync.sync(config, end_date=date(2026, 6, 25))

            self.assertEqual(len(written), 7)
            self.assertEqual(session.post_urls, ["https://api.sazanka.io/vitalsync/v1/tokens/refresh"])
            self.assertEqual(session.post_jsons, [{"refresh_token": "refresh", "client_id": "client"}])
            self.assertEqual(
                session.get_headers,
                [
                    {"Authorization": "Bearer revoked-access"},
                    {"Authorization": "Bearer new-access"},
                    {"Authorization": "Bearer new-access"},
                    {"Authorization": "Bearer new-access"},
                    {"Authorization": "Bearer new-access"},
                ],
            )
            updated = load_config(config_path)
            self.assertEqual(updated.vitalsync.access_token, "new-access")

    def test_sync_start_uses_fallback_when_blood_pressure_cache_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"
timezone = "Asia/Tokyo"

[plugin.vitalsync]
enabled = true
""".strip(),
                encoding="utf-8",
            )
            write_auth_state(data_dir, {"access_token": "access"})
            sleep_csv = data_dir / "vitalsync/sleep.csv"
            sleep_csv.parent.mkdir(parents=True, exist_ok=True)
            sleep_csv.write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,timezone,wake_date,total_sleep_min,time_in_bed_min,awake_min,awake_count,sleep_score,sleep_efficiency,light_sleep_min,deep_sleep_min,rem_sleep_min",
                        "vitalsync,s1,2026-06-26T23:00:00+09:00,2026-06-27T06:30:00+09:00,Asia/Tokyo,2026-06-27,390.00,450.00,30.00,1,,0.87,210.00,90.00,90.00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            self.assertEqual(vitalsync._sync_start_date(config, date(2026, 6, 27)), date(2026, 5, 29))

    def test_get_access_token_refreshes_when_refresh_token_exists_and_expiry_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            data_dir = root / "app-data"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[plugin.vitalsync]
endpoint = "https://receiver.example/vitalsync/v1"
""".strip(),
                encoding="utf-8",
            )
            write_auth_state(
                data_dir,
                {"client_id": "client", "refresh_token": "refresh", "access_token": "stale-access"},
            )
            config = load_config(config_path)
            session = FakeSession()

            access_token = vitalsync.get_access_token(session, config)

            self.assertEqual(access_token, "new-access")
            self.assertEqual(session.post_urls, ["https://receiver.example/vitalsync/v1/tokens/refresh"])
            self.assertEqual(session.post_jsons, [{"refresh_token": "refresh", "client_id": "client"}])
            updated = load_config(config_path)
            self.assertEqual(updated.vitalsync.access_token, "new-access")

    def test_register_client_exchanges_pairing_token_and_saves_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            data_dir = root / "app-data"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[plugin.vitalsync]
endpoint = "https://receiver.example/vitalsync/v1"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)
            session = FakeSession(
                {
                    "client_id": "client",
                    "refresh_token": "refresh",
                    "access_token": "access",
                    "expires_at": "2026-06-29T12:00:00Z",
                }
            )

            with mock.patch("ingest.plugins.vitalsync._requests", return_value=FakeRequests(session)):
                token = vitalsync.register_client(
                    config,
                    pairing_token="pair",
                    client_label="ingest on test",
                )

            self.assertEqual(token["access_token"], "access")
            self.assertEqual(session.post_urls, ["https://receiver.example/vitalsync/v1/clients/register"])
            self.assertEqual(
                session.post_jsons,
                [
                    {
                        "schema": "vitalsync.client_registration.v1",
                        "pairing_token": "pair",
                        "client_type": "ingest",
                        "client_label": "ingest on test",
                    }
                ],
            )
            updated = load_config(config_path)
            self.assertEqual(updated.vitalsync.client_id, "client")
            self.assertEqual(updated.vitalsync.refresh_token, "refresh")
            self.assertEqual(updated.vitalsync.access_token, "access")


def sleep_records() -> list[dict[str, object]]:
    return [
        _record("in-bed", "2026-06-24T14:00:00Z", "2026-06-24T21:30:00Z", "in_bed"),
        _record("core", "2026-06-24T14:30:00Z", "2026-06-24T18:00:00Z", "asleep_core"),
        _record("awake", "2026-06-24T18:00:00Z", "2026-06-24T18:30:00Z", "awake"),
        _record("deep", "2026-06-24T18:30:00Z", "2026-06-24T20:00:00Z", "asleep_deep"),
        _record("rem", "2026-06-24T20:00:00Z", "2026-06-24T21:30:00Z", "asleep_rem"),
    ]


def blood_pressure_records() -> list[dict[str, object]]:
    return [
        {
            "sample_type": "blood_pressure",
            "source_id": "bp-1",
            "source_bundle_id": "com.apple.Health",
            "source_name": "Vitalsync",
            "start_time": "2026-06-24T22:30:00Z",
            "end_time": "2026-06-24T22:30:00Z",
            "value": {"systolic": 121.2, "diastolic": 78.7, "correlation_id": "corr-1"},
        }
    ]


def step_count_records() -> list[dict[str, object]]:
    return [
        {
            "sample_type": "step_count",
            "source_id": "steps-1",
            "source_bundle_id": "com.apple.Health",
            "source_name": "Health",
            "start_time": "2026-06-24T22:30:00Z",
            "end_time": "2026-06-24T22:45:00Z",
            "value": {"quantity": 123.4},
        },
        {
            "sample_type": "step_count",
            "source_id": "steps-2",
            "source_bundle_id": "com.apple.Health",
            "source_name": "Health",
            "start_time": "2026-06-25T03:00:00Z",
            "end_time": "2026-06-25T03:15:00Z",
            "value": {"quantity": 456},
        },
    ]


def daily_step_count_records() -> list[dict[str, object]]:
    return [
        {
            "sample_type": "daily_step_count",
            "source_id": "daily_step_count_2026-06-25",
            "source_bundle_id": None,
            "source_name": "Apple Health Daily Steps",
            "start_time": "2026-06-25T00:00:00+09:00",
            "end_time": "2026-06-26T00:00:00+09:00",
            "timezone": "Asia/Tokyo",
            "value": {"quantity": 9123},
            "metadata": {"aggregate": "day", "date": "2026-06-25"},
        },
    ]


def _record(source_id: str, start: str, end: str, category: str) -> dict[str, object]:
    return {
        "sample_type": "sleep_analysis",
        "source_id": source_id,
        "source_bundle_id": "com.lexwarelabs.goodmorning",
        "source_name": "Sleep Cycle",
        "start_time": start,
        "end_time": end,
        "value": {"category": category},
    }
