from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = ZoneInfo("Asia/Tokyo")


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
    energy_kcal: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    tss_score: float | None = None
    tss_method: str = ""
    intensity_factor: float | None = None
    recovery_time_seconds: float | None = None
    detail_source: str = ""
    detail_source_id: str = ""
    detail_name: str = ""


def normalize_withings_activity(
    activity: dict[str, str],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> NormalizedActivity:
    return normalize_activity(activity, source="withings", local_timezone=local_timezone)


def normalize_hevy_activity(
    activity: dict[str, str],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> NormalizedActivity:
    return normalize_activity(activity, source="hevy", local_timezone=local_timezone)


def normalize_suunto_activity(
    activity: dict[str, str],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> NormalizedActivity:
    return normalize_activity(activity, source="suunto", local_timezone=local_timezone)


def normalize_activity(
    activity: dict[str, str],
    *,
    source: str,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> NormalizedActivity:
    start_time = _local_time(activity.get("start_time", ""), local_timezone)
    duration_min = _float_value(activity.get("duration_min", ""))
    end_time = _local_time(activity.get("end_time", ""), local_timezone) or _end_time(
        start_time,
        duration_min,
    )
    distance_km = _optional_float(activity.get("distance_km", ""))
    raw_type = activity.get("raw_type") or activity.get("activity_type") or "Unknown"
    activity_type = activity.get("activity_type") or raw_type
    name = activity.get("name", "")
    if source == "suunto":
        raw_type = _legacy_suunto_activity_label(raw_type)
        activity_type = _legacy_suunto_activity_label(activity_type)
        name = _legacy_suunto_name(name, raw_type)
    return NormalizedActivity(
        source=source,
        source_id=activity.get("source_id", "") or activity.get("id", "") or start_time,
        start_time=start_time,
        end_time=end_time,
        duration_min=duration_min,
        distance_km=distance_km,
        activity_type=canonical_activity_type(activity_type),
        raw_type=raw_type,
        name=name,
        notes=activity.get("notes", "") or activity.get("description", ""),
        step_count=_int_value(activity.get("step_count", "") or activity.get("steps", "")),
        energy_kcal=_optional_float(activity.get("energy_kcal", "")),
        avg_hr=_optional_float(activity.get("avg_hr", "")),
        max_hr=_optional_float(activity.get("max_hr", "")),
        tss_score=_optional_float(activity.get("tss_score", "")),
        tss_method=activity.get("tss_method", "") or "",
        intensity_factor=_optional_float(activity.get("intensity_factor", "")),
        recovery_time_seconds=_optional_float(activity.get("recovery_time_seconds", "")),
    )


def canonical_activity_type(raw_type: str) -> str:
    value = raw_type.strip().lower().replace("_", " ")
    if value in {"walk", "walking", "indoor walking", "hike", "hiking", "nordic walking", "trekking"}:
        return "walk"
    if value in {"swim", "swimming", "openwater swimming"}:
        return "swim"
    if value in {"run", "running", "trail running", "track running", "treadmill", "vertical run"}:
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


def _legacy_suunto_activity_label(label: str) -> str:
    value = label.strip().lower().replace("_", " ")
    return {
        "activity 17": "INDOOR",
        "activity 55": "CROSSTRAINER",
    }.get(value, label)


def _legacy_suunto_name(name: str, raw_type: str) -> str:
    if name.strip().lower() not in {"activity 17", "activity 55"}:
        return name
    return raw_type.replace("_", " ").title()


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _end_time(start_time: str, duration_min: float) -> str:
    start = _parse_time(start_time)
    if start is None:
        return ""
    return (start + timedelta(minutes=duration_min)).isoformat()


def _local_time(value: str, local_timezone: ZoneInfo) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_timezone)
    else:
        parsed = parsed.astimezone(local_timezone)
    return parsed.isoformat()


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
