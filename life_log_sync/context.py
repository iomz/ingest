from __future__ import annotations

import csv
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from life_log_sync.app_data import write_text_file
from life_log_sync.config import AppConfig


def generate_today_context(config: AppConfig, target_date: date | None = None) -> Path:
    target = target_date or date.today()
    activities = activities_for_date(read_strava_activities(config.strava.activities_csv), target)
    all_measures = read_withings_measures(config.withings.measures_csv)
    measures = measures_for_date(all_measures, target)
    content = render_today_context(target, activities, measures, all_measures)
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


def activities_for_date(activities: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [
        activity
        for activity in activities
        if activity.get("start_date_local", "").startswith(target)
    ]


def measures_for_date(measures: list[dict[str, str]], target_date: date) -> list[dict[str, str]]:
    target = target_date.isoformat()
    return [measure for measure in measures if measure.get("date") == target]


def render_today_context(
    target_date: date,
    activities: list[dict[str, str]],
    measures: list[dict[str, str]] | None = None,
    historical_measures: list[dict[str, str]] | None = None,
) -> str:
    measures = measures or []
    historical_measures = historical_measures if historical_measures is not None else measures
    total_distance_km = sum(_float_value(activity.get("distance_km", "")) for activity in activities)
    total_duration_min = sum(_float_value(activity.get("moving_time_min", "")) for activity in activities)
    walking_distance_km = sum(
        _float_value(activity.get("distance_km", ""))
        for activity in activities
        if _is_walking_activity(activity)
    )
    activity_types = Counter(activity.get("sport_type") or "Unknown" for activity in activities)
    activity_level = _activity_level(total_distance_km, len(activities))
    recovery_compatibility = _recovery_compatibility(activity_level)
    weight_metrics = _weight_metrics(historical_measures, target_date)

    lines = [
        f"# Today Context - {target_date.isoformat()}",
        "",
        "## Level 2: Derived Metrics",
        "",
        f"- Activity level: {activity_level} ({total_distance_km:.2f} km total)",
        f"- Recovery compatibility: {recovery_compatibility} (deterministic from activity level)",
        f"- Total walking distance: {walking_distance_km:.2f} km",
        f"- Total moving time: {total_duration_min:.0f} min",
        f"- Current weight: {weight_metrics['current_weight']}",
        f"- 7-day average weight: {weight_metrics['avg_7d']}",
        f"- 30-day average weight: {weight_metrics['avg_30d']}",
        f"- Weight trend: {weight_metrics['trend']}",
        "",
        "Assumptions: activity thresholds are None = no activities or 0 km, Light <= 5 km, "
        "Moderate <= 12 km, High > 12 km. Walking includes sport types containing walk or hike. "
        "Weight trend compares the current 7-day average with the previous 7-day average; "
        "at least one weight in each 7-day window is required.",
        "",
        "## Level 3: AI Handoff",
        "",
        _ai_handoff(
            activities=activities,
            activity_level=activity_level,
            recovery_compatibility=recovery_compatibility,
            total_distance_km=total_distance_km,
            total_duration_min=total_duration_min,
            walking_distance_km=walking_distance_km,
            weight_metrics=weight_metrics,
        ),
        "",
        "## Strava",
        "",
    ]

    if not activities:
        lines.append("No Strava activities found for this date.")
        lines.append("")
    else:
        lines.extend(
            [
                f"- Activities: {len(activities)}",
                f"- Distance: {total_distance_km:.2f} km",
                f"- Moving time: {total_duration_min:.0f} min",
                f"- Types: {_format_activity_types(activity_types)}",
                "",
                "### Activities",
                "",
            ]
        )

        for activity in activities:
            lines.append(
                "- "
                f"{activity.get('sport_type') or 'Unknown'}: "
                f"{activity.get('name') or 'Untitled'} "
                f"({activity.get('distance_km') or '0.00'} km, "
                f"{_format_minutes(activity.get('moving_time_min', ''))})"
            )
        lines.append("")

    lines.extend(["## Withings", ""])

    if not measures:
        lines.append("No Withings body measurements found for this date.")
        lines.append("")
        return "\n".join(lines)

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


def _date_delta(days: int) -> timedelta:
    return timedelta(days=days)


def _ai_handoff(
    *,
    activities: list[dict[str, str]],
    activity_level: str,
    recovery_compatibility: str,
    total_distance_km: float,
    total_duration_min: float,
    walking_distance_km: float,
    weight_metrics: dict[str, str],
) -> str:
    if not activities:
        activity_sentence = "No Strava activities found for this date."
    else:
        activity_sentence = (
            f"{activity_level} activity day with {len(activities)} Strava activities, "
            f"{total_distance_km:.2f} km total, {walking_distance_km:.2f} km walking, "
            f"and {total_duration_min:.0f} min moving time."
        )
    return (
        f"{activity_sentence} Recovery compatibility is {recovery_compatibility}. "
        f"Current weight is {weight_metrics['current_weight']}; "
        f"weight trend is {weight_metrics['trend']}."
    )


def _is_walking_activity(activity: dict[str, str]) -> bool:
    sport_type = activity.get("sport_type", "").lower()
    return "walk" in sport_type or "hike" in sport_type


def _format_activity_types(activity_types: Counter[str]) -> str:
    return ", ".join(
        f"{activity_type} x{count}" if count > 1 else activity_type
        for activity_type, count in sorted(activity_types.items())
    )


def _format_minutes(value: str) -> str:
    return f"{_float_value(value):.0f} min"


def _float_value(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
