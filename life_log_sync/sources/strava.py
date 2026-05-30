from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from life_log_sync.app_data import write_csv_file, write_json_file
from life_log_sync.config import AppConfig, update_strava_tokens

API_URL = "https://www.strava.com/api/v3/athlete/activities"
TOKEN_URL = "https://www.strava.com/oauth/token"
TIMEOUT_SECONDS = 30

ACTIVITY_FIELDS = [
    "id",
    "start_date_local",
    "name",
    "sport_type",
    "distance_km",
    "moving_time_min",
    "elapsed_time_min",
    "total_elevation_gain_m",
    "average_speed_mps",
    "max_speed_mps",
]


def sync(config: AppConfig) -> list[Path]:
    requests = _requests()

    with requests.Session() as session:
        access_token = get_access_token(session, config)
        activities = fetch_recent_activities(
            session,
            access_token,
            days=config.strava.days,
            per_page=config.strava.per_page,
        )

    written_paths = write_activities(config, activities, merge=True)
    return written_paths


def backfill(config: AppConfig, *, start_date: date, end_date: date | None = None) -> list[Path]:
    requests = _requests()

    with requests.Session() as session:
        access_token = get_access_token(session, config)
        activities = fetch_activities_since(
            session,
            access_token,
            start_date=start_date,
            end_date=end_date,
            per_page=config.strava.per_page,
        )

    return write_activities(config, activities, merge=True)


def get_access_token(session: Any, config: AppConfig) -> str:
    if config.strava.refresh_token:
        return refresh_access_token(session, config)
    if config.strava.access_token:
        return config.strava.access_token
    raise SystemExit(
        "Missing Strava credentials. Set strava.refresh_token in the config file, "
        "or set strava.access_token for a one-off run."
    )


def refresh_access_token(session: Any, config: AppConfig) -> str:
    _require(config.strava.client_id, "strava.client_id")
    _require(config.strava.client_secret, "strava.client_secret")
    _require(config.strava.refresh_token, "strava.refresh_token")

    response = session.post(
        TOKEN_URL,
        data={
            "client_id": config.strava.client_id,
            "client_secret": config.strava.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": config.strava.refresh_token,
        },
        timeout=TIMEOUT_SECONDS,
    )
    _raise_for_strava_error(response, "Strava token refresh failed")

    token = _json_response(response, "Strava token refresh response was not valid JSON.")
    update_strava_tokens(config, token)
    return str(token["access_token"])


def fetch_recent_activities(session: Any, access_token: str, *, days: int, per_page: int) -> list[dict[str, Any]]:
    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    return fetch_activities(session, access_token, after=after, per_page=per_page)


def fetch_activities_since(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date | None,
    per_page: int,
) -> list[dict[str, Any]]:
    if end_date is not None and start_date > end_date:
        raise SystemExit("Strava start date must be on or before end date.")

    after = int(datetime.combine(start_date, time.min, tzinfo=timezone.utc).timestamp())
    before = int(datetime.combine(end_date, time.max, tzinfo=timezone.utc).timestamp()) if end_date else None
    return fetch_activities(session, access_token, after=after, before=before, per_page=per_page)


def fetch_activities(
    session: Any,
    access_token: str,
    *,
    after: int,
    per_page: int,
    before: int | None = None,
) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    page = 1

    while True:
        params = {"after": after, "page": page, "per_page": per_page}
        if before is not None:
            params["before"] = before
        response = session.get(
            API_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=TIMEOUT_SECONDS,
        )
        _raise_for_strava_error(response, "Strava API request failed")

        page_activities = _json_response(response, "Strava API response was not valid JSON.")
        if not isinstance(page_activities, list):
            raise SystemExit("Strava API response did not contain a list of activities.")

        activities.extend(page_activities)
        if len(page_activities) < per_page:
            return activities
        page += 1


def write_activities(
    config: AppConfig,
    activities: list[dict[str, Any]],
    *,
    merge: bool = False,
) -> list[Path]:
    written_paths: list[Path] = []
    raw_dir = config.strava.raw_dir

    for activity in activities:
        activity_id = activity.get("id")
        if not activity_id:
            continue
        written_paths.append(write_json_file(raw_dir / f"{activity_id}.json", activity))

    rows = [normalize_activity(activity) for activity in activities]
    if merge:
        rows = merge_activity_rows(read_activity_rows(config.strava.activities_csv), rows)
    written_paths.append(write_csv_file(config.strava.activities_csv, rows, ACTIVITY_FIELDS))
    return written_paths


def read_activity_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def merge_activity_rows(
    existing_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in [*existing_rows, *new_rows]:
        activity_id = str(row.get("id", "")).strip()
        if activity_id:
            rows_by_id[activity_id] = row
    return sorted(
        rows_by_id.values(),
        key=lambda row: (str(row.get("start_date_local", "")), str(row.get("id", ""))),
    )


def normalize_activity(activity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": activity.get("id", ""),
        "start_date_local": activity.get("start_date_local", ""),
        "name": activity.get("name", ""),
        "sport_type": activity.get("sport_type") or activity.get("type", ""),
        "distance_km": _round_float(activity.get("distance", 0), divisor=1000),
        "moving_time_min": _round_float(activity.get("moving_time", 0), divisor=60),
        "elapsed_time_min": _round_float(activity.get("elapsed_time", 0), divisor=60),
        "total_elevation_gain_m": activity.get("total_elevation_gain", ""),
        "average_speed_mps": activity.get("average_speed", ""),
        "max_speed_mps": activity.get("max_speed", ""),
    }


def _round_float(value: Any, *, divisor: float) -> str:
    try:
        return f"{float(value) / divisor:.2f}"
    except (TypeError, ValueError):
        return ""


def _raise_for_strava_error(response: Any, prefix: str) -> None:
    try:
        response.raise_for_status()
    except Exception as exc:
        status_code = getattr(response, "status_code", "unknown")
        body = getattr(response, "text", "")
        if status_code == 401 and "activity:read_permission" in body:
            raise SystemExit(
                "Strava rejected the token: missing activity:read permission. "
                "Re-authorize the app with the activity:read scope."
            ) from exc
        raise SystemExit(f"{prefix} with HTTP {status_code}: {body}") from exc


def _json_response(response: Any, error_message: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise SystemExit(error_message) from exc


def _require(value: str, name: str) -> None:
    if not value:
        raise SystemExit(f"Missing {name} in the config file.")


def _requests() -> Any:
    try:
        import requests
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `poetry install`.") from exc
    return requests
