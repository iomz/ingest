from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ingest.app_data import write_csv_file, write_json_file
from ingest.config import AppConfig, VitalsyncConfig, update_vitalsync_tokens
from ingest.sources.withings import SLEEP_FIELDS, merge_sleep_rows

TIMEOUT_SECONDS = 30
SLEEP_ANALYSIS = "sleep_analysis"
SLEEP_CATEGORIES = {
    "asleep_core": "core_min",
    "asleep_unspecified": "core_min",
    "asleep": "core_min",
    "asleep_deep": "deep_min",
    "asleep_rem": "rem_min",
    "awake": "awake_min",
    "in_bed": "time_in_bed_min",
}


def sync(config: AppConfig, *, end_date: date | None = None) -> list[Path]:
    if not config.vitalsync.enabled:
        return []
    target_end_date = end_date or datetime.now(config.timezone).date()
    start_date = _sync_start_date(config, target_end_date)
    if start_date > target_end_date:
        return []
    return sync_range(config, start_date, target_end_date, raw_name="sleep_analysis_sync.json")


def sync_range(config: AppConfig, start_date: date, end_date: date, *, raw_name: str) -> list[Path]:
    if start_date > end_date:
        return []
    requests = _requests()
    with requests.Session() as session:
        access_token = get_access_token(session, config)
        records = fetch_records(
            session,
            config.vitalsync,
            access_token,
            sample_type=SLEEP_ANALYSIS,
            start_date=start_date,
            end_date=end_date,
            local_timezone=config.timezone,
        )
    raw_path = write_json_file(config.vitalsync.raw_dir / raw_name, {"records": records})
    rows = normalize_sleep_analysis_records(
        records,
        local_timezone=config.timezone,
        source_bundle_id=config.vitalsync.source_bundle_id,
    )
    if not rows:
        return [raw_path]
    existing_rows = read_sleep_rows(config.vitalsync.sleep_csv)
    sleep_path = write_csv_file(
        config.vitalsync.sleep_csv,
        merge_sleep_rows(existing_rows, rows),
        SLEEP_FIELDS,
    )
    return [raw_path, sleep_path]


def get_access_token(session: Any, config: AppConfig) -> str:
    if config.vitalsync.access_token and not _token_expired(config.vitalsync.expires_at):
        return config.vitalsync.access_token
    if not config.vitalsync.refresh_token:
        raise SystemExit(
            "Missing Vitalsync access token. Set vitalsync.access_token in the config file, "
            "or set vitalsync.refresh_token and vitalsync.client_id."
        )
    if not config.vitalsync.client_id:
        raise SystemExit("Missing Vitalsync client_id for refresh token flow.")
    token = refresh_access_token(session, config.vitalsync)
    update_vitalsync_tokens(config, token)
    return str(token["access_token"])


def refresh_access_token(session: Any, config: VitalsyncConfig) -> dict[str, Any]:
    try:
        response = session.post(
            f"{config.base_url}/tokens/refresh",
            json={"refresh_token": config.refresh_token, "client_id": config.client_id},
            timeout=TIMEOUT_SECONDS,
        )
    except _requests().RequestException as exc:
        raise SystemExit(f"Could not reach Vitalsync token endpoint: {exc}") from exc
    return _vitalsync_body(response, "Vitalsync token refresh failed")


def fetch_records(
    session: Any,
    config: VitalsyncConfig,
    access_token: str,
    *,
    sample_type: str,
    start_date: date,
    end_date: date,
    local_timezone: ZoneInfo,
) -> list[dict[str, Any]]:
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=local_timezone)
    end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=local_timezone)
    cursor = ""
    records: list[dict[str, Any]] = []
    while True:
        params = {
            "sample_type": sample_type,
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "limit": "1000",
        }
        if cursor:
            params["cursor"] = cursor
        url = f"{config.base_url}/records?{urlencode(params)}"
        try:
            response = session.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=TIMEOUT_SECONDS,
            )
        except _requests().RequestException as exc:
            raise SystemExit(f"Could not reach Vitalsync records endpoint: {exc}") from exc
        body = _vitalsync_body(response, "Vitalsync records request failed")
        page_records = body.get("records")
        if not isinstance(page_records, list):
            raise SystemExit("Vitalsync records response did not contain records.")
        records.extend(page_records)
        cursor = str(body.get("next_cursor") or "")
        if not cursor:
            return records


def normalize_sleep_analysis_records(
    records: list[dict[str, Any]],
    *,
    local_timezone: ZoneInfo,
    source_bundle_id: str = "com.lexwarelabs.goodmorning",
) -> list[dict[str, Any]]:
    sessions: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        if record.get("sample_type") != SLEEP_ANALYSIS:
            continue
        if source_bundle_id and record.get("source_bundle_id") != source_bundle_id:
            continue
        start_time = _parse_timestamp(record.get("start_time"))
        end_time = _parse_timestamp(record.get("end_time"))
        if start_time is None or end_time is None or end_time <= start_time:
            continue
        wake_date = end_time.astimezone(local_timezone).date().isoformat()
        key = (
            wake_date,
            str(record.get("source_bundle_id") or ""),
            str(record.get("source_name") or ""),
        )
        sessions.setdefault(key, []).append(record)

    rows: list[dict[str, Any]] = []
    for (wake_date, bundle_id, source_name), session_records in sessions.items():
        intervals_by_metric: dict[str, list[tuple[datetime, datetime]]] = {}
        awake_count = 0
        for record in session_records:
            metric = SLEEP_CATEGORIES.get(_sleep_category(record))
            if metric is None:
                continue
            start_time = _parse_timestamp(record.get("start_time"))
            end_time = _parse_timestamp(record.get("end_time"))
            if start_time is None or end_time is None or end_time <= start_time:
                continue
            intervals_by_metric.setdefault(metric, []).append((start_time, end_time))
            if metric == "awake_min":
                awake_count += 1
        if not intervals_by_metric:
            continue
        all_intervals = [
            interval
            for intervals in intervals_by_metric.values()
            for interval in intervals
        ]
        start_time = min(start for start, _end in all_intervals).astimezone(local_timezone)
        end_time = max(end for _start, end in all_intervals).astimezone(local_timezone)
        core_min = _interval_minutes(intervals_by_metric.get("core_min", []))
        deep_min = _interval_minutes(intervals_by_metric.get("deep_min", []))
        rem_min = _interval_minutes(intervals_by_metric.get("rem_min", []))
        sleep_min = core_min + deep_min + rem_min
        awake_min = _interval_minutes(intervals_by_metric.get("awake_min", []))
        time_in_bed_min = _interval_minutes(intervals_by_metric.get("time_in_bed_min", []))
        if time_in_bed_min <= 0:
            time_in_bed_min = sleep_min + awake_min
        source_id = f"{bundle_id or source_name}:{wake_date}:{start_time.isoformat()}:{end_time.isoformat()}"
        rows.append(
            {
                "source": "vitalsync",
                "source_id": source_id,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "timezone": str(local_timezone),
                "wake_date": wake_date,
                "total_sleep_min": _minutes(sleep_min),
                "time_in_bed_min": _minutes(time_in_bed_min),
                "awake_min": _minutes(awake_min),
                "awake_count": str(awake_count),
                "sleep_score": "",
                "sleep_efficiency": _ratio(sleep_min, time_in_bed_min),
                "light_sleep_min": _minutes(core_min),
                "deep_sleep_min": _minutes(deep_min),
                "rem_sleep_min": _minutes(rem_min),
                "source_name": source_name,
                "source_bundle_id": bundle_id,
            }
        )
    return sorted(rows, key=lambda row: (str(row["wake_date"]), str(row["end_time"])))


def read_sleep_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _sync_start_date(config: AppConfig, end_date: date) -> date:
    latest = latest_sleep_date(read_sleep_rows(config.vitalsync.sleep_csv))
    if latest is None:
        return end_date - timedelta(days=config.vitalsync.days - 1)
    return latest


def latest_sleep_date(rows: Iterable[dict[str, str]]) -> date | None:
    dates: list[date] = []
    for row in rows:
        try:
            dates.append(date.fromisoformat(row.get("wake_date", "")))
        except ValueError:
            pass
    return max(dates) if dates else None


def _interval_minutes(intervals: list[tuple[datetime, datetime]]) -> float:
    return sum((end - start).total_seconds() for start, end in _merge_intervals(intervals)) / 60


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _sleep_category(record: dict[str, Any]) -> str:
    value = record.get("value")
    if not isinstance(value, dict):
        return ""
    return str(value.get("category") or "").strip()


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _minutes(value: float) -> str:
    return f"{value:.2f}"


def _ratio(numerator: float, denominator: float) -> str:
    return f"{numerator / denominator:.2f}" if denominator > 0 else ""


def _token_expired(value: str) -> bool:
    if not value:
        return False
    try:
        if value.isdecimal():
            expires_at = datetime.fromtimestamp(int(value), tz=timezone.utc)
        else:
            expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return expires_at <= datetime.now(timezone.utc) + timedelta(seconds=30)


def _vitalsync_body(response: Any, prefix: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise SystemExit(f"{prefix}: response was not JSON.") from exc
    if response.status_code >= 400:
        message = data.get("message") if isinstance(data, dict) else data
        raise SystemExit(f"{prefix} with HTTP {response.status_code}: {message}")
    if not isinstance(data, dict):
        raise SystemExit(f"{prefix}: response was not an object.")
    return data


def _requests() -> Any:
    import requests

    return requests
