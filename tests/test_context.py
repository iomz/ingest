from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from life_log_sync.config import load_config
from life_log_sync.context import generate_today_context, render_today_context


class ContextTest(unittest.TestCase):
    def test_renders_activity_summary(self) -> None:
        content = render_today_context(
            date(2026, 5, 29),
            [
                {
                    "start_date_local": "2026-05-29T06:30:00Z",
                    "name": "Morning Run",
                    "sport_type": "Run",
                    "distance_km": "5.00",
                    "moving_time_min": "30.00",
                },
                {
                    "start_date_local": "2026-05-29T18:00:00Z",
                    "name": "Evening Ride",
                    "sport_type": "Ride",
                    "distance_km": "20.50",
                    "moving_time_min": "45.00",
                },
            ],
            [
                {"date": "2026-05-29", "datetime_local": "2026-05-29T06:00:00", "type_name": "weight", "value": "70.50", "unit": "kg"},
                {"date": "2026-05-29", "type_name": "fat_ratio", "value": "18.42", "unit": "%"},
            ],
        )

        self.assertIn("# Today Context - 2026-05-29", content)
        self.assertIn("## Level 2: Derived Metrics", content)
        self.assertIn("- Activity level: High (25.50 km total)", content)
        self.assertIn("- Recovery compatibility: Poor (deterministic from activity level)", content)
        self.assertIn("- Total walking distance: 0.00 km", content)
        self.assertIn("- Total moving time: 75 min", content)
        self.assertIn("- Current weight: 70.50 kg", content)
        self.assertIn("- 7-day average weight: 70.50 kg", content)
        self.assertIn("- 30-day average weight: 70.50 kg", content)
        self.assertIn("- Weight trend: Unknown", content)
        self.assertIn("## Level 3: AI Handoff", content)
        self.assertIn(
            "High activity day with 2 Strava activities, 25.50 km total, "
            "0.00 km walking, and 75 min moving time.",
            content,
        )
        self.assertIn("- Activities: 2", content)
        self.assertIn("- Distance: 25.50 km", content)
        self.assertIn("- Moving time: 75 min", content)
        self.assertIn("- Types: Ride, Run", content)
        self.assertIn("- Run: Morning Run (5.00 km, 30 min)", content)
        self.assertIn("## Withings", content)
        self.assertIn("- weight: 70.50 kg", content)
        self.assertIn("- fat_ratio: 18.42 %", content)

    def test_renders_light_walking_derived_metrics(self) -> None:
        content = render_today_context(
            date(2026, 5, 29),
            [
                {
                    "start_date_local": "2026-05-29T12:30:00Z",
                    "name": "Lunch Walk",
                    "sport_type": "Walk",
                    "distance_km": "4.00",
                    "moving_time_min": "50.00",
                }
            ],
        )

        self.assertIn("- Activity level: Light (4.00 km total)", content)
        self.assertIn("- Recovery compatibility: Good (deterministic from activity level)", content)
        self.assertIn("- Total walking distance: 4.00 km", content)
        self.assertIn("- Current weight: No Withings weight available", content)

    def test_renders_moderate_derived_metrics(self) -> None:
        content = render_today_context(
            date(2026, 5, 29),
            [
                {
                    "start_date_local": "2026-05-29T06:30:00Z",
                    "name": "Morning Run",
                    "sport_type": "Run",
                    "distance_km": "8.00",
                    "moving_time_min": "55.00",
                }
            ],
        )

        self.assertIn("- Activity level: Moderate (8.00 km total)", content)
        self.assertIn("- Recovery compatibility: Acceptable (deterministic from activity level)", content)

    def test_renders_none_derived_metrics_without_activities(self) -> None:
        content = render_today_context(date(2026, 5, 29), [])

        self.assertIn("- Activity level: None (0.00 km total)", content)
        self.assertIn("- Recovery compatibility: Good (deterministic from activity level)", content)
        self.assertIn("- Total walking distance: 0.00 km", content)
        self.assertIn("No Strava activities found for this date.", content)

    def test_renders_weight_trend_from_historical_measures(self) -> None:
        historical_measures = [
            {"date": "2026-05-16", "datetime_local": "2026-05-16T06:00:00", "type_name": "weight", "value": "72.00", "unit": "kg"},
            {"date": "2026-05-17", "datetime_local": "2026-05-17T06:00:00", "type_name": "weight", "value": "72.00", "unit": "kg"},
            {"date": "2026-05-18", "datetime_local": "2026-05-18T06:00:00", "type_name": "weight", "value": "72.00", "unit": "kg"},
            {"date": "2026-05-19", "datetime_local": "2026-05-19T06:00:00", "type_name": "weight", "value": "72.00", "unit": "kg"},
            {"date": "2026-05-20", "datetime_local": "2026-05-20T06:00:00", "type_name": "weight", "value": "72.00", "unit": "kg"},
            {"date": "2026-05-21", "datetime_local": "2026-05-21T06:00:00", "type_name": "weight", "value": "72.00", "unit": "kg"},
            {"date": "2026-05-22", "datetime_local": "2026-05-22T06:00:00", "type_name": "weight", "value": "72.00", "unit": "kg"},
            {"date": "2026-05-23", "datetime_local": "2026-05-23T06:00:00", "type_name": "weight", "value": "71.00", "unit": "kg"},
            {"date": "2026-05-24", "datetime_local": "2026-05-24T06:00:00", "type_name": "weight", "value": "71.00", "unit": "kg"},
            {"date": "2026-05-25", "datetime_local": "2026-05-25T06:00:00", "type_name": "weight", "value": "71.00", "unit": "kg"},
            {"date": "2026-05-26", "datetime_local": "2026-05-26T06:00:00", "type_name": "weight", "value": "71.00", "unit": "kg"},
            {"date": "2026-05-27", "datetime_local": "2026-05-27T06:00:00", "type_name": "weight", "value": "71.00", "unit": "kg"},
            {"date": "2026-05-28", "datetime_local": "2026-05-28T06:00:00", "type_name": "weight", "value": "71.00", "unit": "kg"},
            {"date": "2026-05-29", "datetime_local": "2026-05-29T06:00:00", "type_name": "weight", "value": "71.00", "unit": "kg"},
        ]

        content = render_today_context(date(2026, 5, 29), [], historical_measures, historical_measures)

        self.assertIn("- Current weight: 71.00 kg", content)
        self.assertIn("- 7-day average weight: 71.00 kg", content)
        self.assertIn("- 30-day average weight: 71.50 kg", content)
        self.assertIn("- Weight trend: Decreasing", content)

    def test_generates_today_context_from_strava_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "life-log-sync.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[strava]
access_token = "access"
""".strip(),
                encoding="utf-8",
            )
            csv_path = data_dir / "strava/activities.csv"
            csv_path.parent.mkdir(parents=True)
            csv_path.write_text(
                "\n".join(
                    [
                        "id,start_date_local,name,sport_type,distance_km,moving_time_min",
                        "1,2026-05-29T06:30:00Z,Morning Run,Run,5.00,30.00",
                        "2,2026-05-29T18:00:00Z,Evening Walk,Walk,2.00,25.00",
                        "3,2026-05-28T06:30:00Z,Yesterday Run,Run,3.00,20.00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            withings_csv_path = data_dir / "withings/body_measures.csv"
            withings_csv_path.parent.mkdir(parents=True)
            withings_csv_path.write_text(
                "\n".join(
                    [
                        "grpid,date,datetime_local,type,type_name,value,unit",
                        "1,2026-05-29,2026-05-29T06:00:00,1,weight,70.50,kg",
                        "2,2026-05-28,2026-05-28T06:00:00,1,weight,71.00,kg",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_today_context(config, date(2026, 5, 29))

            self.assertEqual(written, data_dir / "generated/today_context.md")
            content = written.read_text(encoding="utf-8")
            self.assertIn("Morning Run", content)
            self.assertIn("Evening Walk", content)
            self.assertIn("- Activities: 2", content)
            self.assertIn("- Distance: 7.00 km", content)
            self.assertIn("- Moving time: 55 min", content)
            self.assertIn("- Total walking distance: 2.00 km", content)
            self.assertNotIn("Yesterday Run", content)
            self.assertIn("- weight: 70.50 kg", content)
            self.assertNotIn("71.00", content)

    def test_handles_missing_strava_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "life-log-sync.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[strava]
access_token = "access"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_today_context(config, date(2026, 5, 29))

            self.assertIn("No Strava activities found", written.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
