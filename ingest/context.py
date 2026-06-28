from __future__ import annotations

import csv
import math
import re
import shutil
import textwrap
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ingest.activities import (
    DEFAULT_TIMEZONE,
    NormalizedActivity,
    normalize_hevy_activity,
    normalize_suunto_activity,
    normalize_withings_activity,
)
from ingest.app_data import write_text_file
from ingest.config import AppConfig, UIConfig
from ingest.ui import DEFAULT_THEME, TerminalTheme, terminal_theme


@dataclass(frozen=True)
class DailyState:
    target_date: date
    activities: list[NormalizedActivity]
    measures: list[dict[str, str]]
    withings_activity_summaries: list[dict[str, str]]
    historical_withings_activity_summaries: list[dict[str, str]]
    historical_activities: list[NormalizedActivity]
    historical_measures: list[dict[str, str]]
    hevy_sets: list[dict[str, str]]
    sleep_records: list[dict[str, str]] = field(default_factory=list)
    historical_sleep_records: list[dict[str, str]] = field(default_factory=list)
    blood_pressure_records: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SuuntoDailyMetrics:
    total_tss: float | None
    total_energy_kcal: float | None
    avg_hr: float | None
    max_hr: float | None


@dataclass(frozen=True)
class TrainingLoadMetrics:
    today_tss: float
    ctl: float
    atl: float
    tsb: float
    tsb_label: str
    history_days: int
    history_label: str


@dataclass(frozen=True)
class ActivityTrendMetric:
    label: str
    today: float
    total_7d: float
    weekly_avg_30d: float
    unit: str
    direction: str


@dataclass(frozen=True)
class PerformanceTrendMetric:
    label: str
    today: float
    avg_7d: float
    avg_30d: float
    unit: str
    direction: str


@dataclass(frozen=True)
class EstimatedDeficitMetrics:
    today: float
    avg_7d: float | None
    avg_30d: float | None


def generate_daily_context(config: AppConfig, target_date: date | None = None) -> Path:
    target = target_date or datetime.now(config.timezone).date()
    state = build_daily_state(config, target)
    return write_text_file(config.daily_context_path, _render_daily_state(state))


def generate_today_context(config: AppConfig, target_date: date | None = None) -> Path:
    return generate_daily_context(config, target_date)


def render_daily_terminal_context(
    state: DailyState,
    console: Any | None = None,
    ui: UIConfig | None = None,
) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = console or Console()
    ui = ui or UIConfig(theme="default", body_weight_goal="maintenance")
    theme = terminal_theme(ui.theme)
    target_date = state.target_date
    activities = state.activities
    withings_steps = _withings_step_count(state.withings_activity_summaries)
    sleep = _primary_sleep(state.sleep_records)
    logged_duration_min = sum(activity.duration_min for activity in activities)
    withings_steps_text = _format_terminal_step_count(withings_steps)
    walking_distance_km = sum(
        activity.distance_km or 0.0
        for activity in activities
        if _is_walking_activity(activity)
    )
    swimming_duration_min = sum(
        activity.duration_min
        for activity in activities
        if activity.activity_type == "swim"
    )
    swimming_distance_km = sum(
        activity.distance_km or 0.0
        for activity in activities
        if activity.activity_type == "swim" and activity.source == "suunto"
    )
    strength_activities = [activity for activity in activities if activity.activity_type == "strength"]
    strength_duration_min = sum(activity.duration_min for activity in strength_activities)
    ride_distance_km = sum(
        activity.distance_km or 0.0
        for activity in activities
        if activity.activity_type == "ride"
    )
    weight_metrics = _weight_metrics(state.historical_measures, target_date)
    estimated_deficit_metrics = _estimated_deficit_metrics(state.historical_measures, target_date)
    activity_trends = _activity_trend_metrics(
        activities,
        state.historical_activities,
        target_date,
    )
    performance_trends = _performance_trend_metrics(
        activities,
        state.historical_activities,
        target_date,
    )
    training_load_trend = _training_load_trend_metric(
        state.historical_activities,
        target_date,
    )
    suunto_metrics = _suunto_daily_metrics(activities)
    training_load_metrics = _training_load_metrics(
        state.historical_activities,
        target_date,
    )

    console.print(
        Text(
            f"Physical Context — {target_date.isoformat()}",
            style=theme.style("title"),
        )
    )
    _render_section_title(console, "Daily Snapshot", theme)
    _render_kv_block(
        console,
        [
            (
                "Movement",
                _snapshot_movement_status(
                    withings_steps,
                    walking_distance_km,
                    ride_distance_km,
                    swimming_duration_min,
                    formatted_steps=withings_steps_text,
                    separator=" / ",
                ),
            ),
            *_terminal_suunto_summary_rows(suunto_metrics, training_load_metrics),
            (
                "Strength",
                _snapshot_strength_status(
                    strength_activities,
                    state.hevy_sets,
                    volume_formatter=_format_terminal_volume,
                    separator=" / ",
                ),
            ),
            *([("Sleep", _sleep_snapshot_status(sleep, separator=" / "))] if sleep else []),
            ("Body", _snapshot_body_status(weight_metrics, separator=" / ")),
        ],
        indent=2,
        theme=theme,
    )

    _render_section_title(console, "Trends", theme)
    workout_trends = [
        *activity_trends,
        *([training_load_trend] if training_load_trend is not None else []),
    ]
    if workout_trends:
        _render_subsection_title(console, "Workout", theme, role="trend_workout")
        workout_table = Table(
            box=None,
            show_edge=False,
            show_lines=False,
            expand=False,
            padding=(0, 2),
        )
        for column in ["  Metric", "Today", "7-day total", "30-day weekly avg", "Direction"]:
            workout_table.add_column(
                column,
                style=theme.style("metric_label") if column.strip() == "Metric" else "",
                no_wrap=True,
            )
        for metric in workout_trends:
            workout_table.add_row(
                _styled_terminal_value(f"  {metric.label}", label="Metric", theme=theme),
                _styled_terminal_value(
                    _format_activity_trend_value(metric.today, metric.unit),
                    theme=theme,
                ),
                _styled_terminal_value(
                    _format_activity_trend_total(metric.total_7d, metric.unit),
                    theme=theme,
                ),
                _styled_terminal_value(
                    _format_activity_trend_weekly_average(metric.weekly_avg_30d, metric.unit),
                    theme=theme,
                ),
                _styled_terminal_value(
                    metric.direction,
                    theme=theme,
                    semantic_role=_trend_direction_role(
                        metric.label,
                        metric.direction,
                        body_weight_goal=ui.body_weight_goal,
                    ),
                ),
            )
        console.print(workout_table)
    if performance_trends:
        _render_subsection_title(
            console,
            "Performance",
            theme,
            role="trend_performance",
        )
        performance_table = Table(
            box=None,
            show_edge=False,
            show_lines=False,
            expand=False,
            padding=(0, 2),
        )
        for column in ["  Metric", "Today", "7-day avg", "30-day avg", "Direction"]:
            performance_table.add_column(
                column,
                style=theme.style("metric_label") if column.strip() == "Metric" else "",
                no_wrap=True,
            )
        for metric in performance_trends:
            performance_table.add_row(
                _styled_terminal_value(
                    f"  {metric.label}",
                    label="Metric",
                    theme=theme,
                ),
                _styled_terminal_value(
                    _format_performance_trend_value(metric.today, metric.unit),
                    theme=theme,
                ),
                _styled_terminal_value(
                    _format_performance_trend_value(metric.avg_7d, metric.unit),
                    theme=theme,
                ),
                _styled_terminal_value(
                    _format_performance_trend_value(metric.avg_30d, metric.unit),
                    theme=theme,
                ),
                _styled_terminal_value(
                    metric.direction,
                    theme=theme,
                    semantic_role=_trend_direction_role(
                        metric.label,
                        metric.direction,
                        body_weight_goal=ui.body_weight_goal,
                    ),
                ),
            )
        console.print(performance_table)

    _render_subsection_title(console, "Body", theme, role="trend_body")
    body_trends = Table(box=None, show_edge=False, show_lines=False, expand=False, padding=(0, 2))
    for column in ["  Metric", "Today", "7-day avg", "30-day avg", "Direction"]:
        body_trends.add_column(
            column,
            style=theme.style("metric_label") if column.strip() == "Metric" else "",
            no_wrap=True,
        )
    body_trends.add_row(
        _styled_terminal_value("  Weight", label="Metric", theme=theme),
        _styled_terminal_value(weight_metrics["current_weight"], theme=theme),
        _styled_terminal_value(weight_metrics["avg_7d"], theme=theme),
        _styled_terminal_value(weight_metrics["avg_30d"], theme=theme),
        _styled_terminal_value(
            _terminal_trend_direction(
                _weight_value(weight_metrics["current_weight"]),
                _weight_value(weight_metrics["avg_7d"]),
                _weight_value(weight_metrics["avg_30d"]),
            ),
            theme=theme,
            semantic_role=_trend_direction_role(
                "Weight",
                _terminal_trend_direction(
                    _weight_value(weight_metrics["current_weight"]),
                    _weight_value(weight_metrics["avg_7d"]),
                    _weight_value(weight_metrics["avg_30d"]),
                ),
                body_weight_goal=ui.body_weight_goal,
                today=_weight_value(weight_metrics["current_weight"]),
                avg_7d=_weight_value(weight_metrics["avg_7d"]),
                avg_30d=_weight_value(weight_metrics["avg_30d"]),
            ),
        ),
    )
    if estimated_deficit_metrics is not None:
        body_trends.add_row(
            _styled_terminal_value("  Estimated deficit", label="Metric", theme=theme),
            _styled_terminal_value(
                _format_estimated_deficit(estimated_deficit_metrics.today),
                theme=theme,
            ),
            _styled_terminal_value(
                _format_average_estimated_deficit(estimated_deficit_metrics.avg_7d),
                theme=theme,
            ),
            _styled_terminal_value(
                _format_average_estimated_deficit(estimated_deficit_metrics.avg_30d),
                theme=theme,
            ),
            _styled_terminal_value(
                _terminal_trend_direction(
                    estimated_deficit_metrics.today,
                    estimated_deficit_metrics.avg_7d,
                    estimated_deficit_metrics.avg_30d,
                ),
                theme=theme,
                semantic_role=_trend_direction_role(
                    "Estimated deficit",
                    _terminal_trend_direction(
                        estimated_deficit_metrics.today,
                        estimated_deficit_metrics.avg_7d,
                        estimated_deficit_metrics.avg_30d,
                    ),
                    body_weight_goal=ui.body_weight_goal,
                    today=estimated_deficit_metrics.today,
                ),
            ),
        )
    console.print(body_trends)

    body_rows = _terminal_body_kv_rows(
        state.measures,
        state.historical_measures,
        target_date,
        state.blood_pressure_records,
    )
    if body_rows:
        _render_section_title(console, "Body", theme)
        _render_kv_block(console, body_rows, indent=2, theme=theme)

    if activities:
        _render_section_title(console, "Activities", theme)
        _render_terminal_activity_sections(console, activities, state.hevy_sets, theme)

    _render_section_title(console, "Data Coverage", theme)
    _render_kv_block(
        console,
        _terminal_data_coverage_rows(
            activities,
            withings_steps,
            state.measures,
            state.blood_pressure_records,
            training_load_metrics,
            sleep,
            _sleep_expected(state.historical_sleep_records, target_date),
        ),
        indent=2,
        theme=theme,
    )

    _render_section_title(console, "Machine Handoff", theme)
    _render_wrapped_paragraph(
        console,
        _ai_handoff(
            activities=activities,
            total_duration_min=logged_duration_min,
            withings_steps_text=withings_steps_text,
            walking_distance_km=walking_distance_km,
            swimming_duration_min=swimming_duration_min,
            swimming_distance_km=swimming_distance_km,
            strength_count=len(strength_activities),
            strength_duration_min=strength_duration_min,
            suunto_metrics=suunto_metrics,
            training_load_metrics=training_load_metrics,
            activity_trends=activity_trends,
            performance_trends=performance_trends,
            training_load_trend=training_load_trend,
            weight_metrics=weight_metrics,
            bmi=_bmi_value(
                _latest_measures_by_type(state.measures).get("weight"),
                _latest_historical_height(state.historical_measures, target_date),
            ),
            bmr=_bmr_value(_latest_measures_by_type(state.measures).get("fat_free_mass")),
            estimated_deficit=_format_average_estimated_deficit(estimated_deficit_metrics.today)
            if estimated_deficit_metrics is not None
            else None,
            sleep=sleep,
        ),
        theme=theme,
    )


def build_daily_state(config: AppConfig, target_date: date) -> DailyState:
    withings_activities = [] if config.suunto.enabled else read_withings_activities(config.withings.workouts_csv)
    hevy_activities = read_hevy_activities(config.hevy.workouts_csv)
    suunto_activities = read_suunto_activities(config.suunto.workouts_csv)
    hevy_sets = sets_for_date(
        read_hevy_sets(config.hevy.sets_csv),
        target_date,
        config.timezone,
    )
    all_measures = read_withings_measures(config.withings.measures_csv)
    measures = measures_for_date(all_measures, target_date)
    all_withings_activity_summaries = read_withings_activity_summaries(config.withings.activity_csv)
    withings_activity_summaries = withings_activity_summaries_for_date(
        all_withings_activity_summaries,
        target_date,
    )
    all_sleep_records = (
        read_vitalsync_sleep(config.vitalsync.sleep_csv)
        if config.vitalsync.enabled
        else read_withings_sleep(config.withings.sleep_csv)
    )
    sleep_records = sleep_records_for_date(all_sleep_records, target_date)
    all_blood_pressure_records = (
        read_vitalsync_blood_pressure(config.vitalsync.blood_pressure_csv)
        if config.vitalsync.enabled
        else []
    )
    blood_pressure_records = blood_pressure_records_for_date(
        all_blood_pressure_records,
        target_date,
    )
    normalized_activities = _prefer_suunto_activities(
        _pair_hevy_suunto_strength(
            [
                *_normalize_withings_activities(withings_activities, config.timezone),
                *_normalize_hevy_activities(hevy_activities, config.timezone),
                *_normalize_suunto_activities(suunto_activities, config.timezone),
            ]
        )
    )
    return DailyState(
        target_date=target_date,
        activities=[
            activity
            for activity in normalized_activities
            if _activity_date(activity.start_time) == target_date
        ],
        measures=measures,
        withings_activity_summaries=withings_activity_summaries,
        historical_withings_activity_summaries=all_withings_activity_summaries,
        historical_activities=normalized_activities,
        historical_measures=all_measures,
        hevy_sets=hevy_sets,
        sleep_records=sleep_records,
        historical_sleep_records=all_sleep_records,
        blood_pressure_records=blood_pressure_records,
    )


def read_withings_measures(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_withings_activity_summaries(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_withings_sleep(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_vitalsync_sleep(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_vitalsync_blood_pressure(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_withings_activities(path: Path) -> list[dict[str, str]]:
    return [
        activity
        for activity in _read_activity_rows(path)
        if activity.get("raw_type") != "category_16"
        and activity.get("activity_type") != "category_16"
    ]


def read_hevy_activities(path: Path) -> list[dict[str, str]]:
    return _read_activity_rows(path)


def read_suunto_activities(path: Path) -> list[dict[str, str]]:
    return _read_activity_rows(path)


def read_hevy_sets(path: Path) -> list[dict[str, str]]:
    return _read_activity_rows(path)


def _read_activity_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def withings_activities_for_date(
    activities: list[dict[str, str]],
    target_date: date,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[dict[str, str]]:
    return activities_for_date(activities, target_date, local_timezone)


def activities_for_date(
    activities: list[dict[str, str]],
    target_date: date,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[dict[str, str]]:
    return [
        activity
        for activity in activities
        if _timestamp_date(activity.get("start_time", ""), local_timezone) == target_date
    ]


def sets_for_date(
    sets: list[dict[str, str]],
    target_date: date,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[dict[str, str]]:
    return [
        set_row
        for set_row in sets
        if _timestamp_date(set_row.get("start_time", ""), local_timezone) == target_date
    ]


def measures_for_date(measures: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [measure for measure in measures if measure.get("date") == target]


def withings_activity_summaries_for_date(
    activity_summaries: list[dict[str, str]],
    target_date: date,
) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [activity for activity in activity_summaries if activity.get("date") == target]


def sleep_records_for_date(
    sleep_records: list[dict[str, str]],
    target_date: date,
) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [sleep for sleep in sleep_records if sleep.get("wake_date") == target]


def blood_pressure_records_for_date(
    blood_pressure_records: list[dict[str, str]],
    target_date: date,
) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [record for record in blood_pressure_records if record.get("date") == target]


def render_daily_context(
    target_date: date,
    activities: list[dict[str, str]],
    measures: list[dict[str, str]] | None = None,
    historical_measures: list[dict[str, str]] | None = None,
    historical_activities: list[dict[str, str]] | None = None,
    hevy_sets: list[dict[str, str]] | None = None,
    withings_activity_summaries: list[dict[str, str]] | None = None,
    historical_withings_activity_summaries: list[dict[str, str]] | None = None,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
    sleep_records: list[dict[str, str]] | None = None,
    historical_sleep_records: list[dict[str, str]] | None = None,
    blood_pressure_records: list[dict[str, str]] | None = None,
) -> str:
    measures = measures or []
    historical_measures = historical_measures if historical_measures is not None else measures
    historical_activities = historical_activities if historical_activities is not None else activities
    withings_activity_summaries = withings_activity_summaries or []
    historical_withings_activity_summaries = (
        historical_withings_activity_summaries
        if historical_withings_activity_summaries is not None
        else withings_activity_summaries
    )
    sleep_records = sleep_records or []
    blood_pressure_records = blood_pressure_records or []
    historical_sleep_records = (
        historical_sleep_records
        if historical_sleep_records is not None
        else sleep_records
    )
    state = DailyState(
        target_date=target_date,
        activities=_prefer_suunto_activities(
            _pair_hevy_suunto_strength(
                _normalize_activities(activities, local_timezone)
            )
        ),
        measures=measures,
        withings_activity_summaries=withings_activity_summaries,
        historical_withings_activity_summaries=historical_withings_activity_summaries,
        historical_activities=_prefer_suunto_activities(
            _pair_hevy_suunto_strength(
                _normalize_activities(historical_activities, local_timezone)
            )
        ),
        historical_measures=historical_measures,
        hevy_sets=hevy_sets or [],
        sleep_records=sleep_records,
        historical_sleep_records=historical_sleep_records,
        blood_pressure_records=blood_pressure_records,
    )
    return _render_daily_state(state)


def render_today_context(
    target_date: date,
    activities: list[dict[str, str]],
    measures: list[dict[str, str]] | None = None,
    historical_measures: list[dict[str, str]] | None = None,
    historical_activities: list[dict[str, str]] | None = None,
) -> str:
    return render_daily_context(target_date, activities, measures, historical_measures, historical_activities)


def _render_daily_state(state: DailyState) -> str:
    target_date = state.target_date
    primary_today_activities = state.activities
    historical_normalized_activities = state.historical_activities
    measures = state.measures
    withings_steps = _withings_step_count(state.withings_activity_summaries)
    sleep = _primary_sleep(state.sleep_records)
    logged_duration_min = sum(activity.duration_min for activity in primary_today_activities)
    withings_steps_text = _format_step_count(withings_steps)
    walking_distance_km = sum(
        activity.distance_km or 0.0
        for activity in primary_today_activities
        if _is_walking_activity(activity)
    )
    swimming_duration_min = sum(
        activity.duration_min
        for activity in primary_today_activities
        if activity.activity_type == "swim"
    )
    swimming_distance_km = sum(
        activity.distance_km or 0.0
        for activity in primary_today_activities
        if activity.activity_type == "swim" and activity.source == "suunto"
    )
    strength_activities = [
        activity
        for activity in primary_today_activities
        if activity.activity_type == "strength"
    ]
    strength_duration_min = sum(activity.duration_min for activity in strength_activities)
    weight_metrics = _weight_metrics(state.historical_measures, target_date)
    estimated_deficit_metrics = _estimated_deficit_metrics(state.historical_measures, target_date)
    activity_trends = _activity_trend_metrics(
        primary_today_activities,
        historical_normalized_activities,
        target_date,
    )
    performance_trends = _performance_trend_metrics(
        primary_today_activities,
        historical_normalized_activities,
        target_date,
    )
    training_load_trend = _training_load_trend_metric(
        historical_normalized_activities,
        target_date,
    )
    ride_distance_km = sum(
        activity.distance_km or 0.0
        for activity in primary_today_activities
        if activity.activity_type == "ride"
    )
    body_rows = _body_rows(
        measures,
        state.historical_measures,
        target_date,
        state.blood_pressure_records,
    )
    suunto_metrics = _suunto_daily_metrics(primary_today_activities)
    training_load_metrics = _training_load_metrics(
        historical_normalized_activities,
        target_date,
    )
    workout_trends = [
        *activity_trends,
        *([training_load_trend] if training_load_trend is not None else []),
    ]

    lines = [
        f"# Physical Context - {target_date.isoformat()}",
        "",
        "## Daily Snapshot",
        "",
        "| Area | Status |",
        "| --- | --- |",
        f"| Movement | {_snapshot_movement_status(withings_steps, walking_distance_km, ride_distance_km, swimming_duration_min)} |",
        *[
            f"| {label} | {value} |"
            for label, value in _suunto_summary_rows(
                suunto_metrics,
                training_load_metrics,
            )
        ],
        f"| Strength | {_snapshot_strength_status(strength_activities, state.hevy_sets)} |",
        *([f"| Sleep | {_sleep_snapshot_status(sleep)} |"] if sleep else []),
        f"| Body | {_snapshot_body_status(weight_metrics)} |",
        "",
        "## Trends",
        "",
        *(
            [
                "### Workout",
                "",
                "| Metric | Today | 7-day total | 30-day weekly avg | Direction |",
                "| --- | --- | --- | --- | --- |",
                *[_render_activity_trend_row(metric) for metric in workout_trends],
                "",
            ]
            if workout_trends
            else []
        ),
        *(
            [
                "### Performance",
                "",
                "| Metric | Today | 7-day avg | 30-day avg | Direction |",
                "| --- | --- | --- | --- | --- |",
                *[
                    _render_performance_trend_row(metric)
                    for metric in performance_trends
                ],
                "",
            ]
            if performance_trends
            else []
        ),
        "### Body",
        "",
        "| Metric | Today | 7-day avg | 30-day avg | Direction |",
        "| --- | --- | --- | --- | --- |",
        (
            "| Weight | "
            f"{weight_metrics['current_weight']} | "
            f"{weight_metrics['avg_7d']} | "
            f"{weight_metrics['avg_30d']} | "
            f"{_trend_direction(weight_metrics['trend'], _weight_value(weight_metrics['current_weight']), _weight_value(weight_metrics['avg_30d']))} |"
        ),
        *(
            [
                (
                    "| Estimated deficit | "
                    f"{_format_estimated_deficit(estimated_deficit_metrics.today)} | "
                    f"{_format_average_estimated_deficit(estimated_deficit_metrics.avg_7d)} | "
                    f"{_format_average_estimated_deficit(estimated_deficit_metrics.avg_30d)} | "
                    f"{_trend_direction('Unknown', estimated_deficit_metrics.today, estimated_deficit_metrics.avg_30d)} |"
                )
            ]
            if estimated_deficit_metrics is not None
            else []
        ),
        "",
    ]

    if body_rows:
        lines.extend(["## Body", "", "| Metric | Value |", "| --- | --- |", *body_rows, ""])

    if primary_today_activities:
        lines.extend(_render_activity_sections(primary_today_activities, state.hevy_sets))

    lines.extend(
        [
            "## Data Coverage",
            "",
            f"- Workout source: {_activity_sources(primary_today_activities)}",
            f"- Step source: {'Withings' if withings_steps is not None else 'None'}",
            f"- Body source: {_body_source_label(measures, state.blood_pressure_records)}",
            f"- Sleep source: {_sleep_source_label(sleep)}",
            f"- Activity count: {len(primary_today_activities)} primary",
            *(
                [
                    "- Training load history: "
                    f"{_training_load_history_status(training_load_metrics.history_label)}"
                ]
                if training_load_metrics is not None
                else []
            ),
            f"- Missing or partial data: {_missing_data_summary(withings_steps, measures, primary_today_activities, sleep, _sleep_expected(state.historical_sleep_records, target_date))}",
            "",
            "## Machine Handoff",
            "",
            _ai_handoff(
                activities=primary_today_activities,
                total_duration_min=logged_duration_min,
                withings_steps_text=withings_steps_text,
                walking_distance_km=walking_distance_km,
                swimming_duration_min=swimming_duration_min,
                swimming_distance_km=swimming_distance_km,
                strength_count=len(strength_activities),
                strength_duration_min=strength_duration_min,
                suunto_metrics=suunto_metrics,
                training_load_metrics=training_load_metrics,
                activity_trends=activity_trends,
                performance_trends=performance_trends,
                training_load_trend=training_load_trend,
                weight_metrics=weight_metrics,
                bmi=_bmi_value(
                    _latest_measures_by_type(measures).get("weight"),
                    _latest_historical_height(state.historical_measures, target_date),
                ),
                bmr=_bmr_value(_latest_measures_by_type(measures).get("fat_free_mass")),
                estimated_deficit=_format_average_estimated_deficit(estimated_deficit_metrics.today)
                if estimated_deficit_metrics is not None
                else None,
                sleep=sleep,
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _snapshot_movement_status(
    withings_steps: int | None,
    walking_distance_km: float,
    ride_distance_km: float,
    swimming_duration_min: float,
    *,
    formatted_steps: str | None = None,
    separator: str = " · ",
) -> str:
    step_part = f"{formatted_steps or _format_step_count(withings_steps)} steps"
    movement_parts: list[str] = []
    if walking_distance_km > 0:
        movement_parts.append(f"{walking_distance_km:.2f} km walk")
    if ride_distance_km > 0:
        movement_parts.append(f"{ride_distance_km:.2f} km ride")
    if swimming_duration_min > 0:
        movement_parts.append(f"{swimming_duration_min:.0f} min swim")
    if withings_steps is None and walking_distance_km <= 0 and movement_parts:
        parts = [*movement_parts, "steps unavailable"]
    else:
        parts = [step_part, *movement_parts]
    return separator.join(parts)


def _snapshot_strength_status(
    strength_activities: list[NormalizedActivity],
    hevy_sets: list[dict[str, str]],
    *,
    volume_formatter: Any | None = None,
    separator: str = " · ",
) -> str:
    if not strength_activities:
        return "None"

    total_duration_min = sum(activity.duration_min for activity in strength_activities)
    strength_sets = _sets_for_strength_activities(strength_activities, hevy_sets)
    total_volume_kg = sum(_float_value(set_row.get("volume_kg", "")) for set_row in strength_sets)
    workout_names = ", ".join(_display_activity_name(activity) for activity in strength_activities)
    parts = [workout_names, f"{total_duration_min:.0f} min"]
    if strength_sets:
        volume_formatter = volume_formatter or _format_volume
        parts.append(f"{len(strength_sets)} sets")
        parts.append(volume_formatter(total_volume_kg))
    return separator.join(parts)


def _snapshot_body_status(weight_metrics: dict[str, str], *, separator: str = " · ") -> str:
    return f"{weight_metrics['current_weight']}{separator}{weight_metrics['trend'].lower()}"


def _suunto_daily_metrics(activities: list[NormalizedActivity]) -> SuuntoDailyMetrics:
    suunto_activities = [activity for activity in activities if activity.source == "suunto"]
    tss_scores = [
        activity.tss_score for activity in suunto_activities if activity.tss_score is not None
    ]
    energy_values = [
        activity.energy_kcal for activity in suunto_activities if activity.energy_kcal is not None
    ]
    weighted_hr_activities = [
        activity
        for activity in suunto_activities
        if activity.avg_hr is not None and activity.duration_min > 0
    ]
    max_hr_values = [
        activity.max_hr for activity in suunto_activities if activity.max_hr is not None
    ]
    weighted_duration = sum(activity.duration_min for activity in weighted_hr_activities)
    return SuuntoDailyMetrics(
        total_tss=sum(tss_scores) if tss_scores else None,
        total_energy_kcal=sum(energy_values) if energy_values else None,
        avg_hr=(
            sum(activity.avg_hr * activity.duration_min for activity in weighted_hr_activities)
            / weighted_duration
            if weighted_duration > 0
            else None
        ),
        max_hr=max(max_hr_values) if max_hr_values else None,
    )


def _training_load_metrics(
    activities: list[NormalizedActivity],
    target_date: date,
) -> TrainingLoadMetrics | None:
    daily_tss: dict[date, float] = {}
    for activity in activities:
        if activity.source != "suunto" or activity.tss_score is None:
            continue
        activity_date = _activity_date(activity.start_time)
        if activity_date is None or activity_date > target_date:
            continue
        daily_tss[activity_date] = daily_tss.get(activity_date, 0.0) + activity.tss_score

    if not daily_tss:
        return None

    ctl = 0.0
    atl = 0.0
    ctl_alpha = 1 - math.exp(-1 / 42)
    atl_alpha = 1 - math.exp(-1 / 7)
    current_date = min(daily_tss)
    state_date = target_date
    while current_date <= state_date:
        load = daily_tss.get(current_date, 0.0)
        ctl += ctl_alpha * (load - ctl)
        atl += atl_alpha * (load - atl)
        current_date += timedelta(days=1)

    tsb = ctl - atl
    history_days = (target_date - min(daily_tss)).days + 1
    return TrainingLoadMetrics(
        today_tss=daily_tss.get(target_date, 0.0),
        ctl=ctl,
        atl=atl,
        tsb=tsb,
        tsb_label=_tsb_label(tsb),
        history_days=history_days,
        history_label=_training_load_history_label(history_days),
    )


def _tsb_label(tsb: float) -> str:
    if tsb < -30:
        return "Too high intensity"
    if tsb < -10:
        return "Fatigue / Improving fitness"
    if tsb < 15:
        return "Training balance"
    return "Losing fitness or recovering"


def _training_load_history_label(history_days: int) -> str:
    if history_days < 7:
        return "Training load history limited; ATL/TSB warming up"
    if history_days < 42:
        return "Training load history limited; CTL warming up"
    return "Training load baseline available"


def _training_load_history_status(label: str) -> str:
    prefix = "Training load history "
    status = label.removeprefix(prefix)
    return status[:1].upper() + status[1:]


def _suunto_summary_rows(
    metrics: SuuntoDailyMetrics,
    training_load: TrainingLoadMetrics | None,
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    load_parts: list[str] = []
    if training_load is not None:
        load_parts.append(f"TSS {training_load.today_tss:.1f}")
        load_parts.extend(
            [
                f"CTL {training_load.ctl:.1f}",
                f"ATL {training_load.atl:.1f}",
                f"TSB {training_load.tsb:.1f}",
                _training_load_snapshot_label(training_load),
            ]
        )
    elif metrics.total_tss is not None:
        load_parts.append(f"TSS {metrics.total_tss:.1f}")
    if load_parts:
        rows.append(("Load", " · ".join(load_parts)))

    hr_parts: list[str] = []
    if metrics.avg_hr is not None:
        hr_parts.append(f"avg {metrics.avg_hr:.0f}")
    if metrics.max_hr is not None:
        hr_parts.append(f"max {metrics.max_hr:.0f}")
    if hr_parts:
        rows.append(("HR", " · ".join(hr_parts)))

    if metrics.total_energy_kcal is not None:
        rows.append(("Energy", f"{metrics.total_energy_kcal:.0f} kcal"))
    return rows


def _training_load_snapshot_label(metrics: TrainingLoadMetrics) -> str:
    if metrics.history_days < 7:
        return "warming up"
    return metrics.tsb_label


def _terminal_suunto_summary_rows(
    metrics: SuuntoDailyMetrics,
    training_load: TrainingLoadMetrics | None,
) -> list[tuple[str, str]]:
    return [
        (label, value.replace(" · ", " / "))
        for label, value in _suunto_summary_rows(metrics, training_load)
    ]


def _activity_metric_parts(activity: NormalizedActivity) -> list[str]:
    parts: list[str] = []
    if activity.step_count > 0:
        parts.append(f"{activity.step_count:,} steps")
    if activity.energy_kcal is not None:
        parts.append(f"{activity.energy_kcal:.0f} kcal")
    if activity.avg_hr is not None and activity.max_hr is not None:
        parts.append(f"HR {activity.avg_hr:.0f}-{activity.max_hr:.0f}")
    elif activity.avg_hr is not None:
        parts.append(f"HR avg {activity.avg_hr:.0f}")
    elif activity.max_hr is not None:
        parts.append(f"HR max {activity.max_hr:.0f}")
    if activity.tss_score is not None:
        method = f"({activity.tss_method.lower()})" if activity.tss_method else ""
        parts.append(f"TSS{method} {activity.tss_score:.1f}")
    return parts


SECTION_THEME_ROLES = {
    "Daily Snapshot": "section_daily_snapshot",
    "Trends": "section_trends",
    "Body": "section_body",
    "Sleep": "section_body",
    "Activities": "section_activities",
    "Data Coverage": "section_data_coverage",
    "Machine Handoff": "section_machine_handoff",
}


def _render_section_title(
    console: Any,
    title: str,
    theme: TerminalTheme = DEFAULT_THEME,
) -> None:
    console.print()
    console.print(title, style=theme.style(SECTION_THEME_ROLES[title]))


def _render_subsection_title(
    console: Any,
    title: str,
    theme: TerminalTheme = DEFAULT_THEME,
    *,
    role: str = "subsection",
) -> None:
    console.print()
    console.print(f"  {title}", style=theme.style(role))


def _render_kv_block(
    console: Any,
    rows: list[tuple[str, str]],
    *,
    indent: int = 0,
    theme: TerminalTheme = DEFAULT_THEME,
) -> None:
    if not rows:
        return
    label_width = max(len(label) for label, _ in rows)
    prefix = " " * indent
    for label, value in rows:
        value_width = max(console.width - indent - label_width - 2, 20)
        wrapped_value = textwrap.fill(str(value), width=value_width).splitlines() or [""]
        line = _styled_terminal_line(
            f"{prefix}{label:<{label_width}}  ",
            wrapped_value[0],
            label=label,
            theme=theme,
        )
        console.print(line)
        continuation_prefix = f"{prefix}{'':<{label_width}}  "
        for line in wrapped_value[1:]:
            console.print(
                _styled_terminal_line(
                    continuation_prefix,
                    line,
                    label=label,
                    theme=theme,
                )
            )


def _styled_terminal_line(
    prefix: str,
    value: str,
    *,
    label: str = "",
    theme: TerminalTheme = DEFAULT_THEME,
) -> Any:
    from rich.text import Text

    text = Text(prefix, style=theme.style("label"))
    text.append(_styled_terminal_value(value, label=label, theme=theme))
    return text


def _styled_terminal_value(
    value: str,
    *,
    label: str = "",
    theme: TerminalTheme = DEFAULT_THEME,
    semantic_role: str | None = None,
) -> Any:
    from rich.text import Text

    text = Text(str(value))
    if label == "Metric":
        text.stylize(theme.style("metric_label"))
        return text

    _stylize_matches(
        text,
        r"\b\d[\d,]*(?:\.\d+)?(?: min/100m| min/km| km/h| steps| TSS| kcal|/day| kg| km| min|%)?\b",
        theme.style("primary_value"),
    )
    _stylize_matches(text, r"\([+-]?\d+%\)", theme.style("primary_value"))
    _stylize_matches(text, r"\b(Good|Low)\b", theme.style("positive"))
    _stylize_matches(text, r"\bPoor\b", theme.style("negative"))
    _stylize_matches(
        text,
        r"(?i)\btraining load history limited\b|\bwarming\s+up\b|\blimited\b",
        theme.style("limited_history"),
    )
    _stylize_matches(
        text,
        r"(?i)\bbaseline(?:\s+is\s+still)?\s+forming\b",
        theme.style("baseline_forming"),
    )
    _stylize_matches(
        text,
        r"(?i)(?:\bNone\b|\bunavailable\b|\bunknown\b|\bNo baseline\b)",
        theme.style("missing"),
    )
    if semantic_role is not None:
        text.stylize(theme.style(semantic_role))
    return text


def _stylize_matches(text: Any, pattern: str, style: str) -> None:
    for match in re.finditer(pattern, text.plain):
        text.stylize(style, match.start(), match.end())


def _render_wrapped_paragraph(
    console: Any,
    text: str,
    *,
    indent: int = 2,
    max_width: int = 88,
    theme: TerminalTheme = DEFAULT_THEME,
) -> None:
    detected_width = console.size.width or shutil.get_terminal_size((100, 24)).columns
    terminal_width = min(detected_width, max_width)
    available_width = max(40, terminal_width - indent)
    prefix = " " * indent
    wrapped = textwrap.fill(
        text,
        width=available_width,
        initial_indent=prefix,
        subsequent_indent=prefix,
        break_long_words=False,
        break_on_hyphens=False,
    )
    styled = _styled_terminal_value(wrapped, theme=theme)
    _stylize_matches(styled, r"(?i)\bfaster than\b[^.]*", theme.style("positive"))
    _stylize_matches(styled, r"(?i)\bslower than\b[^.]*", theme.style("warning"))
    console.print(styled, overflow="fold", crop=False, soft_wrap=False)


def _render_terminal_activity_sections(
    console: Any,
    activities: list[NormalizedActivity],
    hevy_sets: list[dict[str, str]],
    theme: TerminalTheme = DEFAULT_THEME,
) -> None:
    walking_activities = [activity for activity in activities if _is_walking_activity(activity)]
    swimming_activities = [activity for activity in activities if activity.activity_type == "swim"]
    workout_activities = [activity for activity in activities if activity.activity_type == "strength"]
    other_activities = [
        activity
        for activity in activities
        if activity not in [*walking_activities, *swimming_activities, *workout_activities]
    ]

    if walking_activities:
        _render_subsection_title(console, "Walking", theme)
        _render_lines(
            console,
            [_terminal_distance_activity(activity, theme) for activity in walking_activities],
            indent=4,
            theme=theme,
        )

    if swimming_activities:
        _render_subsection_title(console, "Swimming", theme)
        _render_lines(
            console,
            [_terminal_duration_activity(activity, theme) for activity in swimming_activities],
            indent=4,
            theme=theme,
        )

    if workout_activities:
        _render_subsection_title(console, "Workout", theme)
        for activity in workout_activities:
            console.print(_terminal_workout_header(activity, theme))
            hevy_workout_id = _hevy_workout_id(activity)
            workout_sets = [
                set_row
                for set_row in hevy_sets
                if set_row.get("workout_source_id") == hevy_workout_id
            ]
            if activity.detail_source == "hevy":
                total_volume_kg = sum(
                    _float_value(set_row.get("volume_kg", ""))
                    for set_row in workout_sets
                )
                hevy_detail = _display_activity_name(activity)
                if workout_sets:
                    hevy_detail += (
                        f" / {len(workout_sets)} sets / "
                        f"{_format_terminal_volume(total_volume_kg)} volume"
                    )
                _render_lines(
                    console,
                    [f"Hevy {hevy_detail}"],
                    indent=4,
                    theme=theme,
                )
            if workout_sets:
                total_volume_kg = sum(_float_value(set_row.get("volume_kg", "")) for set_row in workout_sets)
                if activity.detail_source != "hevy":
                    _render_kv_block(
                        console,
                        [("Sets", str(len(workout_sets))), ("Volume", _format_terminal_volume(total_volume_kg))],
                        indent=4,
                        theme=theme,
                    )
                _render_lines(
                    console,
                    _terminal_exercise_summaries(workout_sets),
                    indent=4,
                    theme=theme,
                )

    if other_activities:
        _render_subsection_title(console, "Other", theme)
        _render_lines(
            console,
            [_terminal_distance_activity(activity, theme) for activity in other_activities],
            indent=4,
            theme=theme,
        )


def _render_lines(
    console: Any,
    items: list[Any],
    *,
    indent: int = 0,
    theme: TerminalTheme = DEFAULT_THEME,
) -> None:
    prefix = " " * indent
    for item in items:
        if hasattr(item, "plain"):
            from rich.text import Text

            line = Text(prefix)
            line.append(item)
            console.print(line)
        else:
            console.print(_styled_terminal_line(prefix, str(item), theme=theme))


def _terminal_distance_activity(
    activity: NormalizedActivity,
    theme: TerminalTheme = DEFAULT_THEME,
) -> Any:
    from rich.text import Text

    text = Text(activity.raw_type or "Unknown", style=theme.style("subsection"))
    text.append("  ")
    text.append(_terminal_activity_source(activity), style=theme.style("muted"))
    if activity.distance_km is not None:
        text.append(" / ")
        text.append(_format_distance(activity.distance_km), style=theme.style("primary_value"))
    text.append(" / ")
    text.append(
        f"{activity.duration_min:.0f} min",
        style=theme.style("primary_value"),
    )
    _append_terminal_activity_metrics(text, activity, theme)
    return text


def _terminal_duration_activity(
    activity: NormalizedActivity,
    theme: TerminalTheme = DEFAULT_THEME,
) -> Any:
    from rich.text import Text

    text = Text(activity.raw_type or "Unknown", style=theme.style("subsection"))
    text.append("  ")
    text.append(_terminal_activity_source(activity), style=theme.style("muted"))
    text.append(" / ")
    text.append(
        f"{activity.duration_min:.0f} min",
        style=theme.style("primary_value"),
    )
    _append_terminal_activity_metrics(text, activity, theme)
    return text


def _terminal_workout_header(
    activity: NormalizedActivity,
    theme: TerminalTheme = DEFAULT_THEME,
) -> Any:
    from rich.text import Text

    text = Text("    ")
    if activity.detail_source == "hevy":
        text.append(activity.raw_type or "STRENGTH", style=theme.style("subsection"))
        text.append("  ")
        text.append(_terminal_activity_source(activity), style=theme.style("muted"))
    else:
        text.append(_display_activity_name(activity), style=theme.style("subsection"))
    text.append(" / ")
    text.append(
        f"{activity.duration_min:.0f} min",
        style=theme.style("primary_value"),
    )
    _append_terminal_activity_metrics(text, activity, theme)
    return text


def _append_terminal_activity_metrics(
    text: Any,
    activity: NormalizedActivity,
    theme: TerminalTheme = DEFAULT_THEME,
) -> None:
    for metric in _activity_metric_parts(activity):
        text.append(" / ")
        text.append(metric, style=theme.style("primary_value"))


def _terminal_activity_source(activity: NormalizedActivity) -> str:
    if activity.source_id:
        return f"{activity.source}:{activity.source_id}"
    return _display_activity_name(activity)


def _terminal_exercise_summaries(sets: list[dict[str, str]]) -> list[str]:
    by_exercise: dict[str, list[dict[str, str]]] = {}
    for set_row in sets:
        by_exercise.setdefault(set_row.get("exercise") or "Unknown exercise", []).append(set_row)

    summaries: list[str] = []
    for exercise, exercise_sets in by_exercise.items():
        set_count = len(exercise_sets)
        volume_kg = sum(_float_value(set_row.get("volume_kg", "")) for set_row in exercise_sets)
        set_details = ", ".join(_format_set_detail(set_row) for set_row in exercise_sets)
        summaries.append(f"{exercise}: {set_count} sets, {_format_terminal_volume(volume_kg)} ({set_details})")
    return summaries


def _format_terminal_step_count(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:,}"


def _format_terminal_volume(value: float) -> str:
    return f"{value:,.0f} kg" if value else "0 kg"


def _trend_direction(trend: str, today: float | None, avg_30d: float | None) -> str:
    if today is not None and avg_30d is not None:
        difference = today - avg_30d
        if abs(difference) < 0.1:
            return "Stable"
        if difference > 0:
            return "Above 30-day average"
        return "Below 30-day average"
    if trend == "Increasing":
        return "Slightly up"
    if trend == "Decreasing":
        return "Slightly down"
    return trend


def _terminal_trend_direction(today: float | None, avg_7d: float | None, avg_30d: float | None) -> str:
    baseline = avg_30d if avg_30d not in {None, 0} else avg_7d
    if today is None or baseline in {None, 0}:
        return "No baseline"

    label_suffix = "30d avg" if avg_30d not in {None, 0} else "7d avg"
    percent_diff = ((today - baseline) / baseline) * 100
    if abs(percent_diff) < 1:
        return f"Near {label_suffix}"
    if percent_diff > 0:
        return f"Above {label_suffix} ({_format_percent_diff(percent_diff)})"
    return f"Below {label_suffix} ({_format_percent_diff(percent_diff)})"


def _format_percent_diff(value: float) -> str:
    rounded = round(value)
    if rounded > 0:
        return f"+{rounded}%"
    return f"{rounded}%"


def _weight_direction_role(
    today: float | None,
    avg_7d: float | None,
    avg_30d: float | None,
    goal: str,
) -> str | None:
    baseline = avg_30d if avg_30d not in {None, 0} else avg_7d
    if goal == "maintenance" or today is None or baseline in {None, 0}:
        return None
    if math.isclose(today, baseline):
        return None
    decreasing = today < baseline
    goal_aligned = decreasing if goal == "loss" else not decreasing
    return "positive" if goal_aligned else "warning"


def _trend_direction_role(
    metric: str,
    direction: str,
    *,
    body_weight_goal: str,
    today: float | None = None,
    avg_7d: float | None = None,
    avg_30d: float | None = None,
) -> str | None:
    normalized = direction.lower()
    if "baseline forming" in normalized:
        return "baseline_forming"
    if "limited" in normalized or "warming up" in normalized:
        return "limited_history"
    if any(word in normalized for word in ("no baseline", "unavailable", "unknown")):
        return "missing"
    if metric.endswith((" pace", " speed")):
        if normalized.startswith("faster than"):
            return "positive"
        if normalized.startswith("slower than"):
            return "warning"
        return None
    if metric == "Weight":
        return _weight_direction_role(
            today,
            avg_7d,
            avg_30d,
            body_weight_goal,
        )
    if metric == "Estimated deficit":
        if normalized.startswith("above"):
            return "positive"
        if normalized.startswith("below"):
            return "warning"
        if normalized.startswith("near") and today is not None:
            return "positive" if today > 0 else "warning"
    return None


def _weight_value(value: str) -> float | None:
    if value in {"Unknown", "No Withings weight available"}:
        return None
    return _float_value(value.split(" ", 1)[0])


def _body_rows(
    measures: list[dict[str, str]],
    historical_measures: list[dict[str, str]],
    target_date: date,
    blood_pressure_records: list[dict[str, str]] | None = None,
) -> list[str]:
    return [
        f"| {label} | {value} |"
        for label, value in _body_kv_rows(
            measures,
            historical_measures,
            target_date,
            blood_pressure_records or [],
        )
    ]


def _terminal_body_kv_rows(
    measures: list[dict[str, str]],
    historical_measures: list[dict[str, str]],
    target_date: date,
    blood_pressure_records: list[dict[str, str]] | None = None,
) -> list[tuple[str, str]]:
    return [
        (label, value.replace(" · ", " / "))
        for label, value in _body_kv_rows(
            measures,
            historical_measures,
            target_date,
            blood_pressure_records or [],
        )
    ]


def _body_kv_rows(
    measures: list[dict[str, str]],
    historical_measures: list[dict[str, str]],
    target_date: date,
    blood_pressure_records: list[dict[str, str]],
) -> list[tuple[str, str]]:
    blood_pressure = _latest_blood_pressure(blood_pressure_records)
    if not measures:
        return [("Blood pressure", _blood_pressure_value(blood_pressure))] if blood_pressure else []

    latest_by_type = _latest_measures_by_type(measures)
    main_types = {
        "weight",
        "fat_ratio",
        "fat_mass_weight",
        "muscle_mass",
        "hydration",
        "fat_free_mass",
        "bone_mass",
        "height",
        "type_4",
    }
    rows = [
        (
            "Weight",
            " / ".join(
                [
                    _measure_value(latest_by_type.get("weight")),
                    f"fat {_compact_body_fat_value(latest_by_type)}",
                    f"muscle {_measure_value(latest_by_type.get('muscle_mass'))}",
                ]
            ),
        ),
        (
            "Mass",
            " / ".join(
                [
                    f"FFM {_measure_value(latest_by_type.get('fat_free_mass'))}",
                    f"water {_measure_value(latest_by_type.get('hydration'))}",
                    f"bone {_measure_value(latest_by_type.get('bone_mass'))}",
                ]
            ),
        ),
    ]
    for type_name, measure in latest_by_type.items():
        if type_name not in main_types:
            rows.append((type_name or "measurement", _measure_value(measure)))
    bmi = _bmi_value(latest_by_type.get("weight"), _latest_historical_height(historical_measures, target_date))
    bmr = _bmr_value(latest_by_type.get("fat_free_mass"))
    index_parts = []
    if bmi is not None:
        index_parts.append(f"BMI {bmi}")
    if bmr is not None:
        index_parts.append(f"BMR {bmr}")
    if index_parts:
        rows.append(("Index", " / ".join(index_parts)))
    if blood_pressure:
        rows.append(("Blood pressure", _blood_pressure_value(blood_pressure)))
    return rows


def _latest_blood_pressure(records: list[dict[str, str]]) -> dict[str, str] | None:
    if not records:
        return None
    return max(records, key=lambda record: record.get("datetime_local", ""))


def _blood_pressure_value(record: dict[str, str]) -> str:
    systolic = record.get("systolic_mmHg", "")
    diastolic = record.get("diastolic_mmHg", "")
    measured_at = _blood_pressure_clock_time(record.get("datetime_local", ""))
    if measured_at:
        return f"{systolic}/{diastolic} mmHg · {measured_at}"
    return f"{systolic}/{diastolic} mmHg"


def _blood_pressure_clock_time(value: str) -> str:
    parsed = _parse_timestamp(value)
    return parsed.strftime("%H:%M") if parsed is not None else ""


def _latest_measures_by_type(measures: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for measure in measures:
        type_name = measure.get("type_name", "")
        current = latest.get(type_name)
        if current is None or measure.get("datetime_local", "") >= current.get("datetime_local", ""):
            latest[type_name] = measure
    return latest


def _measure_value(measure: dict[str, str] | None) -> str:
    if measure is None:
        return "Unknown"
    return f"{measure.get('value') or '0.00'} {measure.get('unit') or ''}".rstrip()


def _bmr_value(fat_free_mass: dict[str, str] | None) -> str | None:
    if fat_free_mass is None:
        return None
    fat_free_mass_kg = _float_value(fat_free_mass.get("value", ""))
    if fat_free_mass_kg is None:
        return None
    return f"{int((370 + (21.6 * fat_free_mass_kg)) + 0.5)} kcal/day"


def _bmi_value(weight: dict[str, str] | None, height: dict[str, str] | None) -> str | None:
    if weight is None or height is None:
        return None
    weight_kg = _float_value(weight.get("value", ""))
    height_m = _float_value(height.get("value", ""))
    if weight_kg is None or height_m is None or height_m <= 0:
        return None
    return f"{weight_kg / (height_m * height_m):.2f}"


def _latest_historical_height(
    historical_measures: list[dict[str, str]],
    target_date: date,
) -> dict[str, str] | None:
    height_measures = [
        measure
        for measure in historical_measures
        if measure.get("type_name") in {"height", "type_4"}
        and (measure_date := _measure_date(measure)) is not None
        and measure_date <= target_date
    ]
    if not height_measures:
        return None
    return max(
        height_measures,
        key=lambda measure: (
            _measure_date(measure) or date.min,
            measure.get("datetime_local", ""),
        ),
    )


def _body_fat_value(measures: dict[str, dict[str, str]]) -> str:
    fat_ratio = _measure_value(measures.get("fat_ratio"))
    fat_mass = _measure_value(measures.get("fat_mass_weight"))
    if fat_mass != "Unknown":
        return f"{fat_ratio} · {fat_mass}"

    weight = measures.get("weight")
    ratio = measures.get("fat_ratio")
    if weight is None or ratio is None:
        return fat_ratio

    fat_mass_value = _float_value(weight.get("value", "")) * _float_value(ratio.get("value", "")) / 100
    if fat_mass_value <= 0:
        return fat_ratio
    return f"{fat_ratio} · {fat_mass_value:.2f} kg fat mass"


def _compact_body_fat_value(measures: dict[str, dict[str, str]]) -> str:
    fat_ratio = _measure_value(measures.get("fat_ratio")).replace(" %", "%")
    fat_mass = _measure_value(measures.get("fat_mass_weight"))
    if fat_mass != "Unknown":
        return f"{fat_ratio} ({fat_mass})"

    weight = measures.get("weight")
    ratio = measures.get("fat_ratio")
    if weight is None or ratio is None:
        return fat_ratio

    fat_mass_value = _float_value(weight.get("value", "")) * _float_value(ratio.get("value", "")) / 100
    if fat_mass_value <= 0:
        return fat_ratio
    return f"{fat_ratio} ({fat_mass_value:.2f} kg)"


def _missing_data_summary(
    withings_steps: int | None,
    measures: list[dict[str, str]],
    activities: list[NormalizedActivity],
    sleep: dict[str, str] | None = None,
    sleep_expected: bool = False,
) -> str:
    missing: list[str] = []
    if withings_steps is None:
        missing.append("Withings steps unavailable")
    if not measures:
        missing.append("body measures unavailable")
    if not activities:
        missing.append("no activities logged")
    if sleep_expected and sleep is None:
        missing.append("sleep unavailable")
    if not missing:
        return "None"
    return "; ".join(missing)


def _body_source_label(
    measures: list[dict[str, str]],
    blood_pressure_records: list[dict[str, str]],
) -> str:
    sources = []
    if measures:
        sources.append("Withings")
    if blood_pressure_records:
        sources.append("Vitalsync")
    return ", ".join(sources) if sources else "None"


def _terminal_data_coverage_rows(
    activities: list[NormalizedActivity],
    withings_steps: int | None,
    measures: list[dict[str, str]],
    blood_pressure_records: list[dict[str, str]],
    training_load: TrainingLoadMetrics | None,
    sleep: dict[str, str] | None,
    sleep_expected: bool,
) -> list[tuple[str, str]]:
    rows = [
        ("Workout source", _activity_sources(activities)),
        ("Step source", "Withings" if withings_steps is not None else "None"),
        ("Body source", _body_source_label(measures, blood_pressure_records)),
        ("Sleep source", _sleep_source_label(sleep)),
        ("Activity count", f"{len(activities)} primary"),
    ]
    if training_load is not None:
        rows.append(
            (
                "Training load history",
                _training_load_history_status(training_load.history_label),
            )
        )
    rows.append(
        (
            "Missing data",
            _missing_data_summary(
                withings_steps,
                measures,
                activities,
                sleep,
                sleep_expected,
            ),
        )
    )
    return rows


def _primary_sleep(sleep_records: list[dict[str, str]]) -> dict[str, str] | None:
    if not sleep_records:
        return None
    return max(
        sleep_records,
        key=lambda sleep: (
            _float_value(sleep.get("total_sleep_min", "")),
            sleep.get("end_time", ""),
        ),
    )


def _sleep_expected(
    historical_sleep_records: list[dict[str, str]],
    target_date: date,
) -> bool:
    wake_dates = [
        parsed
        for sleep in historical_sleep_records
        if (parsed := _date_from_value(sleep.get("wake_date", ""))) is not None
    ]
    return bool(wake_dates and min(wake_dates) <= target_date)


def _sleep_snapshot_status(
    sleep: dict[str, str],
    *,
    separator: str = " · ",
) -> str:
    duration = _format_sleep_duration(_float_value(sleep.get("total_sleep_min", "")))
    bedtime = _sleep_clock_time(sleep.get("start_time", ""))
    wake_time = _sleep_clock_time(sleep.get("end_time", ""))
    return f"{duration}{separator}{bedtime}–{wake_time}"


def _sleep_source_label(sleep: dict[str, str] | None) -> str:
    if sleep is None:
        return "None"
    source = sleep.get("source", "")
    if source == "vitalsync":
        return "Vitalsync"
    if source == "withings":
        return "Withings"
    return source.title() if source else "Unknown"


def _format_sleep_duration(minutes: float) -> str:
    rounded_minutes = max(0, round(minutes))
    hours, remainder = divmod(rounded_minutes, 60)
    return f"{hours}h{remainder:02d}m"


def _sleep_clock_time(value: str) -> str:
    parsed = _parse_timestamp(value)
    return parsed.strftime("%H:%M") if parsed is not None else "Unknown"


def _date_from_value(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _withings_step_count(activity_summaries: list[dict[str, str]]) -> int | None:
    values = [
        _optional_int_value(activity.get("step_count", ""))
        for activity in activity_summaries
    ]
    present_values = [value for value in values if value is not None]
    if not present_values:
        return None
    return sum(present_values)


def _format_step_count(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:,}"


def _sets_for_strength_activities(
    strength_activities: list[NormalizedActivity],
    hevy_sets: list[dict[str, str]],
) -> list[dict[str, str]]:
    workout_ids = {
        workout_id
        for activity in strength_activities
        if (workout_id := _hevy_workout_id(activity))
    }
    return [
        set_row
        for set_row in hevy_sets
        if set_row.get("workout_source_id") in workout_ids
    ]


def _weight_metrics(measures: list[dict[str, str]], target_date: date) -> dict[str, str]:
    weights = [
        measure
        for measure in measures
        if measure.get("type_name", "").lower() == "weight"
        and (measure_date := _measure_date(measure)) is not None
        and measure_date <= target_date
    ]
    latest_weight = max(weights, key=lambda measure: measure.get("datetime_local", "")) if weights else None
    current_weight = _format_weight(latest_weight) if latest_weight else "No Withings weight available"

    current_7d = _average_weight(weights, target_date, days=7)
    previous_7d = _average_weight(weights, target_date - _date_delta(7), days=7)
    avg_30d = _average_weight(weights, target_date, days=30)
    return {
        "current_weight": current_weight,
        "avg_7d": _format_average_weight(current_7d),
        "avg_30d": _format_average_weight(avg_30d),
        "trend": _weight_trend(current_7d, previous_7d),
    }


def _estimated_deficit_metrics(
    measures: list[dict[str, str]],
    target_date: date,
) -> EstimatedDeficitMetrics | None:
    weights = _weight_measurements(measures, target_date)
    today = _daily_estimated_deficit(weights, target_date)
    if today is None:
        return None
    return EstimatedDeficitMetrics(
        today=today,
        avg_7d=_average_estimated_deficit(weights, target_date, days=7),
        avg_30d=_average_estimated_deficit(weights, target_date, days=30),
    )


def _weight_measurements(measures: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    return [
        measure
        for measure in measures
        if measure.get("type_name", "").lower() == "weight"
        and (measure_date := _measure_date(measure)) is not None
        and measure_date <= target_date
        and _optional_float_value(measure.get("value", "")) is not None
    ]


def _daily_estimated_deficit(weights: list[dict[str, str]], target_date: date) -> float | None:
    current_weight = _latest_weight_for_date(weights, target_date)
    if current_weight is None:
        return None

    comparison_weight = _weight_closest_to_history_anchor(weights, target_date - _date_delta(30))
    if comparison_weight is None:
        return None

    current_value = _float_value(current_weight.get("value", ""))
    comparison_value = _float_value(comparison_weight.get("value", ""))
    if current_value is None or comparison_value is None:
        return None

    weight_change_kg = comparison_value - current_value
    return (weight_change_kg * 7700) / 30


def _latest_weight_for_date(weights: list[dict[str, str]], target_date: date) -> dict[str, str] | None:
    weights_for_date = [measure for measure in weights if _measure_date(measure) == target_date]
    if not weights_for_date:
        return None
    return max(weights_for_date, key=lambda measure: measure.get("datetime_local", ""))


def _weight_closest_to_history_anchor(weights: list[dict[str, str]], anchor_date: date) -> dict[str, str] | None:
    candidates = [
        measure
        for measure in weights
        if (measure_date := _measure_date(measure)) is not None
        and measure_date <= anchor_date
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda measure: (
            _measure_date(measure) or date.min,
            measure.get("datetime_local", ""),
        ),
    )


def _average_estimated_deficit(weights: list[dict[str, str]], end_date: date, *, days: int) -> float | None:
    values = [
        value
        for day_offset in range(days)
        if (value := _daily_estimated_deficit(weights, end_date - _date_delta(day_offset))) is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _activity_trend_metrics(
    today_activities: list[NormalizedActivity],
    activities: list[NormalizedActivity],
    target_date: date,
) -> list[ActivityTrendMetric]:
    primary_type = _primary_activity_type(today_activities)
    if primary_type is None:
        return []

    today_same_type = [
        activity for activity in today_activities if activity.activity_type == primary_type
    ]
    rows: list[ActivityTrendMetric] = []
    distance = sum(
        activity.distance_km or 0.0
        for activity in today_same_type
        if primary_type != "swim" or activity.source == "suunto"
    )
    duration = sum(activity.duration_min for activity in today_same_type)
    label = _activity_type_label(primary_type)

    if distance > 0:
        distance_history = _activity_metric_history(
            activities,
            target_date,
            primary_type,
            metric="distance",
        )
        rows.append(
            ActivityTrendMetric(
                label=f"{label} distance",
                today=distance,
                total_7d=distance_history["total_7d"],
                weekly_avg_30d=distance_history["weekly_avg_30d"],
                unit="km",
                direction=_activity_trend_direction(
                    primary_type,
                    distance_history,
                ),
            )
        )
    duration_history = _activity_metric_history(
        activities,
        target_date,
        primary_type,
        metric="duration",
    )
    rows.append(
        ActivityTrendMetric(
            label=f"{label} duration",
            today=duration,
            total_7d=duration_history["total_7d"],
            weekly_avg_30d=duration_history["weekly_avg_30d"],
            unit="min",
            direction=_activity_trend_direction(
                primary_type,
                duration_history,
            ),
        )
    )
    return rows


def _performance_trend_metrics(
    today_activities: list[NormalizedActivity],
    activities: list[NormalizedActivity],
    target_date: date,
) -> list[PerformanceTrendMetric]:
    primary_type = _primary_activity_type(today_activities)
    if primary_type not in {"walk", "run", "swim", "ride"}:
        return []

    today_same_type = [
        activity
        for activity in today_activities
        if activity.activity_type == primary_type
        and _performance_activity_values(activity) is not None
    ]
    today_value = _aggregate_performance(today_same_type, primary_type)
    if today_value is None:
        return []

    history = _performance_metric_history(
        activities,
        target_date,
        primary_type,
    )
    avg_7d = history["avg_7d"]
    avg_30d = history["avg_30d"]
    if avg_7d is None or avg_30d is None:
        return []

    label = _activity_type_label(primary_type)
    unit = "km/h" if primary_type == "ride" else (
        "min/100m" if primary_type == "swim" else "min/km"
    )
    metric_name = "speed" if primary_type == "ride" else "pace"
    return [
        PerformanceTrendMetric(
            label=f"{label} {metric_name}",
            today=today_value,
            avg_7d=avg_7d,
            avg_30d=avg_30d,
            unit=unit,
            direction=_performance_trend_direction(
                today=today_value,
                avg_30d=avg_30d,
                sessions_30d=int(history["sessions_30d"]),
                lower_is_faster=primary_type != "ride",
            ),
        )
    ]


def _performance_metric_history(
    activities: list[NormalizedActivity],
    end_date: date,
    activity_type: str,
) -> dict[str, float | int | None]:
    activities_to_date = [
        activity
        for activity in activities
        if activity.activity_type == activity_type
        and (activity_date := _activity_date(activity.start_time)) is not None
        and activity_date <= end_date
        and _performance_activity_values(activity) is not None
    ]
    trailing_7 = [
        activity
        for activity in activities_to_date
        if (_activity_date(activity.start_time) or date.min)
        >= end_date - _date_delta(6)
    ]
    trailing_30 = [
        activity
        for activity in activities_to_date
        if (_activity_date(activity.start_time) or date.min)
        >= end_date - _date_delta(29)
    ]
    return {
        "avg_7d": _aggregate_performance(trailing_7, activity_type),
        "avg_30d": _aggregate_performance(trailing_30, activity_type),
        "sessions_30d": len(trailing_30),
    }


def _performance_activity_values(
    activity: NormalizedActivity,
) -> tuple[float, float] | None:
    if activity.activity_type == "swim" and activity.source != "suunto":
        return None
    if (
        activity.distance_km is None
        or activity.distance_km <= 0
        or activity.duration_min <= 0
    ):
        return None
    return activity.distance_km, activity.duration_min


def _aggregate_performance(
    activities: list[NormalizedActivity],
    activity_type: str,
) -> float | None:
    values = [
        values
        for activity in activities
        if (values := _performance_activity_values(activity)) is not None
    ]
    if not values:
        return None
    total_distance_km = sum(distance for distance, _ in values)
    total_duration_min = sum(duration for _, duration in values)
    if total_distance_km <= 0 or total_duration_min <= 0:
        return None
    if activity_type == "ride":
        return total_distance_km / (total_duration_min / 60)
    if activity_type == "swim":
        return total_duration_min / (total_distance_km * 10)
    return total_duration_min / total_distance_km


def _performance_trend_direction(
    *,
    today: float,
    avg_30d: float,
    sessions_30d: int,
    lower_is_faster: bool,
) -> str:
    if sessions_30d < 3:
        return "Baseline forming"
    if avg_30d <= 0:
        return "Baseline forming"
    difference_ratio = abs(today - avg_30d) / avg_30d
    if difference_ratio < 0.02:
        return "Near 30-day average"
    faster = today < avg_30d if lower_is_faster else today > avg_30d
    return "Faster than 30-day average" if faster else "Slower than 30-day average"


def _training_load_trend_metric(
    activities: list[NormalizedActivity],
    target_date: date,
) -> ActivityTrendMetric | None:
    tss_activities = [
        activity
        for activity in activities
        if activity.source == "suunto"
        and activity.tss_score is not None
        and (activity_date := _activity_date(activity.start_time)) is not None
        and activity_date <= target_date
    ]
    if not tss_activities:
        return None

    trailing_7 = [
        activity
        for activity in tss_activities
        if (_activity_date(activity.start_time) or date.min)
        >= target_date - _date_delta(6)
    ]
    trailing_30 = [
        activity
        for activity in tss_activities
        if (_activity_date(activity.start_time) or date.min)
        >= target_date - _date_delta(29)
    ]
    today_tss = sum(
        activity.tss_score or 0.0
        for activity in tss_activities
        if _activity_date(activity.start_time) == target_date
    )
    total_7d = sum(activity.tss_score or 0.0 for activity in trailing_7)
    total_30d = sum(activity.tss_score or 0.0 for activity in trailing_30)
    history_days = (
        target_date
        - min(
            _activity_date(activity.start_time) or target_date
            for activity in tss_activities
        )
    ).days + 1
    weekly_avg_30d = total_30d * 7 / 30
    return ActivityTrendMetric(
        label="TSS",
        today=today_tss,
        total_7d=total_7d,
        weekly_avg_30d=weekly_avg_30d,
        unit="TSS",
        direction=_training_load_trend_direction(
            sessions_30d=len(trailing_30),
            history_days=history_days,
            total_7d=total_7d,
            weekly_avg_30d=weekly_avg_30d,
        ),
    )


def _primary_activity_type(activities: list[NormalizedActivity]) -> str | None:
    duration_by_type: dict[str, float] = {}
    for activity in activities:
        duration_by_type[activity.activity_type] = (
            duration_by_type.get(activity.activity_type, 0.0) + activity.duration_min
        )
    if not duration_by_type:
        return None
    return max(duration_by_type, key=duration_by_type.get)


def _activity_type_label(activity_type: str) -> str:
    return {
        "walk": "Walking",
        "run": "Running",
        "ride": "Cycling",
        "swim": "Swimming",
        "strength": "Strength",
    }.get(activity_type, "Workout")


def _activity_metric_history(
    activities: list[NormalizedActivity],
    end_date: date,
    activity_type: str,
    *,
    metric: str,
) -> dict[str, float | int]:
    activities_to_date = [
        activity
        for activity in activities
        if (activity_date := _activity_date(activity.start_time)) is not None
        and activity_date <= end_date
        and activity.activity_type == activity_type
        and _activity_metric_value(activity, metric) is not None
    ]
    trailing_7 = [
        activity
        for activity in activities_to_date
        if (_activity_date(activity.start_time) or date.min)
        >= end_date - _date_delta(6)
    ]
    trailing_30 = [
        activity
        for activity in activities_to_date
        if (_activity_date(activity.start_time) or date.min)
        >= end_date - _date_delta(29)
    ]
    total_7d = sum(
        _activity_metric_value(activity, metric) or 0.0
        for activity in trailing_7
    )
    total_30d = sum(
        _activity_metric_value(activity, metric) or 0.0
        for activity in trailing_30
    )
    return {
        "total_7d": total_7d,
        "weekly_avg_30d": total_30d * 7 / 30,
        "sessions_30d": len(trailing_30),
        "sessions_all": len(activities_to_date),
    }


def _activity_metric_value(
    activity: NormalizedActivity,
    metric: str,
) -> float | None:
    if metric == "distance":
        if activity.activity_type == "swim" and activity.source != "suunto":
            return None
        return activity.distance_km
    if metric == "duration":
        return activity.duration_min if activity.duration_min > 0 else None
    return None


def _activity_trend_direction(
    activity_type: str,
    history: dict[str, float | int],
) -> str:
    sessions_all = int(history["sessions_all"])
    sessions_30d = int(history["sessions_30d"])
    if sessions_all == 1:
        return f"First recorded {_activity_type_noun(activity_type)}"
    if sessions_30d < 3:
        return "Baseline forming"

    total_7d = float(history["total_7d"])
    weekly_avg_30d = float(history["weekly_avg_30d"])
    if weekly_avg_30d <= 0:
        return "Baseline forming"
    difference = total_7d - weekly_avg_30d
    percent_diff = difference / weekly_avg_30d * 100
    if abs(percent_diff) < 10:
        return "Near 30-day weekly average"
    direction = "Above" if percent_diff > 0 else "Below"
    return f"{direction} 30-day weekly average ({_format_percent_diff(percent_diff)})"


def _training_load_trend_direction(
    *,
    sessions_30d: int,
    history_days: int,
    total_7d: float,
    weekly_avg_30d: float,
) -> str:
    if sessions_30d < 3:
        return "Baseline forming"
    if history_days < 42:
        return "Training load history limited"
    if weekly_avg_30d <= 0:
        return "Baseline forming"
    percent_diff = (total_7d - weekly_avg_30d) / weekly_avg_30d * 100
    if abs(percent_diff) < 10:
        return "Near 30-day weekly average"
    direction = "Above" if percent_diff > 0 else "Below"
    return f"{direction} 30-day weekly average ({_format_percent_diff(percent_diff)})"


def _activity_type_noun(activity_type: str) -> str:
    return {
        "walk": "walk",
        "run": "run",
        "ride": "ride",
        "swim": "swim",
        "strength": "strength workout",
    }.get(activity_type, "workout")


def _format_activity_trend_value(value: float, unit: str) -> str:
    if unit == "km":
        return f"{value:.2f} km"
    if unit == "min":
        return f"{value:.0f} min"
    return f"{value:.1f} TSS"


def _format_activity_trend_total(value: float, unit: str) -> str:
    if unit == "km":
        return f"{value:.2f} km/week"
    if unit == "min":
        return f"{value:.0f} min/week"
    return f"{value:.1f} TSS/week"


def _format_activity_trend_weekly_average(value: float, unit: str) -> str:
    if unit == "km":
        return f"{value:.2f} km/week"
    if unit == "min":
        return f"{value:.0f} min/week"
    return f"{value:.1f} TSS/week"


def _render_activity_trend_row(metric: ActivityTrendMetric) -> str:
    return (
        f"| {metric.label} | "
        f"{_format_activity_trend_value(metric.today, metric.unit)} | "
        f"{_format_activity_trend_total(metric.total_7d, metric.unit)} | "
        f"{_format_activity_trend_weekly_average(metric.weekly_avg_30d, metric.unit)} | "
        f"{metric.direction} |"
    )


def _format_performance_trend_value(value: float, unit: str) -> str:
    if unit == "km/h":
        return f"{value:.2f} km/h"
    total_seconds = round(value * 60)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d} {unit}"


def _render_performance_trend_row(metric: PerformanceTrendMetric) -> str:
    return (
        f"| {metric.label} | "
        f"{_format_performance_trend_value(metric.today, metric.unit)} | "
        f"{_format_performance_trend_value(metric.avg_7d, metric.unit)} | "
        f"{_format_performance_trend_value(metric.avg_30d, metric.unit)} | "
        f"{metric.direction} |"
    )


def _format_weight(measure: dict[str, str]) -> str:
    value = measure.get("value") or "0.00"
    unit = measure.get("unit") or "kg"
    return f"{value} {unit}".rstrip()


def _average_weight(measures: list[dict[str, str]], end_date: date, *, days: int) -> float | None:
    start_date = end_date - _date_delta(days - 1)
    values = [
        _float_value(measure.get("value", ""))
        for measure in measures
        if (measure_date := _measure_date(measure)) is not None
        and start_date <= measure_date <= end_date
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _format_average_weight(value: float | None) -> str:
    if value is None:
        return "Unknown"
    return f"{value:.2f} kg"


def _format_estimated_deficit(value: float) -> str:
    rounded = round(value)
    if rounded == 0:
        return "0 kcal/day"
    return f"{rounded} kcal/day"


def _format_average_estimated_deficit(value: float | None) -> str:
    if value is None:
        return "Unknown"
    return _format_estimated_deficit(value)


def _weight_trend(current_7d: float | None, previous_7d: float | None) -> str:
    if current_7d is None or previous_7d is None:
        return "Unknown"

    difference = current_7d - previous_7d
    if difference <= -0.3:
        return "Decreasing"
    if difference >= 0.3:
        return "Increasing"
    return "Stable"


def _measure_date(measure: dict[str, str]) -> date | None:
    try:
        return date.fromisoformat(measure.get("date", ""))
    except ValueError:
        return None


def _activity_date(raw_value: str) -> date | None:
    parsed = _parse_timestamp(raw_value)
    return parsed.date() if parsed is not None else None


def _timestamp_date(raw_value: str, local_timezone: ZoneInfo) -> date | None:
    parsed = _parse_timestamp(raw_value, local_timezone)
    return parsed.astimezone(local_timezone).date() if parsed is not None else None


def _parse_timestamp(
    value: str,
    local_timezone: ZoneInfo | None = None,
) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        if local_timezone is None:
            return None
        parsed = parsed.replace(tzinfo=local_timezone)
    return parsed


def _date_delta(days: int) -> timedelta:
    return timedelta(days=days)


def _ai_handoff(
    *,
    activities: list[NormalizedActivity],
    total_duration_min: float,
    withings_steps_text: str,
    walking_distance_km: float,
    swimming_duration_min: float,
    swimming_distance_km: float,
    strength_count: int,
    strength_duration_min: float,
    suunto_metrics: SuuntoDailyMetrics,
    training_load_metrics: TrainingLoadMetrics | None,
    activity_trends: list[ActivityTrendMetric],
    performance_trends: list[PerformanceTrendMetric],
    training_load_trend: ActivityTrendMetric | None,
    weight_metrics: dict[str, str],
    bmi: str | None,
    bmr: str | None,
    estimated_deficit: str | None,
    sleep: dict[str, str] | None,
) -> str:
    if not activities:
        activity_sentence = "No primary activities found for this date."
    else:
        walking_part = (
            f", {walking_distance_km:.2f} km walking"
            if walking_distance_km > 0
            else ""
        )
        activity_sentence = (
            f"Recorded {len(activities)} {_pluralize(len(activities), 'primary activity', 'primary activities')}"
            f"{walking_part}, {total_duration_min:.0f} min moving time, "
            f"and {withings_steps_text} Withings steps."
        )
    swimming_parts: list[str] = []
    if swimming_duration_min > 0:
        swimming_parts.append(f"{swimming_duration_min:.0f} min")
    if swimming_distance_km > 0:
        swimming_parts.append(f"{swimming_distance_km:.2f} km")
    swimming_sentence = (
        f" Swimming included {' and '.join(swimming_parts)}."
        if swimming_parts
        else ""
    )
    strength_sentence = (
        f" Strength training included {strength_count} {_pluralize(strength_count, 'workout', 'workouts')} and {strength_duration_min:.0f} min."
        if strength_count > 0
        else ""
    )
    body_sentences = [
        f"Current weight is {weight_metrics['current_weight']}.",
        f"Weight trend is {weight_metrics['trend']}.",
    ]
    if bmi is not None:
        body_sentences.append(f"BMI is {bmi}.")
    if bmr is not None:
        body_sentences.append(f"BMR is {bmr}.")
    if estimated_deficit is not None:
        body_sentences.append(f"Estimated energy deficit is {estimated_deficit}.")
    sleep_sentence = ""
    if sleep is not None:
        sleep_sentence = (
            f" Sleep: {_format_sleep_duration(_float_value(sleep.get('total_sleep_min', '')))}, "
            f"{_sleep_clock_time(sleep.get('start_time', ''))}–"
            f"{_sleep_clock_time(sleep.get('end_time', ''))}, source {_sleep_source_label(sleep)}."
        )
    return (
        f"{activity_sentence}{_suunto_handoff_sentence(suunto_metrics, training_load_metrics)}"
        f"{swimming_sentence}{strength_sentence} "
        f"{_activity_trend_handoff_sentence(activity_trends)}"
        f"{_performance_trend_handoff_sentence(performance_trends)}"
        f"{_training_load_trend_handoff_sentence(training_load_trend)}"
        f"{sleep_sentence} {' '.join(body_sentences)}"
    )


def _suunto_handoff_sentence(
    metrics: SuuntoDailyMetrics,
    training_load: TrainingLoadMetrics | None,
) -> str:
    parts: list[str] = []
    if training_load is not None:
        parts.append(f"TSS {training_load.today_tss:.1f}")
    elif metrics.total_tss is not None:
        parts.append(f"TSS {metrics.total_tss:.1f}")
    if metrics.avg_hr is not None:
        parts.append(f"average HR {metrics.avg_hr:.0f}")
    if metrics.max_hr is not None:
        parts.append(f"maximum HR {metrics.max_hr:.0f}")
    if metrics.total_energy_kcal is not None:
        parts.append(f"activity energy {metrics.total_energy_kcal:.0f} kcal")
    if training_load is not None:
        tsb_state = (
            "warming up"
            if training_load.history_days < 7
            else training_load.tsb_label
        )
        parts.extend(
            [
                f"end-of-day ingest-defined CTL {training_load.ctl:.1f}",
                f"ATL {training_load.atl:.1f}",
                f"TSB {training_load.tsb:.1f}",
                f"TSB state {tsb_state}",
                training_load.history_label,
            ]
        )
    if not parts:
        return ""
    return f" Suunto metrics: {', '.join(parts)}."


def _pluralize(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _activity_trend_handoff_sentence(
    metrics: list[ActivityTrendMetric],
) -> str:
    if not metrics:
        return ""
    metric = metrics[0]
    if metric.direction.startswith("First recorded"):
        return f"{metric.direction} in available history. "
    if metric.direction == "Baseline forming":
        return f"{_activity_type_label(_primary_activity_type_from_label(metric.label))} baseline is still forming. "
    return f"{metric.label} is {metric.direction.lower()}. "


def _performance_trend_handoff_sentence(
    metrics: list[PerformanceTrendMetric],
) -> str:
    if not metrics:
        return ""
    metric = metrics[0]
    if metric.direction == "Baseline forming":
        return f"{metric.label} baseline is still forming. "
    return f"{metric.label} was {metric.direction.lower()}. "


def _training_load_trend_handoff_sentence(
    metric: ActivityTrendMetric | None,
) -> str:
    if metric is None:
        return ""
    weekly_total = _format_activity_trend_total(metric.total_7d, metric.unit)
    return f"Training load over trailing 7 days is {weekly_total}; {metric.direction.lower()}. "


def _primary_activity_type_from_label(label: str) -> str:
    prefix = label.split(" ", 1)[0].lower()
    return {
        "walking": "walk",
        "running": "run",
        "cycling": "ride",
        "swimming": "swim",
        "strength": "strength",
    }.get(prefix, "workout")


def _is_walking_activity(activity: NormalizedActivity) -> bool:
    return activity.activity_type == "walk"


def _format_distance(value: float | None) -> str:
    if value is None:
        return "unknown distance"
    return f"{value:.2f} km"


def _distance_activity_parts(activity: NormalizedActivity) -> list[str]:
    parts: list[str] = []
    if activity.distance_km is not None:
        parts.append(_format_distance(activity.distance_km))
    parts.append(f"{activity.duration_min:.0f} min")
    parts.extend(_activity_metric_parts(activity))
    return parts


def _display_activity_name(activity: NormalizedActivity) -> str:
    name = activity.detail_name or activity.name or f"{activity.source}:{activity.source_id}"
    translations = {
        "屋外で歩行": "Outdoor Walking",
        "屋内で歩行": "Indoor Walking",
        "屋外ランニング": "Outdoor Running",
        "屋外でランニング": "Outdoor Running",
        "ランニング": "Running",
        "室内ランニング": "Indoor Running",
        "屋内ランニング": "Indoor Running",
        "トレッドミル": "Treadmill Running",
    }
    return translations.get(name, name)


def _render_activity_sections(activities: list[NormalizedActivity], hevy_sets: list[dict[str, str]]) -> list[str]:
    lines = ["## Activities", ""]
    walking_activities = [activity for activity in activities if _is_walking_activity(activity)]
    swimming_activities = [activity for activity in activities if activity.activity_type == "swim"]
    workout_activities = [activity for activity in activities if activity.activity_type == "strength"]
    other_activities = [
        activity
        for activity in activities
        if activity not in [*walking_activities, *swimming_activities, *workout_activities]
    ]

    if walking_activities:
        lines.extend(["### Walking", ""])
        lines.extend(_render_distance_activities(walking_activities))
        lines.append("")

    if swimming_activities:
        lines.extend(["### Swimming", ""])
        lines.extend(_render_duration_activities(swimming_activities))
        lines.append("")

    if workout_activities:
        lines.extend(["### Workout", ""])
        lines.extend(_render_workout_activities(workout_activities, hevy_sets))
        lines.append("")

    if other_activities:
        lines.extend(["### Other", ""])
        lines.extend(_render_distance_activities(other_activities))
        lines.append("")

    return lines


def _render_distance_activities(activities: list[NormalizedActivity]) -> list[str]:
    lines: list[str] = []
    for activity in activities:
        parts = _distance_activity_parts(activity)
        lines.append(
            f"- {activity.raw_type or 'Unknown'}: "
            f"{_display_activity_name(activity)} ({', '.join(parts)})"
        )
    return lines


def _render_duration_activities(activities: list[NormalizedActivity]) -> list[str]:
    lines: list[str] = []
    for activity in activities:
        parts = [f"{activity.duration_min:.0f} min", *_activity_metric_parts(activity)]
        lines.append(
            f"- {activity.raw_type or 'Unknown'}: "
            f"{_display_activity_name(activity)} ({', '.join(parts)})"
        )
    return lines


def _render_workout_activities(activities: list[NormalizedActivity], hevy_sets: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for activity in activities:
        hevy_workout_id = _hevy_workout_id(activity)
        workout_sets = [
            set_row
            for set_row in hevy_sets
            if set_row.get("workout_source_id") == hevy_workout_id
        ]
        total_sets = len(workout_sets)
        total_volume_kg = sum(_float_value(set_row.get("volume_kg", "")) for set_row in workout_sets)
        parts = [f"{activity.duration_min:.0f} min", *_activity_metric_parts(activity)]
        if activity.detail_source == "hevy":
            lines.append(
                f"- {activity.raw_type or 'STRENGTH'} "
                f"{activity.source}:{activity.source_id} / {' / '.join(parts)}"
            )
            hevy_parts = [f"Hevy {_display_activity_name(activity)}"]
            if workout_sets:
                hevy_parts.extend(
                    [
                        f"{total_sets} sets",
                        f"{_format_volume(total_volume_kg)} volume",
                    ]
                )
            lines.append(f"  - {' / '.join(hevy_parts)}")
            for summary in _exercise_summaries(workout_sets):
                lines.append(f"    - {summary}")
            continue
        lines.append(f"- {_display_activity_name(activity)}: {' / '.join(parts)}")
        if workout_sets:
            lines.append(f"  - Sets: {total_sets}")
            lines.append(f"  - Volume: {_format_volume(total_volume_kg)}")
            for summary in _exercise_summaries(workout_sets):
                lines.append(f"  - {summary}")
    return lines


def _exercise_summaries(sets: list[dict[str, str]]) -> list[str]:
    by_exercise: dict[str, list[dict[str, str]]] = {}
    for set_row in sets:
        by_exercise.setdefault(set_row.get("exercise") or "Unknown exercise", []).append(set_row)

    summaries: list[str] = []
    for exercise, exercise_sets in by_exercise.items():
        set_count = len(exercise_sets)
        volume_kg = sum(_float_value(set_row.get("volume_kg", "")) for set_row in exercise_sets)
        set_details = ", ".join(_format_set_detail(set_row) for set_row in exercise_sets)
        summaries.append(f"{exercise}: {set_count} sets, {_format_volume(volume_kg)} ({set_details})")
    return summaries


def _format_set_detail(set_row: dict[str, str]) -> str:
    weight = set_row.get("weight_kg", "")
    reps = set_row.get("reps", "")
    if weight and reps:
        return f"{weight} kg x {reps}"
    if reps:
        return f"{reps} reps"
    duration = set_row.get("duration_seconds", "")
    if duration:
        return f"{duration}s"
    return "logged set"


def _format_volume(value: float) -> str:
    return f"{value:.0f} kg" if value else "0 kg"


def _normalize_withings_activities(
    activities: list[dict[str, str]],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[NormalizedActivity]:
    return [normalize_withings_activity(activity, local_timezone) for activity in activities]


def _normalize_activities(
    activities: list[dict[str, str]],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[NormalizedActivity]:
    normalizers = {
        "hevy": normalize_hevy_activity,
        "suunto": normalize_suunto_activity,
        "withings": normalize_withings_activity,
    }
    return [
        normalizers.get(
            (activity.get("source") or "withings").strip().lower(),
            normalize_withings_activity,
        )(activity, local_timezone)
        for activity in activities
    ]


def _normalize_hevy_activities(
    activities: list[dict[str, str]],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[NormalizedActivity]:
    return [normalize_hevy_activity(activity, local_timezone) for activity in activities]


def _normalize_suunto_activities(
    activities: list[dict[str, str]],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[NormalizedActivity]:
    return [normalize_suunto_activity(activity, local_timezone) for activity in activities]


def _pair_hevy_suunto_strength(
    activities: list[NormalizedActivity],
) -> list[NormalizedActivity]:
    hevy_activities = [
        activity
        for activity in activities
        if activity.source == "hevy" and activity.activity_type == "strength"
    ]
    suunto_activities = [
        activity
        for activity in activities
        if activity.source == "suunto" and _is_suunto_strength_activity(activity)
    ]
    hevy_candidates = {
        activity: [
            suunto
            for suunto in suunto_activities
            if _strength_activities_match(activity, suunto)
        ]
        for activity in hevy_activities
    }
    suunto_candidates = {
        activity: [
            hevy
            for hevy in hevy_activities
            if _strength_activities_match(hevy, activity)
        ]
        for activity in suunto_activities
    }
    paired_hevy: set[NormalizedActivity] = set()
    enriched_by_suunto: dict[NormalizedActivity, NormalizedActivity] = {}
    for hevy, candidates in hevy_candidates.items():
        if len(candidates) != 1:
            continue
        suunto = candidates[0]
        if len(suunto_candidates[suunto]) != 1:
            continue
        paired_hevy.add(hevy)
        enriched_by_suunto[suunto] = replace(
            suunto,
            activity_type="strength",
            detail_source="hevy",
            detail_source_id=hevy.source_id,
            detail_name=hevy.name,
        )

    return [
        enriched_by_suunto.get(activity, activity)
        for activity in activities
        if activity not in paired_hevy
    ]


def _is_suunto_strength_activity(activity: NormalizedActivity) -> bool:
    if activity.activity_type == "strength":
        return True
    labels = {
        activity.activity_type.strip().lower(),
        activity.raw_type.strip().lower().replace("_", " "),
        activity.name.strip().lower().replace("_", " "),
    }
    return bool(
        labels
        & {
            "strength",
            "strength training",
            "weight training",
            "weights",
            "gym",
            "outdoor gym",
            "crossfit",
            "kettlebell",
            "calisthenics",
            "indoor",
            "indoor training",
        }
    )


def _strength_activities_match(
    hevy: NormalizedActivity,
    suunto: NormalizedActivity,
) -> bool:
    if _activity_date(hevy.start_time) != _activity_date(suunto.start_time):
        return False
    hevy_start = _activity_timestamp(hevy.start_time)
    suunto_start = _activity_timestamp(suunto.start_time)
    if hevy_start is None or suunto_start is None:
        return False
    hevy_end = _activity_end_timestamp(hevy, hevy_start)
    suunto_end = _activity_end_timestamp(suunto, suunto_start)
    windows_overlap = (
        hevy_end is not None
        and suunto_end is not None
        and hevy_start <= suunto_end
        and suunto_start <= hevy_end
    )
    windows_near = (
        hevy_end is not None
        and suunto_end is not None
        and (
            abs(hevy_start - suunto_end) <= 20 * 60
            or abs(suunto_start - hevy_end) <= 20 * 60
        )
    )
    starts_close = abs(hevy_start - suunto_start) <= 30 * 60
    return windows_overlap or windows_near or starts_close


def _prefer_suunto_activities(
    activities: list[NormalizedActivity],
) -> list[NormalizedActivity]:
    suunto_activities = [activity for activity in activities if activity.source == "suunto"]
    retained_withings: list[NormalizedActivity] = []
    retained: list[NormalizedActivity] = []
    for activity in activities:
        if activity.source != "withings":
            retained.append(activity)
            continue
        if any(_activities_overlap(activity, suunto) for suunto in suunto_activities):
            continue
        if any(_activities_overlap(activity, existing) for existing in retained_withings):
            continue
        retained_withings.append(activity)
        retained.append(activity)
    return retained


def _activities_overlap(
    secondary: NormalizedActivity,
    authoritative: NormalizedActivity,
) -> bool:
    if secondary.activity_type != authoritative.activity_type:
        return False
    if _activity_date(secondary.start_time) != _activity_date(authoritative.start_time):
        return False
    if secondary.distance_km is None or authoritative.distance_km is None:
        return False

    distance_tolerance = max(
        0.15,
        min(secondary.distance_km, authoritative.distance_km) * 0.05,
    )
    if abs(secondary.distance_km - authoritative.distance_km) > distance_tolerance:
        return False

    secondary_start = _activity_timestamp(secondary.start_time)
    authoritative_start = _activity_timestamp(authoritative.start_time)
    if secondary_start is None or authoritative_start is None:
        return False
    secondary_end = _activity_end_timestamp(secondary, secondary_start)
    authoritative_end = _activity_end_timestamp(authoritative, authoritative_start)
    windows_overlap = (
        secondary_end is not None
        and authoritative_end is not None
        and secondary_start <= authoritative_end
        and authoritative_start <= secondary_end
    )
    starts_close = abs(secondary_start - authoritative_start) <= 30 * 60
    return windows_overlap or starts_close


def _activity_timestamp(value: str) -> float | None:
    parsed = _parse_timestamp(value)
    return parsed.timestamp() if parsed is not None else None


def _activity_end_timestamp(
    activity: NormalizedActivity,
    start_timestamp: float,
) -> float | None:
    end_timestamp = _activity_timestamp(activity.end_time)
    if end_timestamp is not None and end_timestamp >= start_timestamp:
        return end_timestamp
    if activity.duration_min > 0:
        return start_timestamp + activity.duration_min * 60
    return None


def _activity_sources(activities: list[NormalizedActivity]) -> str:
    sources = sorted(
        {
            source.capitalize()
            for activity in activities
            for source in [activity.source, activity.detail_source]
            if source
        }
    )
    return ", ".join(sources) if sources else "None"


def _hevy_workout_id(activity: NormalizedActivity) -> str:
    if activity.detail_source == "hevy":
        return activity.detail_source_id
    if activity.source != "suunto":
        return activity.source_id
    return ""


def _float_value(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float_value(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int_value(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
