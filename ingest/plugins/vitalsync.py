from __future__ import annotations

import csv
import sys
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import typer

from ingest import prompts
from ingest.app_data import write_csv_file, write_json_file
from ingest.config import AppConfig, VitalsyncConfig, update_vitalsync_tokens
from ingest.plugins.contract import PluginCliRegistry, PluginManifest
from ingest.plugins.withings import SLEEP_FIELDS, merge_sleep_rows

TIMEOUT_SECONDS = 30
SLEEP_ANALYSIS = "sleep_analysis"
BLOOD_PRESSURE = "blood_pressure"
STEP_COUNT = "step_count"
STEP_FIELDS = [
    "source",
    "date",
    "step_count",
    "distance_km",
]
BP_FIELDS = [
    "source",
    "source_id",
    "date",
    "datetime_local",
    "systolic_mmHg",
    "diastolic_mmHg",
    "source_name",
    "source_bundle_id",
]
SLEEP_CATEGORIES = {
    "asleep_core": "core_min",
    "asleep_unspecified": "core_min",
    "asleep": "core_min",
    "asleep_deep": "deep_min",
    "asleep_rem": "rem_min",
    "awake": "awake_min",
    "in_bed": "time_in_bed_min",
}


class VitalsyncHTTPError(SystemExit):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def sync(config: AppConfig, *, end_date: date | None = None) -> list[Path]:
    if not config.vitalsync.enabled:
        print("Vitalsync sync skipped: plugin.vitalsync.enabled is false.", file=sys.stderr)
        return []
    target_end_date = end_date or datetime.now(config.timezone).date()
    start_date = _sync_start_date(config, target_end_date)
    if start_date > target_end_date:
        return []
    return sync_range(config, start_date, target_end_date, raw_name="sleep_analysis_sync.json")


def register_cli(registry: PluginCliRegistry) -> None:
    vitalsync_auth_app = typer.Typer(help="Vitalsync token helpers.")

    @registry.sync_app.command("vitalsync")
    def sync_vitalsync(ctx: typer.Context) -> None:
        registry.print_paths(registry.run_sync(registry.get_config(ctx), "vitalsync"))

    @vitalsync_auth_app.command("register-client")
    def auth_vitalsync_register_client(
        ctx: typer.Context,
        pairing_token: str | None = typer.Option(None, "--pairing-token", help="One-time Vitalsync pairing token."),
        client_label: str = typer.Option("ingest", "--client-label", help="Label stored by the Vitalsync receiver."),
    ) -> None:
        config = registry.get_config(ctx)
        resolved_pairing_token = pairing_token or prompts.password("Vitalsync pairing token")
        resolved_client_label = client_label or prompts.text("Vitalsync client label", default="ingest")
        register_client(
            config,
            pairing_token=resolved_pairing_token,
            client_label=resolved_client_label,
        )
        print(config.vitalsync.auth_state_path)

    @vitalsync_auth_app.command("refresh-token")
    def auth_vitalsync_refresh_token(ctx: typer.Context) -> None:
        config = registry.get_config(ctx)
        refresh_configured_access_token(config)
        print(config.vitalsync.auth_state_path)

    registry.auth_app.add_typer(vitalsync_auth_app, name="vitalsync")


def sync_unavailable_reason(config: AppConfig) -> str:
    if config.vitalsync.access_token:
        return ""
    if config.vitalsync.refresh_token and config.vitalsync.client_id:
        return ""
    return f"run `ingest auth vitalsync register-client`; missing auth state at {config.vitalsync.auth_state_path}"


manifest = PluginManifest(
    name="vitalsync",
    provides=(
        "recovery.sleep.start_time",
        "recovery.sleep.end_time",
        "recovery.sleep.time_in_bed_min",
        "recovery.sleep.awake_min",
        "recovery.sleep.deep_sleep_min",
        "recovery.sleep.rem_sleep_min",
        "measurement.steps",
        "measurement.blood_pressure.systolic_mmHg",
        "measurement.blood_pressure.diastolic_mmHg",
    ),
    sync=sync,
    sync_unavailable_reason=sync_unavailable_reason,
    register_cli=register_cli,
    serial_sync=True,
)


def sync_range(config: AppConfig, start_date: date, end_date: date, *, raw_name: str) -> list[Path]:
    if start_date > end_date:
        return []
    requests = _requests()
    with requests.Session() as session:
        access_token = get_access_token(session, config)
        sleep_records, access_token = fetch_records_with_refresh(
            session,
            config,
            access_token,
            sample_type=SLEEP_ANALYSIS,
            start_date=start_date,
            end_date=end_date,
            local_timezone=config.timezone,
        )
        blood_pressure_records, access_token = fetch_records_with_refresh(
            session,
            config,
            access_token,
            sample_type=BLOOD_PRESSURE,
            start_date=start_date,
            end_date=end_date,
            local_timezone=config.timezone,
        )
        step_records, access_token = fetch_records_with_refresh(
            session,
            config,
            access_token,
            sample_type=STEP_COUNT,
            start_date=start_date,
            end_date=end_date,
            local_timezone=config.timezone,
        )

    written_paths: list[Path] = []
    raw_path = write_json_file(config.vitalsync.raw_dir / raw_name, {"records": sleep_records})
    written_paths.append(raw_path)
    rows = normalize_sleep_analysis_records(
        sleep_records,
        local_timezone=config.timezone,
        source_bundle_id=config.vitalsync.source_bundle_id,
    )
    existing_rows = read_sleep_rows(config.vitalsync.sleep_csv)
    sleep_path = write_csv_file(
        config.vitalsync.sleep_csv,
        merge_sleep_rows(existing_rows, rows),
        SLEEP_FIELDS,
    )
    written_paths.append(sleep_path)

    blood_pressure_raw_name = raw_name.replace("sleep_analysis", "blood_pressure")
    blood_pressure_raw_path = write_json_file(
        config.vitalsync.raw_dir / blood_pressure_raw_name,
        {"records": blood_pressure_records},
    )
    written_paths.append(blood_pressure_raw_path)
    blood_pressure_rows = normalize_blood_pressure_records(
        blood_pressure_records,
        local_timezone=config.timezone,
    )
    existing_blood_pressure_rows = read_blood_pressure_rows(config.vitalsync.blood_pressure_csv)
    blood_pressure_path = write_csv_file(
        config.vitalsync.blood_pressure_csv,
        merge_blood_pressure_rows(existing_blood_pressure_rows, blood_pressure_rows),
        BP_FIELDS,
    )
    written_paths.append(blood_pressure_path)

    step_raw_name = raw_name.replace("sleep_analysis", "step_count")
    step_raw_path = write_json_file(
        config.vitalsync.raw_dir / step_raw_name,
        {"records": step_records},
    )
    written_paths.append(step_raw_path)
    step_rows = normalize_step_count_records(
        step_records,
        local_timezone=config.timezone,
    )
    existing_step_rows = read_step_rows(config.vitalsync.steps_csv)
    step_path = write_csv_file(
        config.vitalsync.steps_csv,
        merge_step_rows(existing_step_rows, step_rows),
        STEP_FIELDS,
    )
    written_paths.append(step_path)
    return written_paths


def get_access_token(session: Any, config: AppConfig) -> str:
    if config.vitalsync.access_token and (
        not config.vitalsync.refresh_token or _token_current(config.vitalsync.expires_at)
    ):
        return config.vitalsync.access_token
    if not config.vitalsync.refresh_token:
        raise SystemExit(
            "Missing Vitalsync auth state. Run `ingest auth vitalsync register-client`."
        )
    if not config.vitalsync.client_id:
        raise SystemExit("Missing Vitalsync client id in auth state. Run `ingest auth vitalsync register-client`.")
    return refresh_app_access_token(session, config)


def register_client(config: AppConfig, *, pairing_token: str, client_label: str = "") -> dict[str, Any]:
    requests = _requests()
    with requests.Session() as session:
        try:
            response = session.post(
                f"{config.vitalsync.endpoint}/clients/register",
                json={
                    "schema": "vitalsync.client_registration.v1",
                    "pairing_token": pairing_token,
                    "client_type": "ingest",
                    "client_label": client_label,
                },
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SystemExit(f"Could not reach Vitalsync client registration endpoint: {exc}") from exc
    token = _vitalsync_body(response, "Vitalsync client registration failed")
    update_vitalsync_tokens(config, token)
    return token


def refresh_configured_access_token(config: AppConfig) -> dict[str, Any]:
    if not config.vitalsync.refresh_token:
        raise SystemExit("Missing Vitalsync refresh token in auth state. Run `ingest auth vitalsync register-client`.")
    if not config.vitalsync.client_id:
        raise SystemExit("Missing Vitalsync client id in auth state. Run `ingest auth vitalsync register-client`.")
    requests = _requests()
    with requests.Session() as session:
        token = refresh_access_token(session, config.vitalsync)
    update_vitalsync_tokens(config, token)
    return token


def refresh_app_access_token(session: Any, config: AppConfig) -> str:
    token = refresh_access_token(session, config.vitalsync)
    update_vitalsync_tokens(config, token)
    return str(token["access_token"])


def refresh_access_token(session: Any, config: VitalsyncConfig) -> dict[str, Any]:
    try:
        response = session.post(
            f"{config.endpoint}/tokens/refresh",
            json={"refresh_token": config.refresh_token, "client_id": config.client_id},
            timeout=TIMEOUT_SECONDS,
        )
    except _requests().RequestException as exc:
        raise SystemExit(f"Could not reach Vitalsync token endpoint: {exc}") from exc
    return _vitalsync_body(response, "Vitalsync token refresh failed")


def fetch_records_with_refresh(
    session: Any,
    config: AppConfig,
    access_token: str,
    *,
    sample_type: str,
    start_date: date,
    end_date: date,
    local_timezone: ZoneInfo,
) -> tuple[list[dict[str, Any]], str]:
    try:
        records = fetch_records(
            session,
            config.vitalsync,
            access_token,
            sample_type=sample_type,
            start_date=start_date,
            end_date=end_date,
            local_timezone=local_timezone,
        )
        return records, access_token
    except VitalsyncHTTPError as exc:
        if exc.status_code != 401 or not config.vitalsync.refresh_token:
            raise
    if not config.vitalsync.client_id:
        raise SystemExit("Missing Vitalsync client id in auth state. Run `ingest auth vitalsync register-client`.")
    refreshed_access_token = refresh_app_access_token(session, config)
    records = fetch_records(
        session,
        config.vitalsync,
        refreshed_access_token,
        sample_type=sample_type,
        start_date=start_date,
        end_date=end_date,
        local_timezone=local_timezone,
    )
    return records, refreshed_access_token


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
        url = f"{config.endpoint}/records?{urlencode(params)}"
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


def normalize_blood_pressure_records(
    records: list[dict[str, Any]],
    *,
    local_timezone: ZoneInfo,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.get("sample_type") != BLOOD_PRESSURE:
            continue
        start_time = _parse_timestamp(record.get("start_time"))
        if start_time is None:
            continue
        value = record.get("value")
        if not isinstance(value, dict):
            continue
        systolic = _optional_pressure(value.get("systolic"))
        diastolic = _optional_pressure(value.get("diastolic"))
        if not systolic or not diastolic:
            continue
        local_time = start_time.astimezone(local_timezone)
        rows.append(
            {
                "source": "vitalsync",
                "source_id": str(record.get("source_id") or ""),
                "date": local_time.date().isoformat(),
                "datetime_local": local_time.isoformat(),
                "systolic_mmHg": systolic,
                "diastolic_mmHg": diastolic,
                "source_name": str(record.get("source_name") or ""),
                "source_bundle_id": str(record.get("source_bundle_id") or ""),
            }
        )
    return sorted(rows, key=lambda row: (str(row["datetime_local"]), str(row["source_id"])))


def normalize_step_count_records(
    records: list[dict[str, Any]],
    *,
    local_timezone: ZoneInfo,
) -> list[dict[str, Any]]:
    steps_by_date: dict[str, int] = {}
    for record in records:
        if record.get("sample_type") != STEP_COUNT:
            continue
        start_time = _parse_timestamp(record.get("start_time"))
        if start_time is None:
            continue
        value = record.get("value")
        if not isinstance(value, dict):
            continue
        quantity = _optional_int(value.get("quantity"))
        if quantity is None:
            continue
        local_date = start_time.astimezone(local_timezone).date().isoformat()
        steps_by_date[local_date] = steps_by_date.get(local_date, 0) + quantity
    return [
        {
            "source": "vitalsync",
            "date": local_date,
            "step_count": str(step_count),
            "distance_km": "",
        }
        for local_date, step_count in sorted(steps_by_date.items())
    ]


def read_sleep_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_blood_pressure_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_step_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def merge_blood_pressure_rows(
    existing_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in [*existing_rows, *new_rows]:
        rows_by_key[(str(row.get("source", "")), str(row.get("source_id", "")))] = row
    return sorted(
        rows_by_key.values(),
        key=lambda row: (
            str(row.get("datetime_local", "")),
            str(row.get("source", "")),
            str(row.get("source_id", "")),
        ),
    )


def merge_step_rows(
    existing_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in [*existing_rows, *new_rows]:
        rows_by_key[(str(row.get("source", "")), str(row.get("date", "")))] = row
    return sorted(rows_by_key.values(), key=lambda row: (str(row.get("date", "")), str(row.get("source", ""))))


def _sync_start_date(config: AppConfig, end_date: date) -> date:
    fallback = end_date - timedelta(days=config.vitalsync.days - 1)
    latest_dates = [
        latest_sleep_date(read_sleep_rows(config.vitalsync.sleep_csv)),
        latest_blood_pressure_date(read_blood_pressure_rows(config.vitalsync.blood_pressure_csv)),
        latest_step_date(read_step_rows(config.vitalsync.steps_csv)),
    ]
    return min(value or fallback for value in latest_dates)


def latest_sleep_date(rows: Iterable[dict[str, str]]) -> date | None:
    dates: list[date] = []
    for row in rows:
        try:
            dates.append(date.fromisoformat(row.get("wake_date", "")))
        except ValueError:
            pass
    return max(dates) if dates else None


def latest_blood_pressure_date(rows: Iterable[dict[str, str]]) -> date | None:
    dates: list[date] = []
    for row in rows:
        try:
            dates.append(date.fromisoformat(row.get("date", "")))
        except ValueError:
            pass
    return max(dates) if dates else None


def latest_step_date(rows: Iterable[dict[str, str]]) -> date | None:
    dates: list[date] = []
    for row in rows:
        try:
            dates.append(date.fromisoformat(row.get("date", "")))
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


def _optional_pressure(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return str(round(number))


def _optional_int(value: Any) -> int | None:
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _parse_expires_at(value: str) -> datetime | None:
    if not value:
        return None
    try:
        if value.isdecimal():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            return expires_at.replace(tzinfo=timezone.utc)
        return expires_at
    except ValueError:
        return None


def _token_current(value: str) -> bool:
    expires_at = _parse_expires_at(value)
    return expires_at is not None and expires_at > datetime.now(timezone.utc) + timedelta(seconds=30)


def _token_expired(value: str) -> bool:
    expires_at = _parse_expires_at(value)
    if expires_at is None:
        return False
    return expires_at <= datetime.now(timezone.utc) + timedelta(seconds=30)


def _vitalsync_body(response: Any, prefix: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise SystemExit(f"{prefix}: response was not JSON.") from exc
    if response.status_code >= 400:
        message = data.get("message") if isinstance(data, dict) else data
        raise VitalsyncHTTPError(f"{prefix} with HTTP {response.status_code}: {message}", response.status_code)
    if not isinstance(data, dict):
        raise SystemExit(f"{prefix}: response was not an object.")
    return data


def _requests() -> Any:
    import requests

    return requests
