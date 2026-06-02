from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from ingest.config import load_config
from ingest.context import generate_daily_context, render_daily_context, withings_activities_for_date


class ContextTest(unittest.TestCase):
    def test_renders_activity_summary(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "start_time": "2026-05-29T06:30:00Z",
                    "name": "Morning Run",
                    "activity_type": "Run",
                    "distance_km": "5.00",
                    "duration_min": "30.00",
                },
                {
                    "start_time": "2026-05-29T18:00:00Z",
                    "name": "Evening Ride",
                    "activity_type": "Ride",
                    "distance_km": "20.50",
                    "duration_min": "45.00",
                },
            ],
            [
                {"date": "2026-05-29", "datetime_local": "2026-05-29T06:00:00", "type_name": "weight", "value": "70.50", "unit": "kg"},
                {"date": "2026-05-29", "type_name": "fat_ratio", "value": "18.42", "unit": "%"},
            ],
        )

        self.assertIn("# Daily Context - 2026-05-29", content)
        self.assertIn("## Summary", content)
        self.assertIn("- Activity level: High", content)
        self.assertIn("- Recovery compatibility: Poor", content)
        self.assertIn("- Walking: 0.00 km / 0 min", content)
        self.assertIn("- 7-day avg walking: 0.00 km/day", content)
        self.assertIn("- 30-day avg walking: 0.00 km/day", content)
        self.assertIn("- Walking trend: Unknown", content)
        self.assertIn("- Current weight: 70.50 kg", content)
        self.assertIn("- 7-day avg weight: 70.50 kg", content)
        self.assertIn("- 30-day avg weight: 70.50 kg", content)
        self.assertIn("- Weight trend: Unknown", content)
        self.assertIn("## Handoff", content)
        self.assertIn(
            "High walking day with 2 primary activities, 0.00 km walking, and 75 min moving time.",
            content,
        )
        self.assertIn("- Run: Morning Run (5.00 km, 30 min)", content)
        self.assertIn("## Body", content)
        self.assertIn("- weight: 70.50 kg", content)
        self.assertIn("- fat_ratio: 18.42 %", content)
        self.assertNotIn("Assumptions:", content)
        self.assertNotIn("Total swimming distance: 0.00 km", content)

    def test_renders_light_walking_derived_metrics(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "start_time": "2026-05-29T12:30:00Z",
                    "name": "Lunch Walk",
                    "activity_type": "Walk",
                    "distance_km": "4.00",
                    "duration_min": "50.00",
                }
            ],
        )

        self.assertIn("- Activity level: Light", content)
        self.assertIn("- Recovery compatibility: Good", content)
        self.assertIn("- Walking: 4.00 km / 50 min", content)
        self.assertIn("- 7-day avg walking: 0.57 km/day", content)
        self.assertIn("- Walking trend: Unknown", content)
        self.assertIn("- Current weight: No Withings weight available", content)

    def test_renders_moderate_derived_metrics(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "start_time": "2026-05-29T06:30:00Z",
                    "name": "Morning Run",
                    "activity_type": "Run",
                    "distance_km": "8.00",
                    "duration_min": "55.00",
                }
            ],
        )

        self.assertIn("- Activity level: Moderate", content)
        self.assertIn("- Recovery compatibility: Acceptable", content)

    def test_renders_none_derived_metrics_without_activities(self) -> None:
        content = render_daily_context(date(2026, 5, 29), [])

        self.assertIn("- Activity level: None", content)
        self.assertIn("- Recovery compatibility: Good", content)
        self.assertIn("- Walking: 0.00 km / 0 min", content)
        self.assertIn("- Walking trend: Unknown", content)
        self.assertIn("No primary activities found for this date.", content)

    def test_renders_walking_trend_from_historical_activities(self) -> None:
        historical_activities = [
            {"start_time": "2026-05-16T06:00:00Z", "activity_type": "Walk", "distance_km": "1.00", "duration_min": "12.00"},
            {"start_time": "2026-05-17T06:00:00Z", "activity_type": "Walk", "distance_km": "1.00", "duration_min": "12.00"},
            {"start_time": "2026-05-18T06:00:00Z", "activity_type": "Walk", "distance_km": "1.00", "duration_min": "12.00"},
            {"start_time": "2026-05-19T06:00:00Z", "activity_type": "Walk", "distance_km": "1.00", "duration_min": "12.00"},
            {"start_time": "2026-05-20T06:00:00Z", "activity_type": "Walk", "distance_km": "1.00", "duration_min": "12.00"},
            {"start_time": "2026-05-21T06:00:00Z", "activity_type": "Walk", "distance_km": "1.00", "duration_min": "12.00"},
            {"start_time": "2026-05-22T06:00:00Z", "activity_type": "Walk", "distance_km": "1.00", "duration_min": "12.00"},
            {"start_time": "2026-05-23T06:00:00Z", "activity_type": "Walk", "distance_km": "2.00", "duration_min": "24.00"},
            {"start_time": "2026-05-24T06:00:00Z", "activity_type": "Walk", "distance_km": "2.00", "duration_min": "24.00"},
            {"start_time": "2026-05-25T06:00:00Z", "activity_type": "Walk", "distance_km": "2.00", "duration_min": "24.00"},
            {"start_time": "2026-05-26T06:00:00Z", "activity_type": "Walk", "distance_km": "2.00", "duration_min": "24.00"},
            {"start_time": "2026-05-27T06:00:00Z", "activity_type": "Walk", "distance_km": "2.00", "duration_min": "24.00"},
            {"start_time": "2026-05-28T06:00:00Z", "activity_type": "Walk", "distance_km": "2.00", "duration_min": "24.00"},
            {"start_time": "2026-05-29T06:00:00Z", "activity_type": "Walk", "distance_km": "2.00", "duration_min": "24.00"},
        ]

        content = render_daily_context(
            date(2026, 5, 29),
            withings_activities_for_date(historical_activities, date(2026, 5, 29)),
            historical_activities=historical_activities,
        )

        self.assertIn("- 7-day avg walking: 2.00 km/day", content)
        self.assertIn("- 30-day avg walking: 0.70 km/day", content)
        self.assertIn("- Walking trend: Increasing", content)

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

        content = render_daily_context(date(2026, 5, 29), [], historical_measures, historical_measures)

        self.assertIn("- Current weight: 71.00 kg", content)
        self.assertIn("- 7-day avg weight: 71.00 kg", content)
        self.assertIn("- 30-day avg weight: 71.50 kg", content)
        self.assertIn("- Weight trend: Decreasing", content)

    def test_context_counts_withings_activities(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "source_id": "walk-a",
                    "start_time": "2026-05-29T06:30:00+00:00",
                    "name": "Outdoor Walk",
                    "activity_type": "Walk",
                    "distance_km": "5.00",
                    "duration_min": "60.00",
                },
                {
                    "source_id": "walk-b",
                    "start_time": "2026-05-29T06:35:00+00:00",
                    "end_time": "2026-05-29T07:34:00+00:00",
                    "duration_min": "59",
                    "distance_km": "4.90",
                    "activity_type": "walk",
                    "raw_type": "walk",
                    "name": "Duplicate Walk",
                },
            ],
        )

        self.assertIn("- Sources: Withings", content)
        self.assertIn("- Activities: 2", content)
        self.assertIn("Outdoor Walk", content)
        self.assertIn("Duplicate Walk", content)

    def test_context_reports_swimming_separately(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "source_id": "walk",
                    "start_time": "2026-05-29T06:30:00+00:00",
                    "name": "Outdoor Walk",
                    "activity_type": "Walk",
                    "distance_km": "5.00",
                    "duration_min": "60.00",
                },
                {
                    "source_id": "swim",
                    "start_time": "2026-05-29T12:00:00+00:00",
                    "end_time": "2026-05-29T12:45:00+00:00",
                    "duration_min": "45",
                    "distance_km": "1.20",
                    "activity_type": "swim",
                    "raw_type": "swim",
                    "name": "Pool Swim",
                },
            ],
        )

        self.assertIn("- Activity level: Light", content)
        self.assertIn("- Walking: 5.00 km / 60 min", content)
        self.assertIn("- Swimming: 1.20 km / 45 min", content)
        self.assertIn("Swimming included 1.20 km and 45 min.", content)

    def test_translates_known_japanese_activity_names_for_display(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "id": "walk",
                    "start_time": "2026-05-29T06:30:00Z",
                    "name": "屋外で歩行",
                    "activity_type": "Walk",
                    "distance_km": "5.00",
                    "duration_min": "60.00",
                },
                {
                    "id": "run",
                    "start_time": "2026-05-29T18:30:00Z",
                    "name": "屋外ランニング",
                    "activity_type": "Run",
                    "distance_km": "6.00",
                    "duration_min": "40.00",
                },
            ],
        )

        self.assertIn("- Walk: Outdoor Walking (5.00 km, 60 min)", content)
        self.assertIn("- Run: Outdoor Running (6.00 km, 40 min)", content)
        self.assertNotIn("屋外で歩行", content)
        self.assertNotIn("屋外ランニング", content)

    def test_generates_daily_context_from_withings_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
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
            workouts_csv_path = data_dir / "withings/workouts.csv"
            workouts_csv_path.write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type",
                        "withings,w0,2026-05-28T10:00:00Z,2026-05-28T11:20:00Z,80.00,7.00,walk,walk",
                        "withings,w1,2026-05-29T06:30:00Z,2026-05-29T07:00:00Z,30.00,5.00,run,run",
                        "withings,w2,2026-05-29T18:05:00Z,2026-05-29T18:31:00Z,26.00,2.10,walk,walk",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 5, 29))

            self.assertEqual(written, data_dir / "generated/daily_context.md")
            content = written.read_text(encoding="utf-8")
            self.assertIn("withings:w1", content)
            self.assertIn("withings:w2", content)
            self.assertIn("- Sources: Withings", content)
            self.assertIn("- Activities: 2", content)
            self.assertIn("- Walking: 2.10 km / 26 min", content)
            self.assertIn("- 7-day avg walking: 1.30 km/day", content)
            self.assertNotIn("withings:w0", content)
            self.assertIn("- weight: 70.50 kg", content)
            self.assertNotIn("71.00", content)

    def test_handles_missing_withings_workouts_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 5, 29))

            self.assertIn("No primary activities found", written.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
