from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from life_log_sync.config import load_config
from life_log_sync.sources.withings import (
    fetch_body_measures_windowed,
    merge_measure_rows,
    normalize_measure_groups,
    write_measures,
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
        return FakeResponse({"measuregrps": []})


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


if __name__ == "__main__":
    unittest.main()
