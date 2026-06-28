from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from ingest.config import load_config
from ingest.sources import vitalsync


class FakeResponse:
    def __init__(self, data: dict[str, object], status_code: int = 200) -> None:
        self.data = data
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self.data


class FakeSession:
    def __init__(self) -> None:
        self.get_urls: list[str] = []

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.get_urls.append(url)
        records = blood_pressure_records() if "sample_type=blood_pressure" in url else sleep_records()
        return FakeResponse({"schema": "vitalsync.records.v1", "records": records})


class FakeRequests:
    RequestException = Exception

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def Session(self) -> FakeSession:
        return self.session


class VitalsyncTest(unittest.TestCase):
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

[vitalsync]
enabled = true
access_token = "access"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)
            session = FakeSession()

            with mock.patch("ingest.sources.vitalsync._requests", return_value=FakeRequests(session)):
                written = vitalsync.sync(config, end_date=date(2026, 6, 25))

            self.assertEqual(
                written,
                [
                    data_dir / "vitalsync/raw/sleep_analysis_sync.json",
                    data_dir / "vitalsync/sleep.csv",
                    data_dir / "vitalsync/raw/blood_pressure_sync.json",
                    data_dir / "vitalsync/blood_pressure.csv",
                ],
            )
            self.assertIn("sample_type=sleep_analysis", session.get_urls[0])
            self.assertIn("sample_type=blood_pressure", session.get_urls[1])
            sleep_csv = (data_dir / "vitalsync/sleep.csv").read_text(encoding="utf-8")
            self.assertIn("vitalsync,", sleep_csv)
            self.assertIn("390.00,450.00,30.00", sleep_csv)
            blood_pressure_csv = (data_dir / "vitalsync/blood_pressure.csv").read_text(encoding="utf-8")
            self.assertIn("121,79", blood_pressure_csv)

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

[vitalsync]
enabled = true
access_token = "access"
""".strip(),
                encoding="utf-8",
            )
            sleep_csv = data_dir / "vitalsync/sleep.csv"
            sleep_csv.parent.mkdir(parents=True)
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
