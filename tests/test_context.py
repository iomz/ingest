from __future__ import annotations

import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from rich.console import Console

from ingest.activities import normalize_withings_activity
from ingest.config import load_config
from ingest.context import (
    DailyState,
    generate_daily_context,
    render_daily_context,
    render_daily_terminal_context,
    withings_activities_for_date,
)


def weight_measure(measured_date: date, value: float, *, time: str = "06:00:00") -> dict[str, str]:
    return {
        "date": measured_date.isoformat(),
        "datetime_local": f"{measured_date.isoformat()}T{time}",
        "type_name": "weight",
        "value": f"{value:.2f}",
        "unit": "kg",
    }


def body_measure(measured_date: date, type_name: str, value: float, unit: str = "kg") -> dict[str, str]:
    return {
        "date": measured_date.isoformat(),
        "datetime_local": f"{measured_date.isoformat()}T06:00:00",
        "type_name": type_name,
        "value": f"{value:.2f}",
        "unit": unit,
    }


class ContextTest(unittest.TestCase):
    def test_terminal_handoff_wraps_before_rich_printing(self) -> None:
        activity = normalize_withings_activity(
            {
                "source_id": "walk",
                "start_time": "2026-05-29T08:00:00",
                "duration_min": "95.00",
                "distance_km": "3.62",
                "activity_type": "walk",
                "raw_type": "walk",
                "name": "Outdoor Walk",
            }
        )
        state = DailyState(
            target_date=date(2026, 5, 29),
            activities=[activity],
            measures=[
                {"date": "2026-05-29", "datetime_local": "2026-05-29T06:00:00", "type_name": "weight", "value": "100.40", "unit": "kg"},
            ],
            withings_activity_summaries=[{"date": "2026-05-29", "step_count": "10006"}],
            historical_withings_activity_summaries=[{"date": "2026-05-29", "step_count": "10006"}],
            historical_activities=[activity],
            historical_measures=[
                {"date": "2026-05-29", "datetime_local": "2026-05-29T06:00:00", "type_name": "weight", "value": "100.40", "unit": "kg"},
            ],
            hevy_sets=[],
        )
        output = io.StringIO()
        console = Console(file=output, width=140, color_system=None, force_terminal=False)

        render_daily_terminal_context(state, console)

        lines = output.getvalue().splitlines()
        handoff_start = lines.index("Machine Handoff") + 1
        handoff_lines = [line for line in lines[handoff_start:] if line]
        self.assertGreater(len(handoff_lines), 1)
        self.assertTrue(all(line.startswith("  ") for line in handoff_lines))
        self.assertTrue(all(len(line) <= 88 for line in handoff_lines))

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

        self.assertIn("# Physical Context - 2026-05-29", content)
        self.assertIn("## Daily Snapshot", content)
        self.assertIn("| Activity | High · score 31.8 · trend unknown |", content)
        self.assertIn("| Recovery | Caution · fatigue risk moderate · load 15.4 |", content)
        self.assertIn("| Movement | unavailable steps", content)
        self.assertNotIn("- Walking: 0.00 km / 0 min", content)
        self.assertIn("| Weight | 70.50 kg | 70.50 kg | 70.50 kg | Stable |", content)
        self.assertIn("## Machine Handoff", content)
        self.assertIn(
            "High activity day with 2 primary activities, 75 min moving time, and unavailable Withings steps.",
            content,
        )
        self.assertIn("- Run: Morning Run (5.00 km, 30 min)", content)
        self.assertIn("## Body", content)
        self.assertIn("| Weight | 70.50 kg |", content)
        self.assertIn("| Body fat | 18.42 % · 12.99 kg fat mass |", content)
        self.assertNotIn("| BMR |", content)
        self.assertNotIn("Assumptions:", content)
        self.assertNotIn("Total swimming distance: 0.00 km", content)

    def test_renders_bmr_from_fat_free_mass(self) -> None:
        measures = [
            body_measure(date(2026, 5, 29), "weight", 99.00),
            body_measure(date(2026, 5, 29), "fat_free_mass", 68.60),
        ]
        historical_measures = [
            body_measure(date(2024, 1, 1), "height", 1.80, "m"),
            *measures,
        ]

        content = render_daily_context(
            date(2026, 5, 29),
            [],
            measures,
            historical_measures,
        )

        self.assertNotIn("| height |", content)
        self.assertIn("| Fat-free mass | 68.60 kg |", content)
        self.assertIn("| ----- |  |\n| BMI | 30.56 |\n| BMR | 1852 kcal/day |", content)
        self.assertNotIn("| Derived metrics |", content)
        self.assertIn("| BMI | 30.56 |", content)
        self.assertIn("| BMR | 1852 kcal/day |", content)
        self.assertIn("Current weight is 99.00 kg. Weight trend is Unknown. BMI is 30.56. BMR is 1852 kcal/day.", content)

    def test_rounds_bmr_to_nearest_integer(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [],
            [body_measure(date(2026, 5, 29), "fat_free_mass", 68.57)],
        )

        self.assertIn("| BMR | 1851 kcal/day |", content)

    def test_omits_bmr_without_fat_free_mass(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [],
            [body_measure(date(2026, 5, 29), "weight", 99.00)],
        )

        self.assertNotIn("| BMR |", content)

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

        self.assertIn("| Activity | Light · score 8.2 · trend unknown |", content)
        self.assertIn("| Recovery | Good · fatigue risk low · load 4.0 |", content)
        self.assertIn("| Movement | unavailable steps · 4.00 km walk |", content)
        self.assertIn("| Walking distance | 4.00 km | 0.57 km/day | 0.13 km/day | Above 30-day average |", content)
        self.assertIn("| Body | No Withings weight available · unknown |", content)
        self.assertIn("- Recovery load score: 4.0", content)

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

        self.assertIn("| Activity | Moderate · score 12.6 · trend unknown |", content)
        self.assertIn("| Recovery | Acceptable · fatigue risk low · load 10.0 |", content)

    def test_recovery_scores_high_walking_as_caution(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "start_time": "2026-05-29T06:30:00Z",
                    "name": "Long Walk",
                    "activity_type": "Walk",
                    "distance_km": "15.00",
                    "duration_min": "180.00",
                }
            ],
        )

        self.assertIn("- Compatibility: Caution", content)
        self.assertIn("- Fatigue risk: Moderate", content)
        self.assertIn("- Recovery load score: 15.0", content)
        self.assertIn("15.00 km walking", content)

    def test_renders_none_derived_metrics_without_activities(self) -> None:
        content = render_daily_context(date(2026, 5, 29), [])

        self.assertIn("| Activity | None · score unavailable · trend unknown |", content)
        self.assertIn("| Recovery | Good · fatigue risk low · load 0.0 |", content)
        self.assertIn("| Movement | unavailable steps |", content)
        self.assertNotIn("- Walking: 0.00 km / 0 min", content)
        self.assertNotIn("- Walking trend: Unknown", content)
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

        self.assertIn("| Walking distance | 2.00 km | 2.00 km/day | 0.70 km/day | Above 30-day average |", content)
        self.assertIn("| Activity score | 4.0 | 4.0/day | 1.4/day | Above 30-day average |", content)
        self.assertIn("| Activity | Light · score 4.0 · trend increasing |", content)
        self.assertIn("Walking trend is Increasing.", content)

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

        self.assertIn("| Weight | 71.00 kg | 71.00 kg | 71.50 kg | Below 30-day average |", content)
        self.assertIn("| Body | 71.00 kg · decreasing |", content)

    def test_renders_positive_estimated_deficit_for_weight_loss(self) -> None:
        historical_measures = [
            weight_measure(date(2026, 5, 1), 70.00),
            weight_measure(date(2026, 5, 31), 69.00),
        ]

        content = render_daily_context(date(2026, 5, 31), [], historical_measures, historical_measures)

        self.assertIn("| Estimated deficit | 257 kcal/day | 257 kcal/day | 257 kcal/day | Stable |", content)

    def test_renders_negative_estimated_deficit_for_weight_gain(self) -> None:
        historical_measures = [
            weight_measure(date(2026, 5, 1), 69.00),
            weight_measure(date(2026, 5, 31), 70.00),
        ]

        content = render_daily_context(date(2026, 5, 31), [], historical_measures, historical_measures)

        self.assertIn("| Estimated deficit | -257 kcal/day | -257 kcal/day | -257 kcal/day | Stable |", content)

    def test_renders_zero_estimated_deficit_for_flat_weight(self) -> None:
        historical_measures = [
            weight_measure(date(2026, 5, 1), 70.00),
            weight_measure(date(2026, 5, 31), 70.00),
        ]

        content = render_daily_context(date(2026, 5, 31), [], historical_measures, historical_measures)

        self.assertIn("| Estimated deficit | 0 kcal/day | 0 kcal/day | 0 kcal/day | Stable |", content)

    def test_omits_estimated_deficit_with_insufficient_weight_history(self) -> None:
        historical_measures = [
            weight_measure(date(2026, 5, 2), 70.00),
            weight_measure(date(2026, 5, 31), 69.00),
        ]

        content = render_daily_context(date(2026, 5, 31), [], historical_measures, historical_measures)

        self.assertNotIn("Estimated deficit", content)

    def test_ignores_invalid_weight_when_estimating_deficit(self) -> None:
        historical_measures = [
            weight_measure(date(2026, 5, 1), 70.00),
            weight_measure(date(2026, 5, 31), 69.00),
            {
                **weight_measure(date(2026, 5, 31), 0.00, time="07:00:00"),
                "value": "invalid",
            },
        ]

        content = render_daily_context(date(2026, 5, 31), [], historical_measures, historical_measures)

        self.assertIn("| Estimated deficit | 257 kcal/day | 257 kcal/day | 257 kcal/day | Stable |", content)

    def test_renders_estimated_deficit_rolling_averages(self) -> None:
        report_date = date(2026, 6, 30)
        historical_measures = []
        for offset in range(30):
            current_date = date(2026, 6, 1) + timedelta(days=offset)
            deficit = (offset + 1) * 10
            weight_change = (deficit * 30) / 7700
            historical_measures.append(weight_measure(current_date - timedelta(days=30), 80.00 + weight_change))
            historical_measures.append(weight_measure(current_date, 80.00))

        content = render_daily_context(report_date, [], historical_measures, historical_measures)

        self.assertIn("| Estimated deficit | 300 kcal/day | 270 kcal/day | 155 kcal/day | Above 30-day average |", content)
        self.assertIn("Estimated energy deficit is 300 kcal/day.", content)

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
        self.assertIn("- Activity count: 2", content)
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

        self.assertIn("| Activity | Moderate · score 13.8 · trend unknown |", content)
        self.assertIn("| Movement | unavailable steps · 5.00 km walk · 45 min swim |", content)
        self.assertIn("Swimming included 45 min.", content)
        self.assertIn("- swim: Pool Swim (45 min)", content)
        self.assertNotIn("1.20 km", content)

    def test_context_reports_strength_training_separately(self) -> None:
        content = render_daily_context(
            date(2026, 6, 5),
            [
                {
                    "source_id": "push",
                    "start_time": "2026-06-05T13:52:00",
                    "end_time": "2026-06-05T15:25:00",
                    "duration_min": "93.00",
                    "activity_type": "strength",
                    "raw_type": "strength",
                    "name": "Push Day",
                }
            ],
        )

        self.assertIn("| Activity | Moderate · score 7.8 · trend unknown |", content)
        self.assertIn("| Strength | Push Day · 93 min |", content)
        self.assertIn("Strength training included 1 workout and 93 min.", content)
        self.assertIn("### Workout", content)
        self.assertIn("- Push Day: 93 min", content)
        self.assertNotIn("unknown distance", content)

    def test_recovery_scores_heavy_strength_as_poor(self) -> None:
        content = render_daily_context(
            date(2026, 6, 5),
            [
                {
                    "source_id": "w1",
                    "start_time": "2026-06-05T13:52:00",
                    "end_time": "2026-06-05T15:26:00",
                    "duration_min": "94.00",
                    "activity_type": "strength",
                    "raw_type": "strength",
                    "name": "Full Body",
                }
            ],
            hevy_sets=[
                {
                    "workout_source_id": "w1",
                    "exercise": f"Exercise {index}",
                    "volume_kg": "500.74",
                }
                for index in range(1, 70)
            ],
        )

        self.assertIn("- Compatibility: Poor", content)
        self.assertIn("- Fatigue risk: High", content)
        self.assertIn("Full-body strength: 94 min, 69 sets, 69 exercises, 34551 kg", content)

    def test_recovery_scores_mixed_walking_and_strength_as_poor(self) -> None:
        content = render_daily_context(
            date(2026, 6, 5),
            [
                {
                    "source_id": "walk",
                    "start_time": "2026-06-05T08:00:00",
                    "duration_min": "100.00",
                    "distance_km": "5.89",
                    "activity_type": "walk",
                    "raw_type": "walk",
                    "name": "Outdoor Walk",
                },
                {
                    "source_id": "w1",
                    "start_time": "2026-06-05T13:52:00",
                    "end_time": "2026-06-05T15:26:00",
                    "duration_min": "94.00",
                    "activity_type": "strength",
                    "raw_type": "strength",
                    "name": "Full Body",
                },
            ],
            hevy_sets=[
                {
                    "workout_source_id": "w1",
                    "exercise": f"Exercise {index}",
                    "volume_kg": "500.74",
                }
                for index in range(1, 70)
            ],
        )

        self.assertIn("- Compatibility: Poor", content)
        self.assertIn("- Fatigue risk: High", content)
        self.assertIn("- Recovery load score: 39.0", content)
        self.assertIn("Mixed load day: walking + strength", content)

    def test_recovery_scores_cycling_and_strength_as_mixed_load(self) -> None:
        content = render_daily_context(
            date(2026, 6, 5),
            [
                {
                    "source_id": "ride",
                    "start_time": "2026-06-05T08:00:00",
                    "duration_min": "60.00",
                    "distance_km": "20.00",
                    "activity_type": "ride",
                    "raw_type": "ride",
                    "name": "Morning Ride",
                },
                {
                    "source_id": "w1",
                    "start_time": "2026-06-05T13:52:00",
                    "duration_min": "45.00",
                    "activity_type": "strength",
                    "raw_type": "strength",
                    "name": "Push Day",
                },
            ],
            hevy_sets=[
                {"workout_source_id": "w1", "exercise": "Bench Press", "volume_kg": "500"}
                for _ in range(20)
            ],
        )

        self.assertIn("- Compatibility: Caution", content)
        self.assertIn("- Recovery load score: 19.8", content)
        self.assertIn("Additional movement: 20.00 km cycling", content)
        self.assertIn("Mixed load day: strength + cycling", content)
        self.assertNotIn("walking + strength", content)

    def test_recovery_scores_swimming_and_walking_as_caution(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "source_id": "walk",
                    "start_time": "2026-05-29T06:30:00+00:00",
                    "name": "Outdoor Walk",
                    "activity_type": "Walk",
                    "distance_km": "6.00",
                    "duration_min": "72.00",
                },
                {
                    "source_id": "swim",
                    "start_time": "2026-05-29T12:00:00+00:00",
                    "duration_min": "60",
                    "distance_km": "1.20",
                    "activity_type": "swim",
                    "raw_type": "swim",
                    "name": "Pool Swim",
                },
            ],
        )

        self.assertIn("- Compatibility: Caution", content)
        self.assertIn("- Fatigue risk: Moderate", content)
        self.assertIn("- Recovery load score: 15.2", content)
        self.assertIn("Mixed load day: walking + swimming", content)

    def test_subjective_all_out_note_downgrades_recovery(self) -> None:
        content = render_daily_context(
            date(2026, 6, 5),
            [
                {
                    "source_id": "w1",
                    "start_time": "2026-06-05T13:52:00",
                    "duration_min": "60.00",
                    "activity_type": "strength",
                    "raw_type": "strength",
                    "name": "Push Day",
                    "notes": "オールアウト",
                }
            ],
            hevy_sets=[
                {"workout_source_id": "w1", "exercise": f"Exercise {index}", "volume_kg": "700"}
                for index in range(1, 41)
            ],
        )

        self.assertIn("- Compatibility: Poor", content)
        self.assertIn("- Fatigue risk: High", content)
        self.assertIn("Subjective all-out effort noted", content)

    def test_recovery_handles_missing_workout_set_data(self) -> None:
        content = render_daily_context(
            date(2026, 6, 5),
            [
                {
                    "source_id": "w1",
                    "start_time": "2026-06-05T13:52:00",
                    "duration_min": "45.00",
                    "activity_type": "strength",
                    "raw_type": "strength",
                    "name": "Push Day",
                }
            ],
        )

        self.assertIn("- Compatibility: Good", content)
        self.assertIn("Strength details unavailable; score uses duration only", content)

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
                        "source,source_id,start_time,end_time,duration_min,distance_km,step_count,activity_type,raw_type",
                        "withings,w0,2026-05-28T10:00:00Z,2026-05-28T11:20:00Z,80.00,7.00,9000,walk,walk",
                        "withings,w1,2026-05-29T06:30:00Z,2026-05-29T07:00:00Z,30.00,5.00,0,run,run",
                        "withings,w2,2026-05-29T18:05:00Z,2026-05-29T18:31:00Z,26.00,2.10,3456,walk,walk",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            activity_csv_path = data_dir / "withings/activity.csv"
            activity_csv_path.write_text(
                "\n".join(
                    [
                        "date,step_count,distance_km",
                        "2026-05-28,9000,7.00",
                        "2026-05-29,3456,2.10",
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
            self.assertIn("- Activity count: 2", content)
            self.assertIn("| Movement | 3456 steps · 2.10 km walk |", content)
            self.assertIn("| Walking distance | 2.10 km | 1.30 km/day | 0.30 km/day | Above 30-day average |", content)
            self.assertNotIn("withings:w0", content)
            self.assertIn("| Weight | 70.50 kg |", content)
            self.assertNotIn("71.00", content)

    def test_renders_zero_withings_steps_when_daily_activity_row_is_zero(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [],
            withings_activity_summaries=[{"date": "2026-05-29", "step_count": "0"}],
        )

        self.assertIn("| Movement | 0 steps |", content)

    def test_activity_score_uses_steps_when_no_workout_is_logged(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [],
            withings_activity_summaries=[{"date": "2026-05-29", "step_count": "6500"}],
        )

        self.assertIn("| Activity | Light · score 10.0 · trend unknown |", content)
        self.assertIn("| Movement | 6500 steps |", content)

    def test_activity_score_does_not_double_count_walk_steps(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "start_time": "2026-05-29T12:30:00Z",
                    "name": "Lunch Walk",
                    "activity_type": "Walk",
                    "distance_km": "5.00",
                    "duration_min": "60.00",
                    "step_count": "6500",
                }
            ],
            withings_activity_summaries=[{"date": "2026-05-29", "step_count": "6500"}],
        )

        self.assertIn("| Activity | Light · score 10.0 · trend unknown |", content)

    def test_activity_score_estimates_logged_run_steps_when_missing(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "start_time": "2026-05-29T06:30:00Z",
                    "name": "Morning Run",
                    "activity_type": "Run",
                    "distance_km": "5.00",
                    "duration_min": "30.00",
                }
            ],
            withings_activity_summaries=[{"date": "2026-05-29", "step_count": "6000"}],
        )

        self.assertIn("| Activity | Light · score 7.5 · trend unknown |", content)

    def test_handles_missing_withings_workouts_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 5, 29))

            self.assertIn("No primary activities found", written.read_text(encoding="utf-8"))

    def test_generates_daily_context_with_hevy_set_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            hevy_dir = data_dir / "hevy"
            hevy_dir.mkdir(parents=True)
            (hevy_dir / "workouts.csv").write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type,name",
                        "hevy,w1,2026-06-05T13:52:00,2026-06-05T15:25:00,93.00,,strength,strength,Full Body",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (hevy_dir / "sets.csv").write_text(
                "\n".join(
                    [
                        "source,source_id,workout_source_id,workout_name,start_time,exercise,set_index,set_type,weight_kg,reps,distance_km,duration_seconds,rpe,volume_kg",
                        "hevy,s1,w1,Full Body,2026-06-05T13:52:00,Squat,1,normal,80,5,,,,400",
                        "hevy,s2,w1,Full Body,2026-06-05T13:52:00,Squat,2,normal,80,5,,,,400",
                        "hevy,s3,w1,Full Body,2026-06-05T13:52:00,Pull Up,1,normal,,8,,,,0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 6, 5))

            content = written.read_text(encoding="utf-8")
            self.assertIn("### Workout", content)
            self.assertIn("- Full Body: 93 min", content)
            self.assertIn("  - Sets: 3", content)
            self.assertIn("  - Volume: 800 kg", content)
            self.assertIn("  - Squat: 2 sets, 800 kg (80 kg x 5, 80 kg x 5)", content)
            self.assertIn("  - Pull Up: 1 sets, 0 kg (8 reps)", content)
            self.assertNotIn("unknown distance", content)

    def test_ignores_existing_withings_category_16_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            withings_dir = data_dir / "withings"
            withings_dir.mkdir(parents=True)
            (withings_dir / "workouts.csv").write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type",
                        "withings,gym,2026-06-05T13:52:00,2026-06-05T15:25:00,93.00,0.32,category_16,category_16",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 6, 5))

            content = written.read_text(encoding="utf-8")
            self.assertNotIn("category_16", content)
            self.assertIn("- Sources: None", content)


if __name__ == "__main__":
    unittest.main()
