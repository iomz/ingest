from __future__ import annotations

from dataclasses import dataclass
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
    notes: str = ""
    step_count: int = 0


def normalize_withings_activity(activity: dict[str, str]) -> NormalizedActivity:
    return normalize_activity(activity, source="withings")


def normalize_hevy_activity(activity: dict[str, str]) -> NormalizedActivity:
    return normalize_activity(activity, source="hevy")


def normalize_suunto_activity(activity: dict[str, str]) -> NormalizedActivity:
    return normalize_activity(activity, source="suunto")


def normalize_activity(activity: dict[str, str], *, source: str) -> NormalizedActivity:
    start_time = activity.get("start_time", "")
    duration_min = _float_value(activity.get("duration_min", ""))
    end_time = activity.get("end_time", "") or _end_time(start_time, duration_min)
    distance_km = _optional_float(activity.get("distance_km", ""))
    raw_type = activity.get("raw_type") or activity.get("activity_type") or "Unknown"
    return NormalizedActivity(
        source=source,
        source_id=activity.get("source_id", "") or activity.get("id", "") or start_time,
        start_time=start_time,
        end_time=end_time,
        duration_min=duration_min,
        distance_km=distance_km,
        activity_type=canonical_activity_type(activity.get("activity_type") or raw_type),
        raw_type=raw_type,
        name=activity.get("name", ""),
        notes=activity.get("notes", "") or activity.get("description", ""),
        step_count=_int_value(activity.get("step_count", "") or activity.get("steps", "")),
    )


def canonical_activity_type(raw_type: str) -> str:
    value = raw_type.strip().lower().replace("_", " ")
    if value in {"walk", "walking", "indoor walking", "hike", "hiking", "nordic walking", "trekking"}:
        return "walk"
    if value in {"swim", "swimming", "openwater swimming"}:
        return "swim"
    if value in {"run", "running", "trail running", "track running", "vertical run"}:
        return "run"
    if value in {
        "ride",
        "bicycle",
        "cycling",
        "indoor cycling",
        "mountain biking",
        "gravel cycling",
        "e biking",
        "e mtb",
        "hand cycling",
    }:
        return "ride"
    if value in {
        "strength",
        "strength training",
        "weight training",
        "weights",
        "gym",
        "outdoor gym",
        "crossfit",
        "kettlebell",
        "calisthenics",
    }:
        return "strength"
    return value or "unknown"


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


def _int_value(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
