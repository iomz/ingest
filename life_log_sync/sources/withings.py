from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from life_log_sync.app_data import write_csv_file, write_json_file
from life_log_sync.config import AppConfig, update_withings_tokens

TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
TIMEOUT_SECONDS = 30
BACKFILL_WINDOW_DAYS = 90
DAILY_SYNC_DAYS = 7

BODY_MEASURE_TYPES = {
    1: ("weight", "kg"),
    5: ("fat_free_mass", "kg"),
    6: ("fat_ratio", "%"),
    8: ("fat_mass_weight", "kg"),
    76: ("muscle_mass", "kg"),
    77: ("hydration", "kg"),
    88: ("bone_mass", "kg"),
    91: ("pulse_wave_velocity", "m/s"),
}
BODY_MEASURE_TYPE_IDS = ",".join(str(measure_type) for measure_type in BODY_MEASURE_TYPES)

MEASURE_FIELDS = [
    "grpid",
    "date",
    "datetime_local",
    "type",
    "type_name",
    "value",
    "unit",
]


def sync(config: AppConfig) -> list[Path]:
    today = date.today()
    return sync_range(config, today - timedelta(days=DAILY_SYNC_DAYS - 1), today, raw_name="body_measures_recent.json")


def backfill(config: AppConfig, *, start_date: date, end_date: date | None = None) -> list[Path]:
    return sync_range(config, start_date, end_date or date.today(), raw_name="body_measures_backfill.json")


def sync_range(config: AppConfig, start_date: date, end_date: date, *, raw_name: str) -> list[Path]:
    requests = _requests()

    with requests.Session() as session:
        access_token = get_access_token(session, config)
        measures = fetch_body_measures_windowed(session, access_token, start_date=start_date, end_date=end_date)

    return write_measures(config, measures, raw_name=raw_name, merge=True)


def get_access_token(session: Any, config: AppConfig) -> str:
    if config.withings.refresh_token:
        return refresh_access_token(session, config)
    if config.withings.access_token:
        return config.withings.access_token
    raise SystemExit(
        "Missing Withings credentials. Set withings.refresh_token in the config file, "
        "or set withings.access_token for a one-off run. Client id/secret alone cannot access user data."
    )


def refresh_access_token(session: Any, config: AppConfig) -> str:
    _require(config.withings.client_id, "withings.client_id")
    _require(config.withings.client_secret, "withings.client_secret")
    _require(config.withings.refresh_token, "withings.refresh_token")

    try:
        response = session.post(
            TOKEN_URL,
            data={
                "action": "requesttoken",
                "client_id": config.withings.client_id,
                "client_secret": config.withings.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": config.withings.refresh_token,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise SystemExit(f"Could not reach Withings token endpoint: {exc}") from exc
    body = _withings_body(response, "Withings token refresh failed")
    update_withings_tokens(config, body)
    return str(body["access_token"])


def fetch_body_measures(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    try:
        response = session.post(
            MEASURE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={
                "action": "getmeas",
                "category": 1,
                "meastypes": BODY_MEASURE_TYPE_IDS,
                "startdate": _start_timestamp(start_date),
                "enddate": _end_timestamp(end_date),
            },
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise SystemExit(f"Could not reach Withings measure endpoint: {exc}") from exc
    return _withings_body(response, "Withings measure request failed")


def fetch_body_measures_windowed(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    if start_date > end_date:
        raise SystemExit("Withings start date must be on or before end date.")

    measuregrps: list[dict[str, Any]] = []
    window_start = start_date
    while window_start <= end_date:
        window_end = min(window_start + timedelta(days=BACKFILL_WINDOW_DAYS - 1), end_date)
        body = fetch_body_measures(session, access_token, start_date=window_start, end_date=window_end)
        window_groups = body.get("measuregrps", [])
        if not isinstance(window_groups, list):
            raise SystemExit("Withings measure response did not contain measuregrps.")
        measuregrps.extend(window_groups)
        window_start = window_end + timedelta(days=1)
    return {"measuregrps": measuregrps}


def write_measures(
    config: AppConfig,
    body: dict[str, Any],
    *,
    raw_name: str = "body_measures.json",
    merge: bool = False,
) -> list[Path]:
    written_paths: list[Path] = []
    measuregrps = body.get("measuregrps", [])
    if not isinstance(measuregrps, list):
        raise SystemExit("Withings measure response did not contain measuregrps.")

    written_paths.append(write_json_file(config.withings.raw_dir / raw_name, body))
    rows = normalize_measure_groups(measuregrps)
    if merge:
        rows = merge_measure_rows(read_measure_rows(config.withings.measures_csv), rows)
    written_paths.append(write_csv_file(config.withings.measures_csv, rows, MEASURE_FIELDS))
    return written_paths


def read_measure_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def merge_measure_rows(
    existing_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in [*existing_rows, *new_rows]:
        rows_by_key[_measure_row_key(row)] = row
    return sorted(
        rows_by_key.values(),
        key=lambda row: (
            str(row.get("date", "")),
            str(row.get("datetime_local", "")),
            str(row.get("grpid", "")),
            str(row.get("type", "")),
        ),
    )


def _measure_row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("grpid", "")),
        str(row.get("type", "")),
        str(row.get("datetime_local", "")),
    )


def normalize_measure_groups(measuregrps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in measuregrps:
        timestamp = _int_or_zero(group.get("date"))
        local_datetime = datetime.fromtimestamp(timestamp).isoformat() if timestamp else ""
        for measure in group.get("measures", []):
            measure_type = _int_or_zero(measure.get("type"))
            type_name, unit_name = BODY_MEASURE_TYPES.get(measure_type, (f"type_{measure_type}", ""))
            rows.append(
                {
                    "grpid": group.get("grpid", ""),
                    "date": date.fromtimestamp(timestamp).isoformat() if timestamp else "",
                    "datetime_local": local_datetime,
                    "type": measure_type,
                    "type_name": type_name,
                    "value": _measure_value(measure),
                    "unit": unit_name,
                }
            )
    return rows


def _measure_value(measure: dict[str, Any]) -> str:
    value = _int_or_zero(measure.get("value"))
    unit = _int_or_zero(measure.get("unit"))
    return f"{value * (10 ** unit):.2f}"


def _withings_body(response: Any, prefix: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except Exception as exc:
        status_code = getattr(response, "status_code", "unknown")
        body = getattr(response, "text", "")
        raise SystemExit(f"{prefix} with HTTP {status_code}: {body}") from exc

    data = _json_response(response, f"{prefix}: response was not valid JSON.")
    status = data.get("status")
    if status != 0:
        raise SystemExit(f"{prefix} with Withings status {status}: {data.get('error', data)}")
    body = data.get("body", {})
    if not isinstance(body, dict):
        raise SystemExit(f"{prefix}: response body was not an object.")
    return body


def _start_timestamp(value: date) -> int:
    return int(datetime.combine(value, time.min).timestamp())


def _end_timestamp(value: date) -> int:
    return int(datetime.combine(value, time.max).timestamp())


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
