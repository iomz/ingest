from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta


@dataclass(frozen=True)
class NormalizedActivity:
    source: str
    source_id: str
    start_time: str
    end_time: str
    duration_min: float
    distance_km: float | None
    activity_type: str
    raw_type: str
    name: str = ""
    dedup_group_id: str = ""
    is_primary: bool = True


def normalize_strava_activity(activity: dict[str, str]) -> NormalizedActivity:
    start_time = activity.get("start_date_local", "")
    duration_min = _float_value(activity.get("moving_time_min", ""))
    end_time = _end_time(start_time, duration_min)
    distance_km = _optional_float(activity.get("distance_km", ""))
    raw_type = activity.get("sport_type") or "Unknown"
    return NormalizedActivity(
        source="strava",
        source_id=activity.get("id", "") or start_time,
        start_time=start_time,
        end_time=end_time,
        duration_min=duration_min,
        distance_km=distance_km,
        activity_type=canonical_activity_type(raw_type),
        raw_type=raw_type,
        name=activity.get("name", ""),
    )


def normalize_withings_activity(activity: dict[str, str]) -> NormalizedActivity:
    start_time = activity.get("start_time", "") or activity.get("start_date_local", "")
    duration_min = _float_value(activity.get("duration_min", "") or activity.get("moving_time_min", ""))
    end_time = activity.get("end_time", "") or _end_time(start_time, duration_min)
    distance_km = _optional_float(activity.get("distance_km", ""))
    raw_type = activity.get("raw_type") or activity.get("activity_type") or activity.get("sport_type") or "Unknown"
    return NormalizedActivity(
        source="withings",
        source_id=activity.get("source_id", "") or activity.get("id", "") or start_time,
        start_time=start_time,
        end_time=end_time,
        duration_min=duration_min,
        distance_km=distance_km,
        activity_type=canonical_activity_type(raw_type),
        raw_type=raw_type,
        name=activity.get("name", ""),
    )


def deduplicate_activities(activities: list[NormalizedActivity]) -> list[NormalizedActivity]:
    groups: list[list[NormalizedActivity]] = []
    for activity in sorted(activities, key=lambda item: (item.start_time, item.source, item.source_id)):
        for group in groups:
            if any(_same_activity(activity, existing) for existing in group):
                group.append(activity)
                break
        else:
            groups.append([activity])

    deduplicated: list[NormalizedActivity] = []
    for group in groups:
        group_id = _group_id(group)
        primary = _primary_activity(group)
        for activity in group:
            deduplicated.append(
                replace(
                    activity,
                    dedup_group_id=group_id,
                    is_primary=activity == primary,
                )
            )
    return sorted(deduplicated, key=lambda item: (item.start_time, item.source, item.source_id))


def primary_activities(activities: list[NormalizedActivity]) -> list[NormalizedActivity]:
    return [activity for activity in activities if activity.is_primary]


def canonical_activity_type(raw_type: str) -> str:
    value = raw_type.strip().lower().replace("_", " ")
    if value in {"walk", "walking", "indoor walking", "hike", "hiking"}:
        return "walk"
    if value in {"swim", "swimming"}:
        return "swim"
    if value in {"run", "running"}:
        return "run"
    if value in {"ride", "bicycle", "cycling"}:
        return "ride"
    return value or "unknown"


def coverage_summary(activities: list[NormalizedActivity]) -> dict[str, str]:
    before = len(activities)
    after = len(primary_activities(activities))
    sources = sorted({activity.source.capitalize() for activity in activities})
    return {
        "before": str(before),
        "after": str(after),
        "deduplicated_pairs": str(before - after),
        "sources": ", ".join(sources) if sources else "None",
    }


def _same_activity(left: NormalizedActivity, right: NormalizedActivity) -> bool:
    left_start = _parse_time(left.start_time)
    right_start = _parse_time(right.start_time)
    if left_start is None or right_start is None:
        return False
    if abs(left_start - right_start) > timedelta(minutes=10):
        return False
    if not _duration_matches(left.duration_min, right.duration_min):
        return False
    if not _distance_matches(left.distance_km, right.distance_km):
        return False
    return _compatible_activity_types(left.activity_type, right.activity_type)


def _duration_matches(left: float, right: float) -> bool:
    difference = abs(left - right)
    if difference <= 10:
        return True
    larger = max(left, right)
    return larger > 0 and difference / larger <= 0.15


def _distance_matches(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return True
    difference = abs(left - right)
    larger = max(left, right)
    return larger == 0 or difference / larger <= 0.15


def _compatible_activity_types(left: str, right: str) -> bool:
    walking_types = {"walk", "walking", "indoor walking", "hike", "hiking"}
    if left in walking_types and right in walking_types:
        return True
    return left == right


def _primary_activity(group: list[NormalizedActivity]) -> NormalizedActivity:
    return sorted(group, key=_primary_rank)[0]


def _primary_rank(activity: NormalizedActivity) -> tuple[int, str, str]:
    if activity.activity_type == "swim" and activity.source == "withings":
        return (0, activity.source, activity.source_id)
    if activity.activity_type == "walk" and activity.source == "strava":
        return (1, activity.source, activity.source_id)
    return (2, activity.source, activity.source_id)


def _group_id(group: list[NormalizedActivity]) -> str:
    first = sorted(group, key=lambda item: (item.source, item.source_id))[0]
    return f"{first.source}:{first.source_id}"


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None


def _end_time(start_time: str, duration_min: float) -> str:
    start = _parse_time(start_time)
    if start is None:
        return ""
    return (start + timedelta(minutes=duration_min)).isoformat()


def _optional_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_value(value: str) -> float:
    return _optional_float(value) or 0.0
