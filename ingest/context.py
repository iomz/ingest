from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

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


def generate_daily_context(config: AppConfig, target_date: date | None = None) -> Path:
    target = target_date or date.today()
    state = build_daily_state(config, target)
    return write_text_file(config.daily_context_path, _render_daily_state(state))


def generate_today_context(config: AppConfig, target_date: date | None = None) -> Path:
    return generate_daily_context(config, target_date)


def build_daily_state(config: AppConfig, target_date: date) -> DailyState:
    withings_activities = read_withings_activities(config.withings.workouts_csv)
    withings_activities_for_target = withings_activities_for_date(withings_activities, target_date)
    hevy_activities = read_hevy_activities(config.hevy.workouts_csv)
    hevy_activities_for_target = activities_for_date(hevy_activities, target_date)
    hevy_sets = sets_for_date(read_hevy_sets(config.hevy.sets_csv), target_date)
    all_measures = read_withings_measures(config.withings.measures_csv)
    measures = measures_for_date(all_measures, target_date)
    withings_activity_summaries = withings_activity_summaries_for_date(
        read_withings_activity_summaries(config.withings.activity_csv),
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
) -> str:
    measures = measures or []
    historical_measures = historical_measures if historical_measures is not None else measures
    historical_activities = historical_activities if historical_activities is not None else activities
    state = DailyState(
        target_date=target_date,
        activities=_normalize_withings_activities(activities),
        measures=measures,
        withings_activity_summaries=withings_activity_summaries or [],
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
    total_distance_km = sum(
        activity.distance_km or 0.0
        for activity in primary_today_activities
        if activity.activity_type != "swim"
    )
    total_duration_min = sum(activity.duration_min for activity in primary_today_activities)
    withings_steps = _withings_step_count(state.withings_activity_summaries)
    withings_steps_text = _format_step_count(withings_steps)
    walking_distance_km = sum(
        activity.distance_km or 0.0
        for activity in primary_today_activities
        if _is_walking_activity(activity)
    )
    walking_duration_min = sum(
        activity.duration_min
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
        target_date,
        total_distance_km=total_distance_km,
        total_duration_min=total_duration_min,
    )
    recovery_metrics = _recovery_metrics(
        primary_today_activities,
        state.hevy_sets,
        walking_distance_km=walking_distance_km,
        swimming_duration_min=swimming_duration_min,
    )
    weight_metrics = _weight_metrics(state.historical_measures, target_date)
    walking_metrics = _walking_metrics(historical_normalized_activities, target_date)
    sources = _activity_sources(primary_today_activities)

    lines = [
        f"# Physical Context - {target_date.isoformat()}",
        "",
        "## Summary",
        "",
        f"- Activity level: {activity_metrics.label}",
        f"- Activity score: {_format_activity_score(activity_metrics.score)}",
        f"- Activity trend: {activity_metrics.trend}",
        f"- Recovery compatibility: {recovery_metrics.compatibility}",
        f"- Withings steps: {withings_steps_text}",
    ]

    if walking_distance_km > 0 or walking_duration_min > 0:
        lines.append(f"- Walking: {walking_distance_km:.2f} km / {walking_duration_min:.0f} min")
        lines.append(f"- Walking trend: {walking_metrics['trend']}")

    lines.extend(
        [
            f"- Current weight: {weight_metrics['current_weight']}",
            f"- Weight trend: {weight_metrics['trend']}",
        ]
    )

    if swimming_duration_min > 0:
        lines.append(f"- Swimming: {swimming_duration_min:.0f} min")
    if strength_activities:
        lines.append(f"- Strength: {len(strength_activities)} workouts / {strength_duration_min:.0f} min")

    lines.extend(
        [
            "",
            "## Recovery",
            "",
            f"- Compatibility: {recovery_metrics.compatibility}",
            f"- Fatigue risk: {recovery_metrics.fatigue_risk}",
            f"- Recovery load score: {recovery_metrics.load_score:.1f}",
            "- Main drivers:",
            *[f"  - {driver}" for driver in recovery_metrics.drivers],
            "- Suggested next day:",
            *[f"  - {suggestion}" for suggestion in recovery_metrics.suggested_next_day],
            "",
            "## Trends",
            "",
            f"- 7-day avg Activity score: {_format_average_activity_score(activity_metrics.avg_7d)}",
            f"- 30-day avg Activity score: {_format_average_activity_score(activity_metrics.avg_30d)}",
        ]
    )
    if walking_distance_km > 0 or walking_duration_min > 0:
        lines.extend(
            [
                f"- 7-day avg walking: {walking_metrics['avg_7d']}",
                f"- 30-day avg walking: {walking_metrics['avg_30d']}",
            ]
        )
    lines.extend(
        [
            f"- 7-day avg weight: {weight_metrics['avg_7d']}",
            f"- 30-day avg weight: {weight_metrics['avg_30d']}",
            "",
            "## Data Coverage",
            "",
            f"- Sources: {sources}",
            f"- Activities: {len(primary_today_activities)}",
            "",
            "## Handoff",
            "",
            _ai_handoff(
                activities=primary_today_activities,
                activity_metrics=activity_metrics,
                total_duration_min=total_duration_min,
                withings_steps_text=withings_steps_text,
                walking_distance_km=walking_distance_km,
                swimming_duration_min=swimming_duration_min,
                strength_count=len(strength_activities),
                strength_duration_min=strength_duration_min,
                recovery_metrics=recovery_metrics,
                walking_metrics=walking_metrics,
                weight_metrics=weight_metrics,
            ),
            "",
        ]
    )

    if primary_today_activities:
        lines.extend(_render_activity_sections(primary_today_activities, state.hevy_sets))

    if measures:
        lines.extend(["## Body", ""])
        for measure in measures:
            lines.append(
                "- "
                f"{measure.get('type_name') or 'measurement'}: "
                f"{measure.get('value') or '0.00'} {measure.get('unit') or ''}".rstrip()
            )
        lines.append("")
    return "\n".join(lines)


def _activity_level(total_distance_km: float, total_duration_min: float, activity_count: int) -> str:
    if activity_count == 0:
        return "None"
    if total_distance_km <= 5 and total_duration_min <= 60:
        return "Light"
    if total_distance_km <= 12 and total_duration_min <= 120:
        return "Moderate"
    return "High"


def _activity_metrics(
    activities: list[NormalizedActivity],
    historical_activities: list[NormalizedActivity],
    target_date: date,
    *,
    total_distance_km: float,
    total_duration_min: float,
) -> ActivityMetrics:
    current_7d = _average_daily_activity_score(historical_activities, target_date, days=7)
    previous_7d = _average_daily_activity_score(historical_activities, target_date - _date_delta(7), days=7)
    avg_30d = _average_daily_activity_score(historical_activities, target_date, days=30)
    return ActivityMetrics(
        label=_activity_level(total_distance_km, total_duration_min, len(activities)),
        score=_activity_score_for_totals(total_distance_km, total_duration_min, len(activities)),
        avg_7d=current_7d,
        avg_30d=avg_30d,
        trend=_activity_score_trend(current_7d, previous_7d),
    )


def _activity_score_for_totals(
    total_non_swim_distance_km: float,
    total_duration_min: float,
    activity_count: int,
) -> float | None:
    if activity_count == 0:
        return None
    return total_non_swim_distance_km + (total_duration_min / 12)


def _average_daily_activity_score(
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

    total_non_swim_distance_km = sum(
        activity.distance_km or 0.0
        for activity in activities_in_window
        if activity.activity_type != "swim"
    )
    total_duration_min = sum(activity.duration_min for activity in activities_in_window)
    return (total_non_swim_distance_km + (total_duration_min / 12)) / days


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
        drivers.append(f"{strength_duration_min:.0f} min strength session")
    if total_sets > 0:
        drivers.append(f"{total_sets} total sets")
    if total_volume_kg > 0:
        drivers.append(f"{_format_volume(total_volume_kg)} strength volume")
    if full_body:
        drivers.append("full-body workout")
    if exercise_count > 0:
        drivers.append(f"{exercise_count} exercises")
    if walking_distance_km > 0:
        drivers.append(f"{walking_distance_km:.2f} km walking")
    if swimming_duration_min > 0:
        drivers.append(f"{swimming_duration_min:.0f} min swimming")
    if ride_distance_km > 0:
        drivers.append(f"{ride_distance_km:.2f} km cycling")
    if run_distance_km > 0:
        drivers.append(f"{run_distance_km:.2f} km running")
    if other_distance_km > 0:
        drivers.append(f"{other_distance_km:.2f} km other activity distance")
    if mixed_activity:
        drivers.append(
            _mixed_activity_driver(
                walking_distance_km,
                swimming_duration_min,
                strength_workout_count,
                ride_distance_km,
                run_distance_km,
                other_distance_km,
            )
        )
    if subjective_all_out:
        drivers.append("subjective all-out effort noted")
    if strength_details_missing:
        drivers.append("strength details unavailable; score uses duration only")
    if not drivers:
        drivers.append("no activity load recorded")
    return drivers


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
    return " + ".join(activity_types) + " same day"


def _recovery_suggestions(compatibility: str, strength_training: bool, walking_today: bool) -> list[str]:
    if compatibility == "Poor":
        suggestions = ["avoid heavy full-body strength training", _light_movement_suggestion(walking_today)]
    elif compatibility == "Caution":
        suggestions = ["avoid stacking hard sessions", _light_movement_suggestion(walking_today)]
    elif compatibility == "Acceptable":
        suggestions = ["keep next session moderate", "watch soreness and sleep quality"]
    else:
        suggestions = ["normal activity is compatible with current load"]
    if strength_training and "prioritize sleep and hydration" not in suggestions:
        suggestions.append("prioritize sleep and hydration")
    return suggestions


def _light_movement_suggestion(walking_today: bool) -> str:
    if walking_today:
        return "keep walking light if fatigue is high"
    return "keep low-impact movement light if fatigue is high"


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
            f"{activity_metrics.label} activity day with {len(activities)} primary activities"
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
        f" Strength training included {strength_count} workouts and {strength_duration_min:.0f} min."
        if strength_count > 0
        else ""
    )
    recovery_sentence = (
        f" Recovery compatibility is {recovery_metrics.compatibility}; "
        f"fatigue risk is {recovery_metrics.fatigue_risk}; "
        f"load score is {recovery_metrics.load_score:.1f}."
    )
    return (
        f"{activity_sentence}{score_sentence}{swimming_sentence}{strength_sentence}{recovery_sentence} "
        f"{_walking_handoff_sentence(walking_distance_km, walking_metrics)}"
        f"Current weight is {weight_metrics['current_weight']}; "
        f"weight trend is {weight_metrics['trend']}."
    )


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


def _optional_int_value(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
