from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anyio

from ingest.activities import canonical_activity_type
from ingest.app_data import write_csv_file, write_json_file
from ingest.config import AppConfig, SuuntoConfig

WORKOUT_FIELDS = [
    "source",
    "source_id",
    "start_time",
    "end_time",
    "duration_min",
    "distance_km",
    "step_count",
    "activity_type",
    "raw_type",
    "name",
    "notes",
]

ACTIVITY_NAMES = {
    0: "WALKING",
    1: "RUNNING",
    2: "CYCLING",
    10: "MOUNTAIN_BIKING",
    11: "HIKING",
    20: "OUTDOOR_GYM",
    21: "SWIMMING",
    22: "TRAIL_RUNNING",
    23: "GYM",
    24: "NORDIC_WALKING",
    52: "INDOOR_CYCLING",
    53: "TREADMILL",
    54: "CROSSFIT",
    63: "KETTLEBELL",
    70: "TREKKING",
    85: "OPENWATER_SWIMMING",
    99: "GRAVEL_CYCLING",
    103: "TRACK_RUNNING",
    104: "CALISTHENICS",
    105: "E_BIKING",
    106: "E_MTB",
    109: "HAND_CYCLING",
    115: "VERTICAL_RUN",
}


def sync(config: AppConfig) -> list[Path]:
    return anyio.run(sync_async, config)


async def sync_async(config: AppConfig, *, end_date: date | None = None) -> list[Path]:
    cutoff = end_date or date.today()
    existing_rows = read_workout_rows(config.suunto.workouts_csv)
    since = _sync_start_date(existing_rows, cutoff, config.suunto.days)
    workouts = await fetch_workouts(config.suunto, since)
    raw_path = write_json_file(config.suunto.raw_dir / "workouts_sync.json", workouts)
    normalized_rows = normalize_workouts(workouts)
    merged_rows = _merge_workout_rows(existing_rows, normalized_rows)
    workouts_path = write_csv_file(config.suunto.workouts_csv, merged_rows, WORKOUT_FIELDS)
    return [raw_path, workouts_path]


async def fetch_workouts(config: SuuntoConfig, since: date) -> list[dict[str, Any]]:
    command = [
        config.command,
        "workouts",
        "list",
        "--since",
        since.isoformat(),
        "--stream",
    ]
    try:
        result = await anyio.run_process(command, check=False)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Could not run Suunto sync command {config.command!r}. "
            "Install and log in to suuntool, or set suunto.command."
        ) from exc

    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        detail = error or f"exit status {result.returncode}"
        raise SystemExit(f"Suunto sync failed: {detail}")

    return parse_workouts(result.stdout.decode("utf-8"))


def parse_workouts(output: str) -> list[dict[str, Any]]:
    workouts: list[dict[str, Any]] = []
    for line_number, line in enumerate(output.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            workout = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Could not parse suuntool output at line {line_number}: {exc}") from exc
        if not isinstance(workout, dict):
            raise SystemExit(f"Could not parse suuntool output at line {line_number}: expected an object.")
        workouts.append(workout)
    return workouts


def normalize_workouts(workouts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, workout in enumerate(workouts, start=1):
        source_id = str(workout.get("key", "")).strip()
        if not source_id:
            raise SystemExit(f"Suunto workout {index} is missing key.")

        start_time = _local_time(workout.get("startTime"))
        if not start_time:
            raise SystemExit(f"Suunto workout {source_id!r} has invalid startTime.")

        duration_seconds = _float_value(workout.get("totalTime"))
        raw_type = _activity_name(workout, source_id)
        rows.append(
            {
                "source": "suunto",
                "source_id": source_id,
                "start_time": start_time,
                "end_time": _local_time(workout.get("stopTime")),
                "duration_min": f"{duration_seconds / 60:.2f}",
                "distance_km": _optional_distance_km(workout.get("totalDistance")),
                "step_count": _int_value(workout.get("stepCount")),
                "activity_type": canonical_activity_type(raw_type),
                "raw_type": raw_type,
                "name": raw_type.replace("_", " ").title(),
                "notes": "",
            }
        )
    return sorted(rows, key=lambda row: str(row["start_time"]))


def read_workout_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _sync_start_date(rows: list[dict[str, str]], cutoff: date, fallback_days: int) -> date:
    dates = [row.get("start_time", "")[:10] for row in rows]
    valid_dates = [date.fromisoformat(value) for value in dates if _is_iso_date(value)]
    return max(valid_dates) if valid_dates else cutoff - timedelta(days=fallback_days - 1)


def _merge_workout_rows(
    existing_rows: list[dict[str, str]],
    fetched_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_id: dict[str, dict[str, Any]] = {
        row.get("source_id", ""): row for row in existing_rows if row.get("source_id")
    }
    rows_by_id.update({str(row["source_id"]): row for row in fetched_rows})
    return sorted(rows_by_id.values(), key=lambda row: str(row.get("start_time", "")))


def _local_time(value: Any) -> str:
    milliseconds = _float_value(value)
    if milliseconds <= 0:
        return ""
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).astimezone().isoformat()


def _optional_distance_km(value: Any) -> str:
    meters = _float_value(value)
    return f"{meters / 1000:.2f}" if meters > 0 else ""


def _activity_name(workout: dict[str, Any], source_id: str) -> str:
    activity_name = str(workout.get("activityName", "")).strip()
    if activity_name:
        return activity_name

    activity_id = _optional_int_value(workout.get("activityId"))
    if activity_id is None:
        raise SystemExit(
            f"Suunto workout {source_id!r} is missing activityName and has invalid activityId."
        )
    return ACTIVITY_NAMES.get(activity_id, f"activity_{activity_id}")


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
