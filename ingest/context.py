from __future__ import annotations

import csv
import re
import shutil
import textwrap
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ingest.activities import (
    NormalizedActivity,
    normalize_hevy_activity,
    normalize_withings_activity,
)
from ingest.app_data import write_text_file
from ingest.config import AppConfig


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


@dataclass(frozen=True)
class RecoveryMetrics:
    compatibility: str
    fatigue_risk: str
    load_score: float
    drivers: list[str]
    suggested_next_day: list[str]


@dataclass(frozen=True)
class ActivityMetrics:
    label: str
    score: float | None
    avg_7d: float | None
    avg_30d: float | None
    trend: str


@dataclass(frozen=True)
class ActivityEffortTotals:
    non_swim_distance_km: float
    duration_min: float
    activity_count: int


@dataclass(frozen=True)
class EstimatedDeficitMetrics:
    today: float
    avg_7d: float | None
    avg_30d: float | None


WALK_STEPS_PER_KM = 1300
RUN_STEPS_PER_KM = 1200
WALK_MIN_PER_KM = 12
DERIVED_BODY_METRIC_LABELS = {"BMI", "BMR"}


def generate_daily_context(config: AppConfig, target_date: date | None = None) -> Path:
    target = target_date or date.today()
    state = build_daily_state(config, target)
    return write_text_file(config.daily_context_path, _render_daily_state(state))


def generate_today_context(config: AppConfig, target_date: date | None = None) -> Path:
    return generate_daily_context(config, target_date)


def render_daily_terminal_context(state: DailyState, console: Any | None = None) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = console or Console()
    target_date = state.target_date
    activities = state.activities
    withings_steps = _withings_step_count(state.withings_activity_summaries)
    activity_effort_totals = _activity_effort_totals(activities, state.withings_activity_summaries)
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
    strength_activities = [activity for activity in activities if activity.activity_type == "strength"]
    strength_duration_min = sum(activity.duration_min for activity in strength_activities)
    ride_distance_km = sum(
        activity.distance_km or 0.0
        for activity in activities
        if activity.activity_type == "ride"
    )
    activity_metrics = _activity_metrics(
        activities,
        state.historical_activities,
        state.historical_withings_activity_summaries,
        target_date,
        effort_totals=activity_effort_totals,
    )
    recovery_metrics = _recovery_metrics(
        activities,
        state.hevy_sets,
        walking_distance_km=walking_distance_km,
        swimming_duration_min=swimming_duration_min,
    )
    weight_metrics = _weight_metrics(state.historical_measures, target_date)
    estimated_deficit_metrics = _estimated_deficit_metrics(state.historical_measures, target_date)
    walking_metrics = _walking_metrics(state.historical_activities, target_date)
    sources = _activity_sources(activities)

    console.print(Text(f"Physical Context — {target_date.isoformat()}", style="bold"))
    _render_section_title(console, "Daily Snapshot")
    _render_kv_block(
        console,
        [
            ("Activity", _snapshot_activity_status(activity_metrics, separator=" / ")),
            ("Recovery", _snapshot_recovery_status(recovery_metrics, separator=" / ")),
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
            (
                "Strength",
                _snapshot_strength_status(
                    strength_activities,
                    state.hevy_sets,
                    volume_formatter=_format_terminal_volume,
                    separator=" / ",
                ),
            ),
            ("Body", _snapshot_body_status(weight_metrics, separator=" / ")),
        ],
        indent=2,
    )

    _render_section_title(console, "Recovery")
    _render_kv_block(
        console,
        [
            ("Compatibility", recovery_metrics.compatibility),
            ("Fatigue risk", recovery_metrics.fatigue_risk),
            ("Recovery load score", f"{recovery_metrics.load_score:.1f}"),
        ],
        indent=2,
    )
    _render_subsection_title(console, "Primary drivers")
    _render_bullets(console, [_format_terminal_driver(driver) for driver in recovery_metrics.drivers], indent=4)
    _render_subsection_title(console, "Recovery flags")
    _render_bullets(console, recovery_metrics.suggested_next_day, indent=4)

    _render_section_title(console, "Trends")
    trends = Table(box=None, show_edge=False, show_lines=False, expand=False, padding=(0, 2))
    for column in ["  Metric", "Today", "7-day avg", "30-day avg", "Direction"]:
        trends.add_column(column, style="bold white" if column.strip() == "Metric" else "", no_wrap=True)
    trends.add_row(
        _styled_terminal_value("  Activity score", label="Metric"),
        _styled_terminal_value(_format_activity_score(activity_metrics.score)),
        _styled_terminal_value(_format_average_activity_score(activity_metrics.avg_7d)),
        _styled_terminal_value(_format_average_activity_score(activity_metrics.avg_30d)),
        _styled_terminal_value(_terminal_trend_direction(activity_metrics.score, activity_metrics.avg_7d, activity_metrics.avg_30d)),
    )
    trends.add_row(
        _styled_terminal_value("  Walking distance", label="Metric"),
        _styled_terminal_value(f"{walking_distance_km:.2f} km"),
        _styled_terminal_value(walking_metrics["avg_7d"]),
        _styled_terminal_value(walking_metrics["avg_30d"]),
        _styled_terminal_value(
            _terminal_trend_direction(
                walking_distance_km,
                _walking_average_value(walking_metrics["avg_7d"]),
                _walking_average_value(walking_metrics["avg_30d"]),
            )
        ),
    )
    trends.add_row(
        _styled_terminal_value("  Weight", label="Metric"),
        _styled_terminal_value(weight_metrics["current_weight"]),
        _styled_terminal_value(weight_metrics["avg_7d"]),
        _styled_terminal_value(weight_metrics["avg_30d"]),
        _styled_terminal_value(
            _terminal_trend_direction(
                _weight_value(weight_metrics["current_weight"]),
                _weight_value(weight_metrics["avg_7d"]),
                _weight_value(weight_metrics["avg_30d"]),
            )
        ),
    )
    if estimated_deficit_metrics is not None:
        trends.add_row(
            _styled_terminal_value("  Estimated deficit", label="Metric"),
            _styled_terminal_value(_format_estimated_deficit(estimated_deficit_metrics.today)),
            _styled_terminal_value(_format_average_estimated_deficit(estimated_deficit_metrics.avg_7d)),
            _styled_terminal_value(_format_average_estimated_deficit(estimated_deficit_metrics.avg_30d)),
            _styled_terminal_value(
                _terminal_trend_direction(
                    estimated_deficit_metrics.today,
                    estimated_deficit_metrics.avg_7d,
                    estimated_deficit_metrics.avg_30d,
                )
            ),
        )
    console.print(trends)

    body_rows = _terminal_body_kv_rows(state.measures, state.historical_measures, target_date)
    if body_rows:
        _render_section_title(console, "Body")
        _render_kv_block(console, body_rows, indent=2)

    if activities:
        _render_section_title(console, "Activities")
        _render_terminal_activity_sections(console, activities, state.hevy_sets)

    _render_section_title(console, "Data Coverage")
    _render_kv_block(
        console,
        [
            ("Sources", sources),
            ("Activity count", str(len(activities))),
            ("Missing data", _missing_data_summary(withings_steps, state.measures, activities)),
        ],
        indent=2,
    )

    _render_section_title(console, "Machine Handoff")
    _render_wrapped_paragraph(
        console,
        _ai_handoff(
            activities=activities,
            activity_metrics=activity_metrics,
            total_duration_min=logged_duration_min,
            withings_steps_text=withings_steps_text,
            walking_distance_km=walking_distance_km,
            swimming_duration_min=swimming_duration_min,
            strength_count=len(strength_activities),
            strength_duration_min=strength_duration_min,
            recovery_metrics=recovery_metrics,
            walking_metrics=walking_metrics,
            weight_metrics=weight_metrics,
            bmi=_bmi_value(
                _latest_measures_by_type(state.measures).get("weight"),
                _latest_historical_height(state.historical_measures, target_date),
            ),
            bmr=_bmr_value(_latest_measures_by_type(state.measures).get("fat_free_mass")),
            estimated_deficit=_format_average_estimated_deficit(estimated_deficit_metrics.today)
            if estimated_deficit_metrics is not None
            else None,
        ),
    )


def build_daily_state(config: AppConfig, target_date: date) -> DailyState:
    withings_activities = read_withings_activities(config.withings.workouts_csv)
    withings_activities_for_target = withings_activities_for_date(withings_activities, target_date)
    hevy_activities = read_hevy_activities(config.hevy.workouts_csv)
    hevy_activities_for_target = activities_for_date(hevy_activities, target_date)
    hevy_sets = sets_for_date(read_hevy_sets(config.hevy.sets_csv), target_date)
    all_measures = read_withings_measures(config.withings.measures_csv)
    measures = measures_for_date(all_measures, target_date)
    all_withings_activity_summaries = read_withings_activity_summaries(config.withings.activity_csv)
    withings_activity_summaries = withings_activity_summaries_for_date(
        all_withings_activity_summaries,
        target_date,
    )
    return DailyState(
        target_date=target_date,
        activities=[
            *_normalize_withings_activities(withings_activities_for_target),
            *_normalize_hevy_activities(hevy_activities_for_target),
        ],
        measures=measures,
        withings_activity_summaries=withings_activity_summaries,
        historical_withings_activity_summaries=all_withings_activity_summaries,
        historical_activities=[
            *_normalize_withings_activities(withings_activities),
            *_normalize_hevy_activities(hevy_activities),
        ],
        historical_measures=all_measures,
        hevy_sets=hevy_sets,
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


def read_withings_activities(path: Path) -> list[dict[str, str]]:
    return [
        activity
        for activity in _read_activity_rows(path)
        if activity.get("raw_type") != "category_16"
        and activity.get("activity_type") != "category_16"
    ]


def read_hevy_activities(path: Path) -> list[dict[str, str]]:
    return _read_activity_rows(path)


def read_hevy_sets(path: Path) -> list[dict[str, str]]:
    return _read_activity_rows(path)


def _read_activity_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def withings_activities_for_date(activities: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    return activities_for_date(activities, target_date)


def activities_for_date(activities: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [
        activity
        for activity in activities
        if activity.get("start_time", "").startswith(target)
    ]


def sets_for_date(sets: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [set_row for set_row in sets if set_row.get("start_time", "").startswith(target)]


def measures_for_date(measures: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [measure for measure in measures if measure.get("date") == target]


def withings_activity_summaries_for_date(
    activity_summaries: list[dict[str, str]],
    target_date: date,
) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [activity for activity in activity_summaries if activity.get("date") == target]


def render_daily_context(
    target_date: date,
    activities: list[dict[str, str]],
    measures: list[dict[str, str]] | None = None,
    historical_measures: list[dict[str, str]] | None = None,
    historical_activities: list[dict[str, str]] | None = None,
    hevy_sets: list[dict[str, str]] | None = None,
    withings_activity_summaries: list[dict[str, str]] | None = None,
    historical_withings_activity_summaries: list[dict[str, str]] | None = None,
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
    state = DailyState(
        target_date=target_date,
        activities=_normalize_withings_activities(activities),
        measures=measures,
        withings_activity_summaries=withings_activity_summaries,
        historical_withings_activity_summaries=historical_withings_activity_summaries,
        historical_activities=_normalize_withings_activities(historical_activities),
        historical_measures=historical_measures,
        hevy_sets=hevy_sets or [],
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
    activity_effort_totals = _activity_effort_totals(
        primary_today_activities,
        state.withings_activity_summaries,
    )
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
    strength_activities = [
        activity
        for activity in primary_today_activities
        if activity.activity_type == "strength"
    ]
    strength_duration_min = sum(activity.duration_min for activity in strength_activities)
    activity_metrics = _activity_metrics(
        primary_today_activities,
        historical_normalized_activities,
        state.historical_withings_activity_summaries,
        target_date,
        effort_totals=activity_effort_totals,
    )
    recovery_metrics = _recovery_metrics(
        primary_today_activities,
        state.hevy_sets,
        walking_distance_km=walking_distance_km,
        swimming_duration_min=swimming_duration_min,
    )
    weight_metrics = _weight_metrics(state.historical_measures, target_date)
    estimated_deficit_metrics = _estimated_deficit_metrics(state.historical_measures, target_date)
    walking_metrics = _walking_metrics(historical_normalized_activities, target_date)
    sources = _activity_sources(primary_today_activities)
    ride_distance_km = sum(
        activity.distance_km or 0.0
        for activity in primary_today_activities
        if activity.activity_type == "ride"
    )
    body_rows = _body_rows(measures, state.historical_measures, target_date)

    lines = [
        f"# Physical Context - {target_date.isoformat()}",
        "",
        "## Daily Snapshot",
        "",
        "| Area | Status |",
        "| --- | --- |",
        f"| Activity | {_snapshot_activity_status(activity_metrics)} |",
        f"| Recovery | {_snapshot_recovery_status(recovery_metrics)} |",
        f"| Movement | {_snapshot_movement_status(withings_steps, walking_distance_km, ride_distance_km, swimming_duration_min)} |",
        f"| Strength | {_snapshot_strength_status(strength_activities, state.hevy_sets)} |",
        f"| Body | {_snapshot_body_status(weight_metrics)} |",
        "",
        "## Recovery",
        "",
        f"- Compatibility: {recovery_metrics.compatibility}",
        f"- Fatigue risk: {recovery_metrics.fatigue_risk}",
        f"- Recovery load score: {recovery_metrics.load_score:.1f}",
        "- Primary drivers:",
        *[f"  - {driver}" for driver in recovery_metrics.drivers],
        "- Recovery Flags:",
        *[f"  - {suggestion}" for suggestion in recovery_metrics.suggested_next_day],
        "",
        "## Trends",
        "",
        "| Metric | Today | 7-day avg | 30-day avg | Direction |",
        "| --- | --- | --- | --- | --- |",
        (
            "| Activity score | "
            f"{_format_activity_score(activity_metrics.score)} | "
            f"{_format_average_activity_score(activity_metrics.avg_7d)} | "
            f"{_format_average_activity_score(activity_metrics.avg_30d)} | "
            f"{_trend_direction(activity_metrics.trend, activity_metrics.score, activity_metrics.avg_30d)} |"
        ),
        (
            "| Walking distance | "
            f"{walking_distance_km:.2f} km | "
            f"{walking_metrics['avg_7d']} | "
            f"{walking_metrics['avg_30d']} | "
            f"{_trend_direction(walking_metrics['trend'], walking_distance_km, _walking_average_value(walking_metrics['avg_30d']))} |"
        ),
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
            f"- Sources: {sources}",
            f"- Activity count: {len(primary_today_activities)}",
            f"- Missing or partial data: {_missing_data_summary(withings_steps, measures, primary_today_activities)}",
            "",
            "## Machine Handoff",
            "",
            _ai_handoff(
                activities=primary_today_activities,
                activity_metrics=activity_metrics,
                total_duration_min=logged_duration_min,
                withings_steps_text=withings_steps_text,
                walking_distance_km=walking_distance_km,
                swimming_duration_min=swimming_duration_min,
                strength_count=len(strength_activities),
                strength_duration_min=strength_duration_min,
                recovery_metrics=recovery_metrics,
                walking_metrics=walking_metrics,
                weight_metrics=weight_metrics,
                bmi=_bmi_value(
                    _latest_measures_by_type(measures).get("weight"),
                    _latest_historical_height(state.historical_measures, target_date),
                ),
                bmr=_bmr_value(_latest_measures_by_type(measures).get("fat_free_mass")),
                estimated_deficit=_format_average_estimated_deficit(estimated_deficit_metrics.today)
                if estimated_deficit_metrics is not None
                else None,
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _snapshot_activity_status(activity_metrics: ActivityMetrics, *, separator: str = " · ") -> str:
    return (
        f"{activity_metrics.label}{separator}score {_format_activity_score(activity_metrics.score)}"
        f"{separator}trend {activity_metrics.trend.lower()}"
    )


def _snapshot_recovery_status(recovery_metrics: RecoveryMetrics, *, separator: str = " · ") -> str:
    return (
        f"{recovery_metrics.compatibility}{separator}fatigue risk {recovery_metrics.fatigue_risk.lower()}"
        f"{separator}load {recovery_metrics.load_score:.1f}"
    )


def _snapshot_movement_status(
    withings_steps: int | None,
    walking_distance_km: float,
    ride_distance_km: float,
    swimming_duration_min: float,
    *,
    formatted_steps: str | None = None,
    separator: str = " · ",
) -> str:
    parts = [f"{formatted_steps or _format_step_count(withings_steps)} steps"]
    if walking_distance_km > 0:
        parts.append(f"{walking_distance_km:.2f} km walk")
    if ride_distance_km > 0:
        parts.append(f"{ride_distance_km:.2f} km ride")
    if swimming_duration_min > 0:
        parts.append(f"{swimming_duration_min:.0f} min swim")
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


def _render_section_title(console: Any, title: str) -> None:
    console.print()
    console.print(title, style="bold cyan")


def _render_subsection_title(console: Any, title: str) -> None:
    console.print()
    console.print(f"  {title}", style="bold")


def _render_kv_block(console: Any, rows: list[tuple[str, str]], *, indent: int = 0) -> None:
    if not rows:
        return
    label_width = max(len(label) for label, _ in rows)
    prefix = " " * indent
    for label, value in rows:
        value_width = max(console.width - indent - label_width - 2, 20)
        wrapped_value = textwrap.fill(str(value), width=value_width).splitlines() or [""]
        line = _styled_terminal_line(f"{prefix}{label:<{label_width}}  ", wrapped_value[0], label=label)
        console.print(line)
        continuation_prefix = f"{prefix}{'':<{label_width}}  "
        for line in wrapped_value[1:]:
            console.print(_styled_terminal_line(continuation_prefix, line, label=label))


def _render_bullets(console: Any, items: list[str], *, indent: int = 0) -> None:
    prefix = " " * indent
    for item in items:
        console.print(_styled_terminal_line(f"{prefix}• ", item))


def _styled_terminal_line(prefix: str, value: str, *, label: str = "") -> Any:
    from rich.text import Text

    text = Text(prefix, style="bold white")
    text.append(_styled_terminal_value(value, label=label))
    return text


def _styled_terminal_value(value: str, *, label: str = "") -> Any:
    from rich.text import Text

    text = Text(str(value))
    plain = text.plain
    if plain == "None":
        text.stylize("dim italic")
        return text
    if label == "Metric":
        text.stylize("bold white")
        return text

    _stylize_matches(text, r"\b\d[\d,]*(?:\.\d+)?(?:%| kg| km| min| steps|/day)?\b", "cyan")
    _stylize_matches(text, r"\([+-]?\d+%\)", "cyan")
    _stylize_matches(text, r"\b(Good|Low)\b", "green")
    _stylize_matches(text, r"\bPoor\b", "bold red")
    _stylize_matches(text, r"\bNear \d+d avg\b", "green")
    _stylize_matches(text, r"\b(Above|Below) \d+d avg\b", "yellow")
    _stylize_matches(text, r"\bNo baseline\b", "dim")
    if label == "Fatigue risk" or label == "Recovery":
        _stylize_matches(text, r"\b[Hh]igh\b", "yellow")
    return text


def _stylize_matches(text: Any, pattern: str, style: str) -> None:
    for match in re.finditer(pattern, text.plain):
        text.stylize(style, match.start(), match.end())


def _render_wrapped_paragraph(console: Any, text: str, *, indent: int = 2, max_width: int = 88) -> None:
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
    console.print(wrapped, overflow="fold", crop=False, soft_wrap=False)


def _render_terminal_activity_sections(
    console: Any,
    activities: list[NormalizedActivity],
    hevy_sets: list[dict[str, str]],
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
        _render_subsection_title(console, "Walking")
        _render_lines(console, [_terminal_distance_activity(activity) for activity in walking_activities], indent=4)

    if swimming_activities:
        _render_subsection_title(console, "Swimming")
        _render_lines(console, [_terminal_duration_activity(activity) for activity in swimming_activities], indent=4)

    if workout_activities:
        _render_subsection_title(console, "Workout")
        for activity in workout_activities:
            console.print(_terminal_workout_header(activity))
            workout_sets = [
                set_row
                for set_row in hevy_sets
                if set_row.get("workout_source_id") == activity.source_id
            ]
            if workout_sets:
                total_volume_kg = sum(_float_value(set_row.get("volume_kg", "")) for set_row in workout_sets)
                _render_kv_block(
                    console,
                    [("Sets", str(len(workout_sets))), ("Volume", _format_terminal_volume(total_volume_kg))],
                    indent=4,
                )
                _render_lines(console, _terminal_exercise_summaries(workout_sets), indent=4)

    if other_activities:
        _render_subsection_title(console, "Other")
        _render_lines(console, [_terminal_distance_activity(activity) for activity in other_activities], indent=4)


def _render_lines(console: Any, items: list[Any], *, indent: int = 0) -> None:
    prefix = " " * indent
    for item in items:
        if hasattr(item, "plain"):
            from rich.text import Text

            line = Text(prefix)
            line.append(item)
            console.print(line)
        else:
            console.print(_styled_terminal_line(prefix, str(item)))


def _terminal_distance_activity(activity: NormalizedActivity) -> Any:
    from rich.text import Text

    text = Text(activity.raw_type or "Unknown", style="bold")
    text.append("  ")
    text.append(_terminal_activity_source(activity), style="dim")
    text.append(" / ")
    text.append(_format_distance(activity.distance_km), style="cyan")
    text.append(" / ")
    text.append(f"{activity.duration_min:.0f} min", style="cyan")
    return text


def _terminal_duration_activity(activity: NormalizedActivity) -> Any:
    from rich.text import Text

    text = Text(activity.raw_type or "Unknown", style="bold")
    text.append("  ")
    text.append(_terminal_activity_source(activity), style="dim")
    text.append(" / ")
    text.append(f"{activity.duration_min:.0f} min", style="cyan")
    return text


def _terminal_workout_header(activity: NormalizedActivity) -> Any:
    from rich.text import Text

    text = Text("    ")
    text.append(_display_activity_name(activity), style="bold")
    text.append(" / ")
    text.append(f"{activity.duration_min:.0f} min", style="cyan")
    return text


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


def _format_terminal_driver(value: str) -> str:
    return re.sub(r"\b(\d{4,})(?= kg\b)", lambda match: f"{int(match.group(1)):,}", value)


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


def _walking_average_value(value: str) -> float | None:
    if value == "Unknown":
        return None
    return _float_value(value.split(" ", 1)[0])


def _weight_value(value: str) -> float | None:
    if value in {"Unknown", "No Withings weight available"}:
        return None
    return _float_value(value.split(" ", 1)[0])


def _body_rows(
    measures: list[dict[str, str]],
    historical_measures: list[dict[str, str]],
    target_date: date,
) -> list[str]:
    rows = []
    in_derived_metrics = False
    for label, value in _body_kv_rows(measures, historical_measures, target_date):
        if label in DERIVED_BODY_METRIC_LABELS and not in_derived_metrics:
            rows.append("| ----- |  |")
            in_derived_metrics = True
        rows.append(f"| {label} | {value} |")
    return rows


def _terminal_body_kv_rows(
    measures: list[dict[str, str]],
    historical_measures: list[dict[str, str]],
    target_date: date,
) -> list[tuple[str, str]]:
    terminal_rows = []
    in_derived_metrics = False
    for label, value in _body_kv_rows(measures, historical_measures, target_date):
        if label in DERIVED_BODY_METRIC_LABELS and not in_derived_metrics:
            terminal_rows.append(("-----", ""))
            in_derived_metrics = True
        terminal_rows.append((label, value.replace(" · ", " / ")))
    return terminal_rows


def _body_kv_rows(
    measures: list[dict[str, str]],
    historical_measures: list[dict[str, str]],
    target_date: date,
) -> list[tuple[str, str]]:
    if not measures:
        return []

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
        ("Weight", _measure_value(latest_by_type.get("weight"))),
        ("Body fat", _body_fat_value(latest_by_type)),
        ("Muscle mass", _measure_value(latest_by_type.get("muscle_mass"))),
        ("Hydration", _measure_value(latest_by_type.get("hydration"))),
        ("Fat-free mass", _measure_value(latest_by_type.get("fat_free_mass"))),
        ("Bone mass", _measure_value(latest_by_type.get("bone_mass"))),
    ]
    for type_name, measure in latest_by_type.items():
        if type_name not in main_types:
            rows.append((type_name or "measurement", _measure_value(measure)))
    bmi = _bmi_value(latest_by_type.get("weight"), _latest_historical_height(historical_measures, target_date))
    bmr = _bmr_value(latest_by_type.get("fat_free_mass"))
    if bmi is not None:
        rows.append(("BMI", bmi))
    if bmr is not None:
        rows.append(("BMR", bmr))
    return rows


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


def _missing_data_summary(
    withings_steps: int | None,
    measures: list[dict[str, str]],
    activities: list[NormalizedActivity],
) -> str:
    missing: list[str] = []
    if withings_steps is None:
        missing.append("Withings steps unavailable")
    if not measures:
        missing.append("body measures unavailable")
    if not activities:
        missing.append("no activities logged")
    if not missing:
        return "None"
    return "; ".join(missing)


def _activity_level(total_distance_km: float, total_duration_min: float, activity_count: int) -> str:
    if activity_count == 0:
        return "None"
    if total_distance_km <= 5 and total_duration_min <= 60:
        return "Light"
    if total_distance_km <= 12 and total_duration_min <= 120:
        return "Moderate"
    return "High"


def _activity_effort_totals(
    activities: list[NormalizedActivity],
    withings_activity_summaries: list[dict[str, str]],
) -> ActivityEffortTotals:
    logged_non_swim_distance_km = sum(
        activity.distance_km or 0.0
        for activity in activities
        if activity.activity_type != "swim"
    )
    logged_duration_min = sum(activity.duration_min for activity in activities)
    unlogged_steps = _unlogged_step_count(activities, withings_activity_summaries)
    unlogged_step_distance_km = unlogged_steps / WALK_STEPS_PER_KM
    unlogged_step_duration_min = unlogged_step_distance_km * WALK_MIN_PER_KM
    return ActivityEffortTotals(
        non_swim_distance_km=logged_non_swim_distance_km + unlogged_step_distance_km,
        duration_min=logged_duration_min + unlogged_step_duration_min,
        activity_count=len(activities) + (1 if unlogged_steps > 0 else 0),
    )


def _unlogged_step_count(
    activities: list[NormalizedActivity],
    withings_activity_summaries: list[dict[str, str]],
) -> int:
    total_steps = _withings_step_count(withings_activity_summaries)
    if total_steps is None:
        return 0
    logged_steps = sum(_logged_step_equivalent(activity) for activity in activities)
    return max(0, total_steps - logged_steps)


def _logged_step_equivalent(activity: NormalizedActivity) -> int:
    if activity.activity_type not in {"walk", "run"}:
        return 0
    if activity.step_count > 0:
        return activity.step_count
    if activity.distance_km is None:
        return 0
    steps_per_km = RUN_STEPS_PER_KM if activity.activity_type == "run" else WALK_STEPS_PER_KM
    return round(activity.distance_km * steps_per_km)


def _activity_metrics(
    activities: list[NormalizedActivity],
    historical_activities: list[NormalizedActivity],
    historical_withings_activity_summaries: list[dict[str, str]],
    target_date: date,
    *,
    effort_totals: ActivityEffortTotals,
) -> ActivityMetrics:
    current_7d = _average_daily_activity_score(
        historical_activities,
        historical_withings_activity_summaries,
        target_date,
        days=7,
    )
    previous_7d = _average_daily_activity_score(
        historical_activities,
        historical_withings_activity_summaries,
        target_date - _date_delta(7),
        days=7,
    )
    avg_30d = _average_daily_activity_score(
        historical_activities,
        historical_withings_activity_summaries,
        target_date,
        days=30,
    )
    return ActivityMetrics(
        label=_activity_level(
            effort_totals.non_swim_distance_km,
            effort_totals.duration_min,
            effort_totals.activity_count,
        ),
        score=_activity_score_for_totals(effort_totals),
        avg_7d=current_7d,
        avg_30d=avg_30d,
        trend=_activity_score_trend(current_7d, previous_7d),
    )


def _activity_score_for_totals(effort_totals: ActivityEffortTotals) -> float | None:
    if effort_totals.activity_count == 0:
        return None
    return effort_totals.non_swim_distance_km + (effort_totals.duration_min / 12)


def _average_daily_activity_score(
    activities: list[NormalizedActivity],
    withings_activity_summaries: list[dict[str, str]],
    end_date: date,
    *,
    days: int,
) -> float | None:
    start_date = end_date - _date_delta(days - 1)
    activities_in_window = [
        activity
        for activity in activities
        if (activity_date := _activity_date(activity.start_time)) is not None
        and start_date <= activity_date <= end_date
    ]
    summaries_in_window = [
        summary
        for summary in withings_activity_summaries
        if (summary_date := _summary_date(summary)) is not None
        and start_date <= summary_date <= end_date
    ]
    if not activities_in_window and not summaries_in_window:
        return None

    total_score = 0.0
    for offset in range(days):
        score_date = start_date + _date_delta(offset)
        score = _activity_score_for_totals(
            _activity_effort_totals(
                [
                    activity
                    for activity in activities_in_window
                    if _activity_date(activity.start_time) == score_date
                ],
                [
                    summary
                    for summary in summaries_in_window
                    if _summary_date(summary) == score_date
                ],
            )
        )
        total_score += score or 0.0
    return total_score / days


def _format_activity_score(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.1f}"


def _format_average_activity_score(value: float | None) -> str:
    if value is None:
        return "Unknown"
    return f"{value:.1f}/day"


def _activity_score_trend(current_7d: float | None, previous_7d: float | None) -> str:
    if current_7d is None or previous_7d is None:
        return "Unknown"

    difference = current_7d - previous_7d
    if difference <= -1:
        return "Decreasing"
    if difference >= 1:
        return "Increasing"
    return "Stable"


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
    return str(value)


def _recovery_metrics(
    activities: list[NormalizedActivity],
    hevy_sets: list[dict[str, str]],
    *,
    walking_distance_km: float,
    swimming_duration_min: float,
) -> RecoveryMetrics:
    strength_activities = [activity for activity in activities if activity.activity_type == "strength"]
    strength_duration_min = sum(activity.duration_min for activity in strength_activities)
    ride_distance_km = sum(
        activity.distance_km or 0.0
        for activity in activities
        if activity.activity_type == "ride"
    )
    run_distance_km = sum(
        activity.distance_km or 0.0
        for activity in activities
        if activity.activity_type == "run"
    )
    other_distance_km = sum(
        activity.distance_km or 0.0
        for activity in activities
        if activity.activity_type not in {"walk", "swim", "strength", "ride", "run"}
    )
    strength_sets = _sets_for_strength_activities(strength_activities, hevy_sets)
    total_sets = len(strength_sets)
    total_volume_kg = sum(_float_value(set_row.get("volume_kg", "")) for set_row in strength_sets)
    exercise_count = len({set_row.get("exercise", "") for set_row in strength_sets if set_row.get("exercise")})
    full_body = any(_is_full_body_workout(activity) for activity in strength_activities)
    subjective_all_out = _has_subjective_all_out(strength_activities, strength_sets)

    walking_load = walking_distance_km * 1.0
    swimming_load = swimming_duration_min * 0.12
    strength_load = (strength_duration_min * 0.12) + (total_sets * 0.18) + (total_volume_kg / 8000)
    ride_load = ride_distance_km * 0.35
    run_load = run_distance_km * 1.25
    other_load = other_distance_km * 0.80
    total_load = walking_load + swimming_load + strength_load + ride_load + run_load + other_load

    active_activity_types = sum(
        [
            walking_distance_km > 0,
            swimming_duration_min > 0,
            strength_duration_min > 0,
            ride_distance_km > 0,
            run_distance_km > 0,
            other_distance_km > 0,
        ]
    )
    if active_activity_types >= 3:
        total_load *= 1.30
    elif active_activity_types >= 2:
        total_load *= 1.15

    compatibility = _recovery_label(total_load)
    if full_body and (total_sets >= 60 or strength_duration_min >= 90):
        compatibility = _downgrade_recovery_label(compatibility)
    if subjective_all_out:
        compatibility = _downgrade_recovery_label(compatibility)
    fatigue_risk = _fatigue_risk(compatibility, subjective_all_out)

    return RecoveryMetrics(
        compatibility=compatibility,
        fatigue_risk=fatigue_risk,
        load_score=total_load,
        drivers=_recovery_drivers(
            walking_distance_km=walking_distance_km,
            swimming_duration_min=swimming_duration_min,
            ride_distance_km=ride_distance_km,
            run_distance_km=run_distance_km,
            other_distance_km=other_distance_km,
            strength_duration_min=strength_duration_min,
            strength_workout_count=len(strength_activities),
            total_sets=total_sets,
            total_volume_kg=total_volume_kg,
            exercise_count=exercise_count,
            full_body=full_body,
            mixed_activity=active_activity_types >= 2,
            subjective_all_out=subjective_all_out,
            strength_details_missing=bool(strength_activities) and not strength_sets,
        ),
        suggested_next_day=_recovery_suggestions(
            compatibility,
            strength_duration_min > 0,
            walking_distance_km > 0,
            active_activity_types >= 2,
        ),
    )


def _sets_for_strength_activities(
    strength_activities: list[NormalizedActivity],
    hevy_sets: list[dict[str, str]],
) -> list[dict[str, str]]:
    workout_ids = {activity.source_id for activity in strength_activities}
    return [
        set_row
        for set_row in hevy_sets
        if set_row.get("workout_source_id") in workout_ids
    ]


def _recovery_label(load_score: float) -> str:
    if load_score < 8:
        return "Good"
    if load_score <= 12:
        return "Acceptable"
    if load_score <= 25:
        return "Caution"
    return "Poor"


def _downgrade_recovery_label(label: str) -> str:
    order = ["Good", "Acceptable", "Caution", "Poor"]
    index = order.index(label)
    return order[min(index + 1, len(order) - 1)]


def _fatigue_risk(compatibility: str, subjective_all_out: bool) -> str:
    if compatibility == "Poor" or subjective_all_out:
        return "High"
    if compatibility == "Caution":
        return "Moderate"
    if compatibility == "Acceptable":
        return "Low"
    return "Low"


def _recovery_drivers(
    *,
    walking_distance_km: float,
    swimming_duration_min: float,
    ride_distance_km: float,
    run_distance_km: float,
    other_distance_km: float,
    strength_duration_min: float,
    strength_workout_count: int,
    total_sets: int,
    total_volume_kg: float,
    exercise_count: int,
    full_body: bool,
    mixed_activity: bool,
    subjective_all_out: bool,
    strength_details_missing: bool,
) -> list[str]:
    drivers: list[str] = []
    if strength_duration_min > 0:
        strength_parts = [f"{strength_duration_min:.0f} min"]
        if total_sets > 0:
            strength_parts.append(f"{total_sets} sets")
        if exercise_count > 0:
            strength_parts.append(f"{exercise_count} exercises")
        if total_volume_kg > 0:
            strength_parts.append(_format_volume(total_volume_kg))
        label = "Full-body strength" if full_body else "Strength"
        drivers.append(f"{label}: {', '.join(strength_parts)}")
    movement_parts: list[str] = []
    if walking_distance_km > 0:
        movement_parts.append(f"{walking_distance_km:.2f} km walking")
    if ride_distance_km > 0:
        movement_parts.append(f"{ride_distance_km:.2f} km cycling")
    if run_distance_km > 0:
        movement_parts.append(f"{run_distance_km:.2f} km running")
    if swimming_duration_min > 0:
        movement_parts.append(f"{swimming_duration_min:.0f} min swimming")
    if other_distance_km > 0:
        movement_parts.append(f"{other_distance_km:.2f} km other activity")
    if movement_parts:
        drivers.append(f"Additional movement: {' + '.join(movement_parts)}")
    if mixed_activity:
        drivers.append(f"Mixed load day: {_mixed_activity_driver(walking_distance_km, swimming_duration_min, strength_workout_count, ride_distance_km, run_distance_km, other_distance_km)}")
    if subjective_all_out:
        drivers.append("Subjective all-out effort noted")
    if strength_details_missing:
        drivers.append("Strength details unavailable; score uses duration only")
    if not drivers:
        drivers.append("No activity load recorded")
    return drivers[:5]


def _mixed_activity_driver(
    walking_distance_km: float,
    swimming_duration_min: float,
    strength_workout_count: int,
    ride_distance_km: float,
    run_distance_km: float,
    other_distance_km: float,
) -> str:
    activity_types: list[str] = []
    if walking_distance_km > 0:
        activity_types.append("walking")
    if swimming_duration_min > 0:
        activity_types.append("swimming")
    if strength_workout_count > 0:
        activity_types.append("strength")
    if ride_distance_km > 0:
        activity_types.append("cycling")
    if run_distance_km > 0:
        activity_types.append("running")
    if other_distance_km > 0:
        activity_types.append("other")
    return " + ".join(activity_types)


def _recovery_suggestions(
    compatibility: str,
    strength_training: bool,
    walking_today: bool,
    mixed_activity: bool,
) -> list[str]:
    if compatibility == "Poor":
        load_reason = (
            "Recovery load is elevated due to strength volume plus additional movement"
            if mixed_activity
            else "Recovery load is elevated due to strength volume"
        )
        suggestions = [
            "Avoid stacking another high-volume strength day",
            _light_movement_suggestion(walking_today),
            load_reason,
        ]
    elif compatibility == "Caution":
        suggestions = [
            "Avoid stacking hard sessions",
            _light_movement_suggestion(walking_today),
            "Recovery load is elevated relative to baseline",
        ]
    elif compatibility == "Acceptable":
        suggestions = ["Keep next session moderate", "Watch soreness and sleep quality"]
    else:
        suggestions = ["Normal activity is compatible with current load"]
    if strength_training and "Strength load present in current day" not in suggestions:
        suggestions.append("Strength load present in current day")
    return suggestions


def _light_movement_suggestion(walking_today: bool) -> str:
    if walking_today:
        return "Treat long walking as optional if fatigue remains high"
    return "Keep low-impact movement light if fatigue remains high"


def _is_full_body_workout(activity: NormalizedActivity) -> bool:
    name = _display_activity_name(activity).lower()
    return "full body" in name or "full-body" in name or "全身" in name


def _has_subjective_all_out(
    strength_activities: list[NormalizedActivity],
    strength_sets: list[dict[str, str]],
) -> bool:
    terms = [
        "all-out",
        "all out",
        "failure",
        "max effort",
        "hard",
        "exhausted",
        "限界",
        "オールアウト",
        "追い込んだ",
        "きつい",
    ]
    text = " ".join(
        [
            *(activity.name for activity in strength_activities),
            *(activity.notes for activity in strength_activities),
            *(set_row.get("notes", "") for set_row in strength_sets),
            *(set_row.get("workout_name", "") for set_row in strength_sets),
        ]
    ).lower()
    if any(term in text for term in terms):
        return True
    return any(_float_value(set_row.get("rpe", "")) >= 9.5 for set_row in strength_sets)


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


def _walking_metrics(activities: list[NormalizedActivity], target_date: date) -> dict[str, str]:
    current_7d = _average_daily_walking_distance(activities, target_date, days=7)
    previous_7d = _average_daily_walking_distance(activities, target_date - _date_delta(7), days=7)
    avg_30d = _average_daily_walking_distance(activities, target_date, days=30)
    return {
        "avg_7d": _format_average_distance(current_7d),
        "avg_30d": _format_average_distance(avg_30d),
        "trend": _distance_trend(current_7d, previous_7d),
    }


def _average_daily_walking_distance(
    activities: list[NormalizedActivity],
    end_date: date,
    *,
    days: int,
) -> float | None:
    start_date = end_date - _date_delta(days - 1)
    activities_in_window = [
        activity
        for activity in activities
        if (activity_date := _activity_date(activity.start_time)) is not None
        and start_date <= activity_date <= end_date
    ]
    if not activities_in_window:
        return None

    total_walking_distance = sum(
        activity.distance_km or 0.0
        for activity in activities_in_window
        if _is_walking_activity(activity)
    )
    return total_walking_distance / days


def _format_average_distance(value: float | None) -> str:
    if value is None:
        return "Unknown"
    return f"{value:.2f} km/day"


def _distance_trend(current_7d: float | None, previous_7d: float | None) -> str:
    if current_7d is None or previous_7d is None:
        return "Unknown"

    difference = current_7d - previous_7d
    if difference <= -0.5:
        return "Decreasing"
    if difference >= 0.5:
        return "Increasing"
    return "Stable"


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
    try:
        return date.fromisoformat(raw_value[:10])
    except ValueError:
        return None


def _summary_date(summary: dict[str, str]) -> date | None:
    try:
        return date.fromisoformat(summary.get("date", ""))
    except ValueError:
        return None


def _date_delta(days: int) -> timedelta:
    return timedelta(days=days)


def _ai_handoff(
    *,
    activities: list[NormalizedActivity],
    activity_metrics: ActivityMetrics,
    total_duration_min: float,
    withings_steps_text: str,
    walking_distance_km: float,
    swimming_duration_min: float,
    strength_count: int,
    strength_duration_min: float,
    recovery_metrics: RecoveryMetrics,
    walking_metrics: dict[str, str],
    weight_metrics: dict[str, str],
    bmi: str | None,
    bmr: str | None,
    estimated_deficit: str | None,
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
            f"{activity_metrics.label} activity day with {len(activities)} {_pluralize(len(activities), 'primary activity', 'primary activities')}"
            f"{walking_part}, {total_duration_min:.0f} min moving time, "
            f"and {withings_steps_text} Withings steps."
        )
    score_sentence = (
        f" Activity score is {_format_activity_score(activity_metrics.score)}; "
        f"activity trend is {activity_metrics.trend}."
    )
    swimming_sentence = (
        f" Swimming included {swimming_duration_min:.0f} min."
        if swimming_duration_min > 0
        else ""
    )
    strength_sentence = (
        f" Strength training included {strength_count} {_pluralize(strength_count, 'workout', 'workouts')} and {strength_duration_min:.0f} min."
        if strength_count > 0
        else ""
    )
    recovery_sentence = (
        f" Recovery compatibility is {recovery_metrics.compatibility}; "
        f"fatigue risk is {recovery_metrics.fatigue_risk}; "
        f"load score is {recovery_metrics.load_score:.1f}."
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
    return (
        f"{activity_sentence}{score_sentence}{swimming_sentence}{strength_sentence}{recovery_sentence} "
        f"{_walking_handoff_sentence(walking_distance_km, walking_metrics)}"
        f"{' '.join(body_sentences)}"
    )


def _pluralize(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _walking_handoff_sentence(walking_distance_km: float, walking_metrics: dict[str, str]) -> str:
    if walking_distance_km <= 0:
        return ""
    return f"Walking trend is {walking_metrics['trend']}. "


def _is_walking_activity(activity: NormalizedActivity) -> bool:
    return activity.activity_type == "walk"


def _format_distance(value: float | None) -> str:
    if value is None:
        return "unknown distance"
    return f"{value:.2f} km"


def _display_activity_name(activity: NormalizedActivity) -> str:
    name = activity.name or f"{activity.source}:{activity.source_id}"
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
        lines.append(
            "- "
            f"{activity.raw_type or 'Unknown'}: "
            f"{_display_activity_name(activity)} "
            f"({_format_distance(activity.distance_km)}, "
            f"{activity.duration_min:.0f} min)"
        )
    return lines


def _render_duration_activities(activities: list[NormalizedActivity]) -> list[str]:
    lines: list[str] = []
    for activity in activities:
        lines.append(
            "- "
            f"{activity.raw_type or 'Unknown'}: "
            f"{_display_activity_name(activity)} "
            f"({activity.duration_min:.0f} min)"
        )
    return lines


def _render_workout_activities(activities: list[NormalizedActivity], hevy_sets: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for activity in activities:
        workout_sets = [
            set_row
            for set_row in hevy_sets
            if set_row.get("workout_source_id") == activity.source_id
        ]
        total_sets = len(workout_sets)
        total_volume_kg = sum(_float_value(set_row.get("volume_kg", "")) for set_row in workout_sets)
        lines.append(f"- {_display_activity_name(activity)}: {activity.duration_min:.0f} min")
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


def _normalize_withings_activities(activities: list[dict[str, str]]) -> list[NormalizedActivity]:
    return [normalize_withings_activity(activity) for activity in activities]


def _normalize_hevy_activities(activities: list[dict[str, str]]) -> list[NormalizedActivity]:
    return [normalize_hevy_activity(activity) for activity in activities]


def _activity_sources(activities: list[NormalizedActivity]) -> str:
    sources = sorted({activity.source.capitalize() for activity in activities})
    return ", ".join(sources) if sources else "None"


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
