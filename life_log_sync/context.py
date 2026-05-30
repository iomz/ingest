from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from life_log_sync.activities import (
    NormalizedActivity,
    coverage_summary,
    deduplicate_activities,
    normalize_strava_activity,
    normalize_withings_activity,
    primary_activities,
)
from life_log_sync.app_data import write_text_file
from life_log_sync.config import AppConfig


def generate_today_context(config: AppConfig, target_date: date | None = None) -> Path:
    target = target_date or date.today()
    all_activities = read_strava_activities(config.strava.activities_csv)
    withings_activities = read_withings_activities(config.withings.workouts_csv)
    activities = activities_for_date(all_activities, target)
    withings_activities_for_target = withings_activities_for_date(withings_activities, target)
    all_measures = read_withings_measures(config.withings.measures_csv)
    measures = measures_for_date(all_measures, target)
    content = render_today_context(
        target,
        activities,
        measures,
        all_measures,
        all_activities,
        extra_activities=_normalize_withings_activities(withings_activities_for_target),
        extra_historical_activities=_normalize_withings_activities(withings_activities),
    )
    return write_text_file(config.today_context_path, content)


def read_strava_activities(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_withings_measures(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_withings_activities(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def activities_for_date(activities: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [
        activity
        for activity in activities
        if activity.get("start_date_local", "").startswith(target)
    ]


def withings_activities_for_date(activities: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [
        activity
        for activity in activities
        if activity.get("start_time", "").startswith(target)
    ]


def measures_for_date(measures: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [measure for measure in measures if measure.get("date") == target]


def render_today_context(
    target_date: date,
    activities: list[dict[str, str]],
    measures: list[dict[str, str]] | None = None,
    historical_measures: list[dict[str, str]] | None = None,
    historical_activities: list[dict[str, str]] | None = None,
    extra_activities: list[NormalizedActivity] | None = None,
    extra_historical_activities: list[NormalizedActivity] | None = None,
) -> str:
    measures = measures or []
    historical_measures = historical_measures if historical_measures is not None else measures
    historical_activities = historical_activities if historical_activities is not None else activities
    extra_activities = extra_activities or []
    extra_historical_activities = extra_historical_activities if extra_historical_activities is not None else extra_activities
    deduplicated_activities = deduplicate_activities([*_normalize_strava_activities(activities), *extra_activities])
    primary_today_activities = primary_activities(deduplicated_activities)
    historical_primary_activities = primary_activities(
        deduplicate_activities([*_normalize_strava_activities(historical_activities), *extra_historical_activities])
    )
    total_distance_km = sum(
        activity.distance_km or 0.0
        for activity in primary_today_activities
        if activity.activity_type != "swim"
    )
    total_duration_min = sum(activity.duration_min for activity in primary_today_activities)
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
    swimming_distance_km = sum(
        activity.distance_km or 0.0
        for activity in primary_today_activities
        if activity.activity_type == "swim"
    )
    swimming_duration_min = sum(
        activity.duration_min
        for activity in primary_today_activities
        if activity.activity_type == "swim"
    )
    activity_level = _activity_level(total_distance_km, len(primary_today_activities))
    recovery_compatibility = _recovery_compatibility(activity_level)
    weight_metrics = _weight_metrics(historical_measures, target_date)
    walking_metrics = _walking_metrics(historical_primary_activities, target_date)
    coverage = coverage_summary(deduplicated_activities)

    lines = [
        f"# Today Context - {target_date.isoformat()}",
        "",
        "## Summary",
        "",
        f"- Activity level: {activity_level}",
        f"- Recovery compatibility: {recovery_compatibility}",
        f"- Walking: {walking_distance_km:.2f} km / {walking_duration_min:.0f} min",
        f"- Walking trend: {walking_metrics['trend']}",
        f"- Current weight: {weight_metrics['current_weight']}",
        f"- Weight trend: {weight_metrics['trend']}",
    ]

    if swimming_distance_km > 0 or swimming_duration_min > 0:
        lines.append(f"- Swimming: {swimming_distance_km:.2f} km / {swimming_duration_min:.0f} min")

    lines.extend(
        [
            "",
            "## Trends",
            "",
            f"- 7-day avg walking: {walking_metrics['avg_7d']}",
            f"- 30-day avg walking: {walking_metrics['avg_30d']}",
            f"- 7-day avg weight: {weight_metrics['avg_7d']}",
            f"- 30-day avg weight: {weight_metrics['avg_30d']}",
            "",
            "## Data Coverage",
            "",
            f"- Sources: {coverage['sources']}",
            f"- Primary activities: {coverage['after']}",
            f"- Deduplicated activities: {coverage['deduplicated_pairs']}",
            "",
            "## Handoff",
            "",
            _ai_handoff(
                activities=primary_today_activities,
                activity_level=activity_level,
                total_duration_min=total_duration_min,
                walking_distance_km=walking_distance_km,
                swimming_distance_km=swimming_distance_km,
                swimming_duration_min=swimming_duration_min,
                walking_metrics=walking_metrics,
                weight_metrics=weight_metrics,
            ),
            "",
        ]
    )

    if primary_today_activities:
        lines.extend(["## Activities", ""])
        for activity in primary_today_activities:
            lines.append(
                "- "
                f"{activity.raw_type or 'Unknown'}: "
                f"{_display_activity_name(activity)} "
                f"({_format_distance(activity.distance_km)}, "
                f"{activity.duration_min:.0f} min)"
            )
        lines.append("")

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


def _activity_level(total_distance_km: float, activity_count: int) -> str:
    if activity_count == 0 or total_distance_km == 0:
        return "None"
    if total_distance_km <= 5:
        return "Light"
    if total_distance_km <= 12:
        return "Moderate"
    return "High"


def _recovery_compatibility(activity_level: str) -> str:
    if activity_level in {"None", "Light"}:
        return "Good"
    if activity_level == "Moderate":
        return "Acceptable"
    return "Poor"


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
    activity_level: str,
    total_duration_min: float,
    walking_distance_km: float,
    swimming_distance_km: float,
    swimming_duration_min: float,
    walking_metrics: dict[str, str],
    weight_metrics: dict[str, str],
) -> str:
    if not activities:
        activity_sentence = "No primary activities found for this date."
    else:
        activity_sentence = (
            f"{activity_level} walking day with {len(activities)} primary activities, "
            f"{walking_distance_km:.2f} km walking, "
            f"and {total_duration_min:.0f} min moving time."
        )
    swimming_sentence = (
        f" Swimming included {swimming_distance_km:.2f} km and {swimming_duration_min:.0f} min."
        if swimming_duration_min > 0
        else ""
    )
    return (
        f"{activity_sentence}{swimming_sentence} Walking trend is {walking_metrics['trend']}. "
        f"Current weight is {weight_metrics['current_weight']}; "
        f"weight trend is {weight_metrics['trend']}."
    )


def _is_walking_activity(activity: dict[str, str] | NormalizedActivity) -> bool:
    if isinstance(activity, NormalizedActivity):
        return activity.activity_type == "walk"
    sport_type = activity.get("sport_type", "").lower()
    return "walk" in sport_type or "hike" in sport_type


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


def _normalize_strava_activities(activities: list[dict[str, str]]) -> list[NormalizedActivity]:
    return [normalize_strava_activity(activity) for activity in activities]


def _normalize_withings_activities(activities: list[dict[str, str]]) -> list[NormalizedActivity]:
    return [normalize_withings_activity(activity) for activity in activities]


def _float_value(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
