from __future__ import annotations

import io
import contextlib
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from rich.console import Console

from ingest.activities import normalize_suunto_activity, normalize_withings_activity
from ingest.config import load_config
from ingest.context import (
    DailyState,
    _styled_terminal_value,
    _terminal_distance_activity,
    _trend_direction_role,
    _weight_direction_role,
    generate_daily_context,
    render_daily_context,
    render_daily_terminal_context,
    withings_activities_for_date,
    _training_load_metrics,
    _training_load_history_label,
    _tsb_label,
)
from ingest.ui import terminal_theme


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
    def assert_no_custom_recovery(self, content: str) -> None:
        for wording in [
            "fatigue risk",
            "Compatibility:",
            "Recovery load score",
            "Recovery Flags",
            "Recovery compatibility",
            "load score",
        ]:
            self.assertNotIn(wording, content)

    def test_training_load_uses_report_date_end_state_and_rest_day_decay(self) -> None:
        activity = normalize_suunto_activity(
            {
                "start_time": "2026-06-01T08:00:00+09:00",
                "duration_min": "60",
                "activity_type": "run",
                "tss_score": "100",
            }
        )

        after_training = _training_load_metrics([activity], date(2026, 6, 1))
        after_one_rest_day = _training_load_metrics([activity], date(2026, 6, 2))
        after_two_rest_days = _training_load_metrics([activity], date(2026, 6, 3))

        self.assertIsNotNone(after_training)
        self.assertIsNotNone(after_one_rest_day)
        self.assertIsNotNone(after_two_rest_days)
        assert after_training is not None
        assert after_one_rest_day is not None
        assert after_two_rest_days is not None
        self.assertEqual(after_training.today_tss, 100.0)
        self.assertAlmostEqual(after_training.ctl, 2.35, places=2)
        self.assertAlmostEqual(after_training.atl, 13.31, places=2)
        self.assertAlmostEqual(after_training.tsb, -10.96, places=2)
        self.assertEqual(after_one_rest_day.today_tss, 0.0)
        self.assertLess(after_one_rest_day.ctl, after_training.ctl)
        self.assertLess(after_one_rest_day.atl, after_training.atl)
        self.assertLess(after_two_rest_days.ctl, after_one_rest_day.ctl)
        self.assertLess(after_two_rest_days.atl, after_one_rest_day.atl)

    def test_training_load_ignores_workouts_without_tss(self) -> None:
        missing_tss = normalize_suunto_activity(
            {
                "start_time": "2026-06-01T08:00:00+09:00",
                "duration_min": "60",
                "activity_type": "run",
            }
        )

        self.assertIsNone(_training_load_metrics([missing_tss], date(2026, 6, 2)))

    def test_tsb_labels_use_suunto_style_zones(self) -> None:
        self.assertEqual(_tsb_label(-30.1), "Too high intensity")
        self.assertEqual(_tsb_label(-30), "Fatigue (fitness improving)")
        self.assertEqual(_tsb_label(-10), "Balanced")
        self.assertEqual(_tsb_label(14.9), "Balanced")
        self.assertEqual(_tsb_label(15), "Recovery (fitness declining)")

    def test_training_load_history_labels_use_calendar_span(self) -> None:
        self.assertEqual(
            _training_load_history_label(6),
            "Training load history limited; ATL/TSB warming up",
        )
        self.assertEqual(
            _training_load_history_label(7),
            "Training load history limited; CTL warming up",
        )
        self.assertEqual(
            _training_load_history_label(41),
            "Training load history limited; CTL warming up",
        )
        self.assertEqual(
            _training_load_history_label(42),
            "Training load baseline available",
        )

        activity = normalize_suunto_activity(
            {
                "start_time": "2026-06-01T08:00:00+09:00",
                "duration_min": "60",
                "activity_type": "run",
                "tss_score": "100",
            }
        )
        six_days = _training_load_metrics([activity], date(2026, 6, 6))
        seven_days = _training_load_metrics([activity], date(2026, 6, 7))
        forty_two_days = _training_load_metrics([activity], date(2026, 7, 12))

        assert six_days is not None
        assert seven_days is not None
        assert forty_two_days is not None
        self.assertEqual(
            six_days.history_label,
            "Training load history limited; ATL/TSB warming up",
        )
        self.assertEqual(
            seven_days.history_label,
            "Training load history limited; CTL warming up",
        )
        self.assertEqual(
            forty_two_days.history_label,
            "Training load baseline available",
        )

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
        self.assertNotIn("\x1b[", output.getvalue())

    def test_explicit_load_source_does_not_backfill_empty_load_activities(self) -> None:
        activity = normalize_withings_activity(
            {
                "source_id": "walk",
                "start_time": "2026-05-29T08:00:00",
                "duration_min": "30.00",
                "activity_type": "walk",
                "raw_type": "walk",
                "name": "Outdoor Walk",
            }
        )

        state = DailyState(
            target_date=date(2026, 5, 29),
            activities=[activity],
            historical_activities=[activity],
            load_source="suunto",
        )

        self.assertEqual(state.load_activities, [])
        self.assertEqual(state.historical_load_activities, [])

    def test_terminal_value_styles_semantic_states(self) -> None:
        cases = [
            ("12.3 km", "12.3 km", "bright_cyan", None),
            (
                "Faster than 30-day average",
                "Faster than",
                "green",
                "positive",
            ),
            (
                "Slower than 30-day average",
                "Slower than",
                "yellow",
                "warning",
            ),
            ("Baseline forming", "Baseline forming", "yellow", None),
            (
                "Training load history limited",
                "Training load history limited",
                "yellow",
                None,
            ),
            ("ATL/TSB warming up", "warming up", "yellow", None),
            ("ATL/TSB warming\nup", "warming\nup", "yellow", None),
            (
                "Swimming pace baseline is still forming",
                "baseline is still forming",
                "yellow",
                None,
            ),
            ("steps unavailable", "unavailable", "dim", None),
            ("None", "None", "dim", None),
        ]

        for value, styled_text, expected_style, semantic_role in cases:
            with self.subTest(value=value):
                text = _styled_terminal_value(
                    value,
                    semantic_role=semantic_role,
                )
                matching_spans = [
                    span
                    for span in text.spans
                    if styled_text in text.plain[span.start:span.end]
                ]
                self.assertTrue(matching_spans)
                self.assertIn(
                    expected_style,
                    {str(span.style) for span in matching_spans},
                )

    def test_terminal_activity_source_id_is_dim(self) -> None:
        activity = normalize_withings_activity(
            {
                "source_id": "walk-123",
                "start_time": "2026-05-29T08:00:00",
                "duration_min": "30.00",
                "distance_km": "2.00",
                "activity_type": "walk",
            }
        )

        text = _terminal_distance_activity(activity)
        source_spans = [
            span
            for span in text.spans
            if "withings:walk-123" in text.plain[span.start:span.end]
        ]
        numeric_spans = [
            span
            for span in text.spans
            if text.plain[span.start:span.end] in {"2.00 km", "30 min"}
        ]

        self.assertEqual({str(span.style) for span in source_spans}, {"dim"})
        self.assertEqual(
            {str(span.style) for span in numeric_spans},
            {"bright_cyan"},
        )

    def test_weight_direction_role_follows_configured_goal(self) -> None:
        self.assertEqual(_weight_direction_role(98, 99, 100, "loss"), "positive")
        self.assertEqual(_weight_direction_role(102, 101, 100, "loss"), "warning")
        self.assertEqual(_weight_direction_role(102, 101, 100, "gain"), "positive")
        self.assertEqual(_weight_direction_role(98, 99, 100, "gain"), "warning")
        self.assertIsNone(_weight_direction_role(98, 99, 100, "maintenance"))

        maintenance_text = _styled_terminal_value(
            "Below 30d avg (-2%)",
        )
        self.assertNotIn(
            "yellow",
            {str(span.style) for span in maintenance_text.spans},
        )

        loss_text = _styled_terminal_value(
            "Below 30d avg (-2%)",
            semantic_role=_weight_direction_role(98, 99, 100, "loss"),
        )
        self.assertIn(
            "green",
            {str(span.style) for span in loss_text.spans},
        )

    def test_trend_direction_role_is_metric_aware(self) -> None:
        cases = [
            ("Swimming pace", "Baseline forming", "maintenance", "baseline_forming"),
            (
                "TSS",
                "Training load history limited",
                "maintenance",
                "limited_history",
            ),
            (
                "Swimming pace",
                "Faster than 30-day average",
                "maintenance",
                "positive",
            ),
            (
                "Cycling speed",
                "Slower than 30-day average",
                "maintenance",
                "warning",
            ),
            (
                "Estimated deficit",
                "Above 30d avg (+23%)",
                "maintenance",
                "positive",
            ),
            (
                "Estimated deficit",
                "Below 30d avg (-10%)",
                "maintenance",
                "warning",
            ),
        ]

        for metric, direction, goal, expected in cases:
            with self.subTest(metric=metric, direction=direction):
                self.assertEqual(
                    _trend_direction_role(
                        metric,
                        direction,
                        body_weight_goal=goal,
                        today=500,
                    ),
                    expected,
                )

        self.assertEqual(
            _trend_direction_role(
                "Weight",
                "Below 30d avg (-2%)",
                body_weight_goal="loss",
                today=98,
                avg_7d=99,
                avg_30d=100,
            ),
            "positive",
        )
        self.assertIsNone(
            _trend_direction_role(
                "Walking distance",
                "Above 30-day weekly average (+20%)",
                body_weight_goal="loss",
            )
        )

    def test_colorful_theme_applies_semantic_direction_roles(self) -> None:
        theme = terminal_theme("colorful")
        cases = [
            ("Baseline forming", "baseline_forming"),
            (
                "Training load history limited",
                "limited_history",
            ),
            ("Faster than 30-day average", "positive"),
            ("Slower than 30-day average", "warning"),
        ]

        for value, role in cases:
            with self.subTest(value=value):
                text = _styled_terminal_value(
                    value,
                    theme=theme,
                    semantic_role=role,
                )
                self.assertIn(
                    theme.style(role),
                    {str(span.style) for span in text.spans},
                )

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
        self.assertNotIn("| Activity |", content)
        self.assertNotIn("Activity score", content)
        self.assert_no_custom_recovery(content)
        self.assertIn("| Movement | 20.50 km ride · steps unavailable |", content)
        self.assertNotIn("| Strength |", content)
        self.assertNotIn("- Walking: 0.00 km / 0 min", content)
        self.assertIn("| Weight | 70.50 kg | 70.50 kg | 70.50 kg | Stable |", content)
        self.assertIn("## Machine Handoff", content)
        self.assertIn(
            "Recorded 2 primary activities, 75 min moving time, and unavailable steps.",
            content,
        )
        self.assertIn("- Run: Morning Run (5.00 km, 30 min)", content)
        self.assertIn("## Body", content)
        self.assertIn("| Weight | 70.50 kg / fat 18.42% (12.99 kg) / muscle Unknown |", content)
        self.assertIn("| Mass | FFM Unknown / water Unknown / bone Unknown |", content)
        self.assertNotIn("| Body fat |", content)
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
        self.assertIn("| Mass | FFM 68.60 kg / water Unknown / bone Unknown |", content)
        self.assertIn("| Index | BMI 30.56 / BMR 1852 kcal/day |", content)
        self.assertNotIn("| Fat-free mass |", content)
        self.assertNotIn("| ----- |", content)
        self.assertNotIn("| Derived metrics |", content)
        self.assertNotIn("| BMI | 30.56 |", content)
        self.assertNotIn("| BMR | 1852 kcal/day |", content)
        self.assertIn("Current weight is 99.00 kg. Weight trend is Unknown. BMI is 30.56. BMR is 1852 kcal/day.", content)

    def test_renders_vitalsync_blood_pressure_in_body_section(self) -> None:
        content = render_daily_context(
            date(2026, 6, 25),
            [],
            blood_pressure_records=[
                {
                    "source": "vitalsync",
                    "source_id": "bp-1",
                    "date": "2026-06-25",
                    "datetime_local": "2026-06-25T07:30:00+09:00",
                    "systolic_mmHg": "121",
                    "diastolic_mmHg": "79",
                }
            ],
        )

        self.assertIn("## Body", content)
        self.assertIn("| Blood pressure | 121/79 mmHg · 07:30 |", content)
        self.assertIn("  - Blood Pressure: Vitalsync", content)

    def test_renders_vitalsync_blood_pressure_with_withings_body_measures(self) -> None:
        content = render_daily_context(
            date(2026, 6, 25),
            [],
            measures=[body_measure(date(2026, 6, 25), "weight", 99.00)],
            historical_measures=[body_measure(date(2026, 6, 25), "weight", 99.00)],
            blood_pressure_records=[
                {
                    "source": "vitalsync",
                    "source_id": "bp-1",
                    "date": "2026-06-25",
                    "datetime_local": "2026-06-25T07:30:00+09:00",
                    "systolic_mmHg": "121",
                    "diastolic_mmHg": "79",
                }
            ],
        )

        self.assertIn("| Weight | 99.00 kg / fat Unknown / muscle Unknown |", content)
        self.assertIn("| Blood pressure | 121/79 mmHg · 07:30 |", content)
        self.assertIn("- Measurement: Withings", content)
        self.assertIn("  - Blood Pressure: Vitalsync", content)

    def test_renders_latest_vitalsync_waist_circumference_with_date(self) -> None:
        content = render_daily_context(
            date(2026, 6, 25),
            [],
            waist_circumference_records=[
                {
                    "date": "2026-06-24",
                    "datetime_local": "2026-06-24T07:30:00+09:00",
                    "waist_circumference_m": "0.83",
                },
                {
                    "date": "2026-06-25",
                    "datetime_local": "2026-06-25T07:30:00+09:00",
                    "waist_circumference_m": "0.82",
                },
            ],
        )

        self.assertIn("## Body", content)
        self.assertIn("| Waist circumference | 82.0 cm (2026-06-25) |", content)

    def test_rounds_bmr_to_nearest_integer(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [],
            [body_measure(date(2026, 5, 29), "fat_free_mass", 68.57)],
        )

        self.assertIn("| Index | BMR 1851 kcal/day |", content)

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

        self.assertNotIn("Activity score", content)
        self.assert_no_custom_recovery(content)
        self.assertIn("| Movement | unavailable steps · 4.00 km walk |", content)
        self.assertIn(
            "| Walking distance | 4.00 km | 4.00 km/week | 0.93 km/week | "
            "First recorded walk |",
            content,
        )
        self.assertIn("| Body | No Withings weight available · unknown |", content)

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

        self.assertNotIn("Activity score", content)
        self.assert_no_custom_recovery(content)

    def test_long_walk_does_not_generate_recovery_judgment(self) -> None:
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

        self.assert_no_custom_recovery(content)
        self.assertIn("15.00 km walking", content)

    def test_renders_none_derived_metrics_without_activities(self) -> None:
        content = render_daily_context(date(2026, 5, 29), [])

        self.assertNotIn("| Activity |", content)
        self.assertNotIn("Activity score", content)
        self.assert_no_custom_recovery(content)
        self.assertIn("| Movement | unavailable steps |", content)
        self.assertNotIn("- Walking: 0.00 km / 0 min", content)
        self.assertNotIn("- Walking trend: Unknown", content)
        self.assertIn("No primary activities found for this date.", content)

    def test_terminal_renders_without_activity_trends(self) -> None:
        state = DailyState(
            target_date=date(2026, 5, 29),
            activities=[],
            measures=[],
            withings_activity_summaries=[],
            historical_withings_activity_summaries=[],
            historical_activities=[],
            historical_measures=[],
            hevy_sets=[],
        )
        output = io.StringIO()

        render_daily_terminal_context(
            state,
            Console(file=output, width=140, color_system=None, force_terminal=False),
        )

        self.assertIn("Physical Context — 2026-05-29", output.getvalue())
        self.assertNotIn("Activity\n", output.getvalue())

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

        self.assertIn(
            "| Walking distance | 2.00 km | 14.00 km/week | 4.90 km/week | "
            "Above 30-day weekly average (+186%) |",
            content,
        )
        self.assertNotIn("Activity score", content)
        self.assertIn(
            "Walking distance is above 30-day weekly average (+186%).",
            content,
        )
        self.assertIn(
            "| Walking pace | 12:00 min/km | 12:00 min/km | 12:00 min/km | "
            "Near 30-day average |",
            content,
        )
        trends = content.split("## Trends", 1)[1].split("\n## ", 1)[0]
        self.assertIn("### Workout", trends)
        self.assertIn("### Performance", trends)
        self.assertIn("### Body", trends)
        self.assertNotIn("### Activity", trends)
        self.assertNotIn("#### Volume", trends)
        self.assertNotIn("### Training Load", trends)

    def test_walking_pace_uses_distance_weighted_aggregation_and_lower_is_faster(self) -> None:
        historical_activities = [
            {
                "start_time": "2026-05-27T06:00:00Z",
                "activity_type": "Walk",
                "distance_km": "1.00",
                "duration_min": "20.00",
            },
            {
                "start_time": "2026-05-28T06:00:00Z",
                "activity_type": "Walk",
                "distance_km": "9.00",
                "duration_min": "90.00",
            },
            {
                "start_time": "2026-05-29T06:00:00Z",
                "activity_type": "Walk",
                "distance_km": "5.00",
                "duration_min": "45.00",
            },
        ]

        content = render_daily_context(
            date(2026, 5, 29),
            withings_activities_for_date(historical_activities, date(2026, 5, 29)),
            historical_activities=historical_activities,
        )

        self.assertIn(
            "| Walking pace | 9:00 min/km | 10:20 min/km | 10:20 min/km | "
            "Faster than 30-day average |",
            content,
        )
        self.assertIn(
            "Walking pace was faster than 30-day average.",
            content,
        )

    def test_omits_pace_when_distance_is_missing(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "start_time": "2026-05-29T06:00:00Z",
                    "activity_type": "Run",
                    "duration_min": "30.00",
                }
            ],
        )

        self.assertNotIn("Running pace", content)

    def test_cycling_speed_uses_higher_is_faster_direction(self) -> None:
        historical_activities = [
            {
                "start_time": "2026-05-27T06:00:00Z",
                "activity_type": "Ride",
                "distance_km": "20.00",
                "duration_min": "60.00",
            },
            {
                "start_time": "2026-05-28T06:00:00Z",
                "activity_type": "Ride",
                "distance_km": "20.00",
                "duration_min": "60.00",
            },
            {
                "start_time": "2026-05-29T06:00:00Z",
                "activity_type": "Ride",
                "distance_km": "30.00",
                "duration_min": "60.00",
            },
        ]

        content = render_daily_context(
            date(2026, 5, 29),
            withings_activities_for_date(historical_activities, date(2026, 5, 29)),
            historical_activities=historical_activities,
        )

        self.assertIn(
            "| Cycling speed | 30.00 km/h | 23.33 km/h | 23.33 km/h | "
            "Faster than 30-day average |",
            content,
        )

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

    def test_snapshot_body_does_not_carry_forward_stale_weight(self) -> None:
        historical_measures = [
            weight_measure(date(2026, 6, 30), 97.00),
        ]

        content = render_daily_context(date(2026, 7, 1), [], [], historical_measures)

        self.assertIn("| Body | No Withings weight available · unknown |", content)
        self.assertIn("| Weight | No Withings weight available | 97.00 kg | 97.00 kg | Unknown |", content)

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

    def test_context_deduplicates_overlapping_withings_activities(self) -> None:
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

        self.assertIn("- Activity: Suunto", content)
        self.assertIn("- Activity count: 1 primary", content)
        self.assertIn("Outdoor Walk", content)
        self.assertNotIn("Duplicate Walk", content)

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

        self.assertNotIn("Activity score", content)
        self.assertIn("| Movement | unavailable steps · 5.00 km walk · 45 min swim |", content)
        self.assertIn("Swimming included 45 min.", content)
        self.assertIn("- swim: Pool Swim (45 min)", content)
        self.assertNotIn("1.20 km", content)

    def test_withings_swim_distance_stays_out_of_trends_and_handoff(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [
                {
                    "source_id": "swim",
                    "start_time": "2026-05-29T12:00:00+00:00",
                    "duration_min": "45",
                    "distance_km": "1.20",
                    "activity_type": "swim",
                    "raw_type": "swim",
                    "name": "Pool Swim",
                },
            ],
        )

        self.assertIn("| Swimming duration |", content)
        self.assertNotIn("| Swimming distance |", content)
        self.assertIn("Swimming included 45 min.", content)
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

        self.assertNotIn("Activity score", content)
        self.assertIn("| Strength | Push Day · 93 min |", content)
        self.assertIn("Strength training included 1 workout and 93 min.", content)
        self.assertIn("### Workout", content)
        self.assertIn("- Push Day: 93 min", content)
        self.assertNotIn("unknown distance", content)

    def test_heavy_strength_does_not_generate_recovery_judgment(self) -> None:
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

        self.assert_no_custom_recovery(content)
        self.assertIn("| Strength | Full Body · 94 min · 69 sets · 34551 kg |", content)

    def test_mixed_walking_and_strength_does_not_generate_recovery_judgment(self) -> None:
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

        self.assert_no_custom_recovery(content)
        self.assertIn("| Movement | unavailable steps · 5.89 km walk |", content)
        self.assertIn("| Strength | Full Body · 94 min · 69 sets · 34551 kg |", content)

    def test_cycling_and_strength_do_not_generate_recovery_judgment(self) -> None:
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

        self.assert_no_custom_recovery(content)
        self.assertIn("| Movement | 20.00 km ride · steps unavailable |", content)
        self.assertIn("| Strength | Push Day · 45 min · 20 sets · 10000 kg |", content)
        self.assertNotIn("walking + strength", content)

    def test_swimming_and_walking_do_not_generate_recovery_judgment(self) -> None:
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

        self.assert_no_custom_recovery(content)
        self.assertIn(
            "| Movement | unavailable steps · 6.00 km walk · 60 min swim |",
            content,
        )

    def test_subjective_all_out_note_does_not_generate_recovery_judgment(self) -> None:
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

        self.assert_no_custom_recovery(content)
        self.assertIn("| Strength | Push Day · 60 min · 40 sets · 28000 kg |", content)

    def test_missing_workout_set_data_does_not_generate_recovery_judgment(self) -> None:
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

        self.assert_no_custom_recovery(content)
        self.assertNotIn("score uses duration", content)

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
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[context.activity]\nworkout = \"withings\"\n\n"
                    "[context.measurement]\ndefault = \"withings\"\nsteps = \"withings\"\nblood_pressure = \"withings\"\n\n"
                    "[context.recovery]\nsleep = \"withings\"\n"
                ),
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
                        "3,2026-05-29,2026-05-29T07:15:00,9,diastolic_blood_pressure,79,mmHg",
                        "3,2026-05-29,2026-05-29T07:15:00,10,systolic_blood_pressure,121,mmHg",
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
                        "withings,w2,2026-05-29T09:05:00Z,2026-05-29T09:31:00Z,26.00,2.10,3456,walk,walk",
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
            self.assertIn("  - Workout: Withings", content)
            self.assertIn("- Measurement: Withings", content)
            self.assertIn("- Activity count: 2 primary", content)
            self.assertIn("| Movement | 3,456 steps · 2.10 km walk |", content)
            self.assertIn(
                "| Running distance | 5.00 km | 5.00 km/week | 1.17 km/week | "
                "First recorded run |",
                content,
            )
            self.assertNotIn("withings:w0", content)
            self.assertIn("| Weight | 70.50 kg |", content)
            self.assertIn("| Blood pressure | 121/79 mmHg · 07:15 |", content)
            self.assertNotIn("71.00", content)

    def test_renders_withings_sleep_on_local_wake_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\ntimezone = "Asia/Tokyo"\n\n'
                    "[context.recovery]\nsleep = \"withings\"\n"
                ),
                encoding="utf-8",
            )
            withings_dir = data_dir / "withings"
            withings_dir.mkdir(parents=True)
            (withings_dir / "sleep.csv").write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,timezone,wake_date,total_sleep_min,time_in_bed_min,awake_min,awake_count,sleep_score,sleep_efficiency,light_sleep_min,deep_sleep_min,rem_sleep_min",
                        "withings,s1,2026-06-24T00:46:00+09:00,2026-06-24T07:28:00+09:00,Asia/Tokyo,2026-06-24,402.00,417.00,15.00,2,81,0.96,,,",
                        "withings,s2,2026-06-24T23:46:00+09:00,2026-06-25T06:28:00+09:00,Asia/Tokyo,2026-06-25,402.00,417.00,15.00,2,81,0.96,210.00,120.00,72.00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 6, 25))
            content = written.read_text(encoding="utf-8")

            self.assertIn("| Sleep | 6h42m · 23:46–06:28 |", content)
            self.assertNotIn("## Sleep", content)
            self.assertNotIn("| Duration | 6h42m |", content)
            self.assertNotIn("| Awake time | 0h15m |", content)
            self.assertNotIn("| Awake count | 2 |", content)
            self.assertNotIn("| Sleep score | 81 |", content)
            self.assertNotIn("| Sleep efficiency | 96% |", content)
            self.assertIn("  - Sleep: Withings", content)
            self.assertIn("Sleep: 6h42m, 23:46–06:28, source Withings.", content)
            self.assertNotIn("2026-06-24T00:46", content)
            self.assertNotIn("readiness", content.lower())
            self.assertNotIn("recovery score", content.lower())
            self.assertNotIn("sleep-adjusted TSS", content)

    def test_renders_vitalsync_sleep_on_local_wake_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f'[app]\ndata_dir = "{data_dir}"\ntimezone = "Asia/Tokyo"\n\n[plugin.vitalsync]\nenabled = true\n',
                encoding="utf-8",
            )
            vitalsync_dir = data_dir / "vitalsync"
            vitalsync_dir.mkdir(parents=True)
            (vitalsync_dir / "sleep.csv").write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,timezone,wake_date,total_sleep_min,time_in_bed_min,awake_min,awake_count,sleep_score,sleep_efficiency,light_sleep_min,deep_sleep_min,rem_sleep_min",
                        "vitalsync,s1,2026-06-24T23:00:00+09:00,2026-06-25T06:30:00+09:00,Asia/Tokyo,2026-06-25,390.00,450.00,30.00,1,,0.87,210.00,90.00,90.00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 6, 25))
            content = written.read_text(encoding="utf-8")

            self.assertIn("| Sleep | 6h30m · 23:00–06:30 |", content)  # noqa: RUF001
            self.assertIn("- Recovery: Vitalsync", content)
            self.assertIn("Sleep: 6h30m, 23:00–06:30, source Vitalsync.", content)  # noqa: RUF001

    def test_invalid_context_source_warns_and_skips_metric(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[context.measurement]\nsteps = \"viatalsync\"\n"
                ),
                encoding="utf-8",
            )
            withings_dir = data_dir / "withings"
            withings_dir.mkdir(parents=True)
            (withings_dir / "activity.csv").write_text(
                "date,step_count,distance_km\n2026-06-25,10000,7.00\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                written = generate_daily_context(config, date(2026, 6, 25))
            content = written.read_text(encoding="utf-8")

            self.assertIn("  - Steps: None", content)
            self.assertIn("source 'viatalsync' is not supported", stderr.getvalue())

    def test_uses_only_vitalsync_sleep_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f'[app]\ndata_dir = "{data_dir}"\ntimezone = "Asia/Tokyo"\n\n[plugin.vitalsync]\nenabled = true\n',
                encoding="utf-8",
            )
            header = (
                "source,source_id,start_time,end_time,timezone,wake_date,total_sleep_min,"
                "time_in_bed_min,awake_min,awake_count,sleep_score,sleep_efficiency,"
                "light_sleep_min,deep_sleep_min,rem_sleep_min"
            )
            withings_dir = data_dir / "withings"
            withings_dir.mkdir(parents=True)
            (withings_dir / "sleep.csv").write_text(
                "\n".join(
                    [
                        header,
                        "withings,w1,2026-06-24T22:00:00+09:00,2026-06-25T06:45:00+09:00,Asia/Tokyo,2026-06-25,480.00,525.00,45.00,2,81,0.91,240.00,120.00,120.00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            vitalsync_dir = data_dir / "vitalsync"
            vitalsync_dir.mkdir(parents=True)
            (vitalsync_dir / "sleep.csv").write_text(
                "\n".join(
                    [
                        header,
                        "vitalsync,v1,2026-06-24T23:00:00+09:00,2026-06-25T06:30:00+09:00,Asia/Tokyo,2026-06-25,390.00,450.00,30.00,1,,0.87,210.00,90.00,90.00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 6, 25))
            content = written.read_text(encoding="utf-8")

            self.assertIn("| Sleep | 6h30m · 23:00–06:30 |", content)  # noqa: RUF001
            self.assertIn("- Recovery: Vitalsync", content)
            self.assertNotIn("8h00m", content)

    def test_enabled_vitalsync_discards_withings_sleep_when_vitalsync_has_no_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f'[app]\ndata_dir = "{data_dir}"\ntimezone = "Asia/Tokyo"\n\n[plugin.vitalsync]\nenabled = true\n',
                encoding="utf-8",
            )
            withings_dir = data_dir / "withings"
            withings_dir.mkdir(parents=True)
            (withings_dir / "sleep.csv").write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,timezone,wake_date,total_sleep_min,time_in_bed_min,awake_min,awake_count,sleep_score,sleep_efficiency,light_sleep_min,deep_sleep_min,rem_sleep_min",
                        "withings,w1,2026-06-24T22:00:00+09:00,2026-06-25T06:45:00+09:00,Asia/Tokyo,2026-06-25,480.00,525.00,45.00,2,81,0.91,240.00,120.00,120.00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 6, 25))
            content = written.read_text(encoding="utf-8")

            self.assertNotIn("| Sleep |", content)
            self.assertIn("- Recovery: Vitalsync", content)

    def test_missing_sleep_does_not_crash_and_is_only_missing_after_history_starts(self) -> None:
        content_without_history = render_daily_context(date(2026, 6, 25), [])
        content_with_history = render_daily_context(
            date(2026, 6, 25),
            [],
            historical_sleep_records=[
                {
                    "source": "withings",
                    "source_id": "s1",
                    "wake_date": "2026-06-24",
                    "total_sleep_min": "420",
                }
            ],
        )

        self.assertIn("- Recovery: Vitalsync", content_without_history)
        self.assertNotIn("sleep unavailable", content_without_history)
        self.assertIn("- Recovery: Vitalsync", content_with_history)
        self.assertNotIn("sleep unavailable", content_with_history)

    def test_terminal_context_renders_sleep_snapshot_and_coverage(self) -> None:
        sleep = {
            "source": "withings",
            "source_id": "s1",
            "start_time": "2026-06-24T23:46:00+09:00",
            "end_time": "2026-06-25T06:28:00+09:00",
            "timezone": "Asia/Tokyo",
            "wake_date": "2026-06-25",
            "total_sleep_min": "402.00",
            "awake_min": "15.00",
            "awake_count": "2",
        }
        state = DailyState(
            target_date=date(2026, 6, 25),
            activities=[],
            measures=[],
            withings_activity_summaries=[],
            historical_withings_activity_summaries=[],
            historical_activities=[],
            historical_measures=[],
            hevy_sets=[],
            sleep_records=[sleep],
            historical_sleep_records=[sleep],
        )
        output = io.StringIO()
        console = Console(file=output, width=120, color_system=None, force_terminal=False)

        render_daily_terminal_context(state, console)

        content = output.getvalue()
        self.assertIn("Sleep     6h42m / 23:46–06:28", content)
        self.assertNotIn("Strength", content)
        self.assertIn("Recovery: Vitalsync", content)
        self.assertNotIn("Awake time   0h15m", content)

    def test_renders_zero_withings_steps_when_daily_activity_row_is_zero(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [],
            withings_activity_summaries=[{"date": "2026-05-29", "step_count": "0"}],
        )

        self.assertIn("| Movement | 0 steps |", content)

    def test_daily_steps_render_without_activity_score(self) -> None:
        content = render_daily_context(
            date(2026, 5, 29),
            [],
            withings_activity_summaries=[{"date": "2026-05-29", "step_count": "6500"}],
        )

        self.assertIn("| Movement | 6,500 steps |", content)
        self.assertNotIn("Activity score", content)

    def test_walk_and_daily_steps_render_as_observed_metrics(self) -> None:
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

        self.assertIn("| Movement | 6,500 steps · 5.00 km walk |", content)
        self.assertNotIn("Activity score", content)

    def test_run_keeps_daily_step_total_without_estimated_score(self) -> None:
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

        self.assertIn("| Movement | 6,000 steps |", content)
        self.assertNotIn("Activity score", content)

    def test_handles_missing_withings_workouts_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[context.activity]\nworkout = \"withings\"\n"
                ),
                encoding="utf-8",
            )
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
            self.assertIn("- Activity: Suunto", content)


if __name__ == "__main__":
    unittest.main()
