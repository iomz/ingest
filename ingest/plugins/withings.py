from __future__ import annotations

import csv
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ingest.activities import DEFAULT_TIMEZONE
from ingest.app_data import write_csv_file, write_json_file
from ingest.config import AppConfig, update_withings_auth_state, update_withings_tokens

TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
WORKOUT_URL = "https://wbsapi.withings.net/v2/measure"
SLEEP_URL = "https://wbsapi.withings.net/v2/sleep"
TIMEOUT_SECONDS = 30
BACKFILL_WINDOW_DAYS = 90
WITHINGS_SCOPES = "user.metrics,user.activity"

BODY_MEASURE_TYPES = {
    1: ("weight", "kg"),
    4: ("height", "m"),
    5: ("fat_free_mass", "kg"),
    6: ("fat_ratio", "%"),
    8: ("fat_mass_weight", "kg"),
    9: ("diastolic_blood_pressure", "mmHg"),
    10: ("systolic_blood_pressure", "mmHg"),
    76: ("muscle_mass", "kg"),
    77: ("hydration", "kg"),
    88: ("bone_mass", "kg"),
    91: ("pulse_wave_velocity", "m/s"),
}
BODY_MEASURE_TYPE_IDS = ",".join(str(measure_type) for measure_type in BODY_MEASURE_TYPES)
HEIGHT_MEASURE_TYPE_ID = "4"
HEIGHT_LOOKBACK_START = date(2010, 1, 1)

MEASURE_FIELDS = [
    "grpid",
    "date",
    "datetime_local",
    "type",
    "type_name",
    "value",
    "unit",
]

ACTIVITY_FIELDS = [
    "date",
    "step_count",
    "distance_km",
]

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
]

SLEEP_FIELDS = [
    "source",
    "source_id",
    "start_time",
    "end_time",
    "timezone",
    "wake_date",
    "total_sleep_min",
    "time_in_bed_min",
    "awake_min",
    "awake_count",
    "sleep_score",
    "sleep_efficiency",
    "light_sleep_min",
    "deep_sleep_min",
    "rem_sleep_min",
]

WORKOUT_CATEGORIES = {
    1: "walk",
    2: "run",
    3: "hike",
    5: "bmx",
    6: "ride",
    7: "swim",
    8: "surf",
}
IGNORED_WORKOUT_CATEGORIES = {16}


def sync(config: AppConfig, *, end_date: date | None = None) -> list[Path]:
    target_end_date = end_date or datetime.now(config.timezone).date()
    start_date = _sync_cursor_date(config, target_end_date)
    if start_date > target_end_date:
        return []
    return sync_range(config, start_date, target_end_date, raw_name="body_measures_sync.json")


def _sync_cursor_date(config: AppConfig, end_date: date) -> date:
    latest_date = lagging_local_date(config)
    if latest_date is None:
        return end_date - timedelta(days=config.withings.days - 1)
    return latest_date


def backfill(config: AppConfig, *, start_date: date, end_date: date | None = None) -> list[Path]:
    target_end_date = end_date or datetime.now(config.timezone).date()
    if start_date > target_end_date:
        return []
    return sync_range(config, start_date, target_end_date, raw_name="body_measures_backfill.json")


def lagging_local_date(config: AppConfig) -> date | None:
    latest_dates = [
        latest_measure_date(read_measure_rows(config.withings.measures_csv)),
        latest_activity_date(read_activity_rows(config.withings.activity_csv)),
        latest_workout_date(read_workout_rows(config.withings.workouts_csv)),
        latest_sleep_date(read_sleep_rows(config.withings.sleep_csv)),
    ]
    present_dates = [value for value in latest_dates if value is not None]
    if not present_dates:
        return None
    return min(present_dates)


def sync_range(config: AppConfig, start_date: date, end_date: date, *, raw_name: str) -> list[Path]:
    if start_date > end_date:
        return []
    requests = _requests()

    with requests.Session() as session:
        access_token = get_access_token(session, config)
        measures = fetch_body_measures_windowed(
            session,
            access_token,
            start_date=start_date,
            end_date=end_date,
            local_timezone=config.timezone,
        )
        height = {"measuregrps": []}
        if not has_cached_height(config.withings.measures_csv):
            height = fetch_latest_height(
                session,
                access_token,
                end_date=end_date,
                local_timezone=config.timezone,
            )
        activity = fetch_activity_windowed(
            session,
            access_token,
            start_date=start_date,
            end_date=end_date,
        )
        workouts = fetch_workouts_windowed_if_available(
            session,
            access_token,
            start_date=start_date,
            end_date=end_date,
        )
        sleep = fetch_sleep_summaries_windowed(
            session,
            access_token,
            start_date=start_date,
            end_date=end_date,
            local_timezone=config.timezone,
        )

    measures = _with_latest_height(measures, height)
    written_paths = write_measures(config, measures, raw_name=raw_name, merge=True)
    activity_raw_name = raw_name.replace("body_measures", "activity")
    written_paths.extend(write_activity(config, activity, raw_name=activity_raw_name, merge=True))
    workout_raw_name = raw_name.replace("body_measures", "workouts")
    written_paths.extend(write_workouts(config, workouts, raw_name=workout_raw_name, merge=True))
    sleep_raw_name = raw_name.replace("body_measures", "sleep")
    written_paths.extend(write_sleep(config, sleep, raw_name=sleep_raw_name, merge=True))
    return written_paths


def get_access_token(session: Any, config: AppConfig) -> str:
    if config.withings.refresh_token:
        return refresh_access_token(session, config)
    if config.withings.access_token:
        return config.withings.access_token
    raise SystemExit(
        "Missing Withings auth state. Run `ingest auth withings auth-url`, then "
        "`ingest auth withings exchange-code`."
    )


def authorization_url(config: AppConfig, *, redirect_uri: str, state: str = "ingest", client_id: str = "") -> str:
    resolved_client_id = client_id or config.withings.client_id
    _require(resolved_client_id, "Withings client id")
    update_withings_auth_state(config, client_id=resolved_client_id)
    return (
        AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": resolved_client_id,
                "redirect_uri": redirect_uri,
                "scope": WITHINGS_SCOPES,
                "state": state,
            }
        )
    )


def exchange_authorization_code(
    config: AppConfig,
    *,
    code: str,
    redirect_uri: str,
    client_id: str = "",
    client_secret: str = "",
) -> None:
    requests = _requests()
    resolved_client_id = client_id or config.withings.client_id
    resolved_client_secret = client_secret or config.withings.client_secret
    _require(resolved_client_id, "Withings client id")
    _require(resolved_client_secret, "Withings client secret")
    update_withings_auth_state(
        config,
        client_id=resolved_client_id,
        client_secret=resolved_client_secret,
    )
    with requests.Session() as session:
        response = session.post(
            TOKEN_URL,
            data={
                "action": "requesttoken",
                "client_id": resolved_client_id,
                "client_secret": resolved_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=TIMEOUT_SECONDS,
        )
    body = _withings_body(response, "Withings authorization code exchange failed")
    update_withings_tokens(config, body)


def refresh_access_token(session: Any, config: AppConfig) -> str:
    _require(config.withings.client_id, "Withings client id in auth state")
    _require(config.withings.client_secret, "Withings client secret in auth state")
    _require(config.withings.refresh_token, "Withings refresh token in auth state")

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
    meastypes: str = BODY_MEASURE_TYPE_IDS,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    try:
        response = session.post(
            MEASURE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={
                "action": "getmeas",
                "category": 1,
                "meastypes": meastypes,
                "startdate": _start_timestamp(start_date, local_timezone),
                "enddate": _end_timestamp(end_date, local_timezone),
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
    meastypes: str = BODY_MEASURE_TYPE_IDS,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    if start_date > end_date:
        raise SystemExit("Withings start date must be on or before end date.")

    measuregrps: list[dict[str, Any]] = []
    window_start = start_date
    while window_start <= end_date:
        window_end = min(window_start + timedelta(days=BACKFILL_WINDOW_DAYS - 1), end_date)
        body = fetch_body_measures(
            session,
            access_token,
            start_date=window_start,
            end_date=window_end,
            meastypes=meastypes,
            local_timezone=local_timezone,
        )
        window_groups = body.get("measuregrps", [])
        if not isinstance(window_groups, list):
            raise SystemExit("Withings measure response did not contain measuregrps.")
        measuregrps.extend(window_groups)
        window_start = window_end + timedelta(days=1)
    return {"measuregrps": measuregrps}


def fetch_latest_height(
    session: Any,
    access_token: str,
    *,
    end_date: date,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    body = fetch_body_measures_windowed(
        session,
        access_token,
        start_date=HEIGHT_LOOKBACK_START,
        end_date=end_date,
        meastypes=HEIGHT_MEASURE_TYPE_ID,
        local_timezone=local_timezone,
    )
    measuregrps = body.get("measuregrps", [])
    if not isinstance(measuregrps, list):
        raise SystemExit("Withings height response did not contain measuregrps.")
    height_groups = [
        group
        for group in measuregrps
        if any(_int_or_zero(measure.get("type")) == 4 for measure in group.get("measures", []))
    ]
    if not height_groups:
        return {"measuregrps": []}
    latest_group = max(
        height_groups,
        key=lambda group: (
            _int_or_zero(group.get("date")),
            _int_or_zero(group.get("grpid")),
        ),
    )
    return {"measuregrps": [_height_only_group(latest_group)]}


def _with_latest_height(measures: dict[str, Any], height: dict[str, Any]) -> dict[str, Any]:
    measuregrps = measures.get("measuregrps", [])
    height_groups = height.get("measuregrps", [])
    if not isinstance(measuregrps, list):
        raise SystemExit("Withings measure response did not contain measuregrps.")
    if not isinstance(height_groups, list):
        raise SystemExit("Withings height response did not contain measuregrps.")
    return {"measuregrps": [*measuregrps, *height_groups]}


def _height_only_group(group: dict[str, Any]) -> dict[str, Any]:
    height_measures = [
        measure
        for measure in group.get("measures", [])
        if _int_or_zero(measure.get("type")) == 4
    ]
    return {**group, "measures": height_measures}


def fetch_workouts(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
    offset: int = 0,
) -> dict[str, Any]:
    try:
        data = {
            "action": "getworkouts",
            "startdateymd": start_date.isoformat(),
            "enddateymd": end_date.isoformat(),
            "data_fields": ",".join(
                [
                    "calories",
                    "manual_calories",
                    "distance",
                    "manual_distance",
                    "effduration",
                    "steps",
                    "pool_laps",
                    "strokes",
                    "pool_length",
                    "algo_pause_duration",
                ]
            ),
        }
        if offset:
            data["offset"] = str(offset)
        response = session.post(
            WORKOUT_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data=data,
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise SystemExit(f"Could not reach Withings workouts endpoint: {exc}") from exc
    return _withings_body(response, "Withings workouts request failed")


def fetch_activity(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    try:
        response = session.post(
            WORKOUT_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={
                "action": "getactivity",
                "startdateymd": start_date.isoformat(),
                "enddateymd": end_date.isoformat(),
                "data_fields": "steps,distance",
            },
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise SystemExit(f"Could not reach Withings activity endpoint: {exc}") from exc
    return _withings_body(response, "Withings activity request failed")


def fetch_sleep_summaries(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
    offset: int = 0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "getsummary",
        "startdateymd": start_date.isoformat(),
        "enddateymd": end_date.isoformat(),
        "data_fields": ",".join(
            [
                "total_sleep_time",
                "total_timeinbed",
                "asleepduration",
                "wakeupduration",
                "wakeupcount",
                "sleep_score",
                "sleep_efficiency",
                "lightsleepduration",
                "deepsleepduration",
                "remsleepduration",
            ]
        ),
    }
    if offset:
        payload["offset"] = offset

    try:
        response = session.post(
            SLEEP_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data=payload,
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise SystemExit(f"Could not reach Withings sleep endpoint: {exc}") from exc
    return _withings_body(response, "Withings sleep request failed")


def fetch_activity_windowed(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    if start_date > end_date:
        raise SystemExit("Withings start date must be on or before end date.")

    activities: list[dict[str, Any]] = []
    window_start = start_date
    while window_start <= end_date:
        window_end = min(window_start + timedelta(days=BACKFILL_WINDOW_DAYS - 1), end_date)
        body = fetch_activity(session, access_token, start_date=window_start, end_date=window_end)
        window_activities = body.get("activities", [])
        if not isinstance(window_activities, list):
            raise SystemExit("Withings activity response did not contain activities.")
        activities.extend(window_activities)
        window_start = window_end + timedelta(days=1)
    return {"activities": activities}


def fetch_sleep_summaries_windowed(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    if start_date > end_date:
        raise SystemExit("Withings start date must be on or before end date.")

    series: list[dict[str, Any]] = []
    window_start = start_date
    while window_start <= end_date:
        window_end = min(window_start + timedelta(days=BACKFILL_WINDOW_DAYS - 1), end_date)
        offset = 0
        while True:
            body = fetch_sleep_summaries(
                session,
                access_token,
                start_date=window_start,
                end_date=window_end,
                offset=offset,
            )
            window_series = body.get("series", [])
            if not isinstance(window_series, list):
                raise SystemExit("Withings sleep response did not contain series.")
            series.extend(window_series)
            if not body.get("more"):
                break
            next_offset = _int_or_zero(body.get("offset"))
            if next_offset <= offset:
                raise SystemExit("Withings sleep response requested pagination without a valid next offset.")
            offset = next_offset
        window_start = window_end + timedelta(days=1)
    summary_dates = {
        datetime.fromtimestamp(_int_or_zero(summary.get("enddate")), tz=local_timezone).date()
        for summary in series
        if _int_or_zero(summary.get("enddate"))
    }
    fallback_series: list[dict[str, Any]] = []
    wake_date = start_date
    while wake_date <= end_date:
        if wake_date not in summary_dates:
            fallback_series.extend(
                fetch_sleep_states_as_summaries(
                    session,
                    access_token,
                    start_date=wake_date,
                    end_date=wake_date,
                    local_timezone=local_timezone,
                )
            )
        wake_date += timedelta(days=1)
    body: dict[str, Any] = {"series": [*series, *fallback_series]}
    if fallback_series:
        body["fallback_source"] = "sleep_get"
    return body


def fetch_sleep_states_as_summaries(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    wake_date = start_date
    while wake_date <= end_date:
        window_start = datetime.combine(
            wake_date - timedelta(days=1),
            time(12),
            tzinfo=local_timezone,
        )
        window_end = datetime.combine(
            wake_date,
            time(11, 59, 59),
            tzinfo=local_timezone,
        )
        states = fetch_sleep_states(
            session,
            access_token,
            start_timestamp=int(window_start.timestamp()),
            end_timestamp=int(window_end.timestamp()),
        )
        summary = summarize_sleep_states(states, wake_date, local_timezone)
        if summary is not None:
            summaries.append(summary)
        wake_date += timedelta(days=1)
    return summaries


def fetch_sleep_states(
    session: Any,
    access_token: str,
    *,
    start_timestamp: int,
    end_timestamp: int,
) -> list[dict[str, Any]]:
    try:
        response = session.post(
            SLEEP_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={
                "action": "get",
                "startdate": start_timestamp,
                "enddate": end_timestamp,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise SystemExit(f"Could not reach Withings sleep endpoint: {exc}") from exc
    body = _withings_body(response, "Withings sleep states request failed")
    series = body.get("series", [])
    if not isinstance(series, list):
        raise SystemExit("Withings sleep states response did not contain series.")
    return series


def summarize_sleep_states(
    states: list[dict[str, Any]],
    wake_date: date,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> dict[str, Any] | None:
    valid_states = [
        state
        for state in states
        if _int_or_zero(state.get("startdate"))
        and _int_or_zero(state.get("enddate")) > _int_or_zero(state.get("startdate"))
    ]
    if not valid_states:
        return None

    start_timestamp = min(_int_or_zero(state.get("startdate")) for state in valid_states)
    end_timestamp = max(_int_or_zero(state.get("enddate")) for state in valid_states)
    if datetime.fromtimestamp(end_timestamp, tz=local_timezone).date() != wake_date:
        return None

    durations = {
        sleep_state: sum(
            _int_or_zero(state.get("enddate")) - _int_or_zero(state.get("startdate"))
            for state in valid_states
            if _int_or_zero(state.get("state")) == sleep_state
        )
        for sleep_state in range(4)
    }
    total_sleep_time = durations[1] + durations[2] + durations[3]
    if total_sleep_time <= 0:
        return None

    return {
        "id": f"states-{start_timestamp}-{end_timestamp}",
        "startdate": start_timestamp,
        "enddate": end_timestamp,
        "timezone": local_timezone.key,
        "data": {
            "total_timeinbed": sum(durations.values()),
            "total_sleep_time": total_sleep_time,
            "wakeupduration": durations[0],
            "lightsleepduration": durations[1],
            "deepsleepduration": durations[2],
            "remsleepduration": durations[3],
        },
    }


def fetch_workouts_windowed(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    if start_date > end_date:
        raise SystemExit("Withings start date must be on or before end date.")

    series: list[dict[str, Any]] = []
    window_start = start_date
    while window_start <= end_date:
        window_end = min(window_start + timedelta(days=BACKFILL_WINDOW_DAYS - 1), end_date)
        offset = 0
        while True:
            body = fetch_workouts(
                session,
                access_token,
                start_date=window_start,
                end_date=window_end,
                offset=offset,
            )
            window_series = body.get("series", [])
            if not isinstance(window_series, list):
                raise SystemExit("Withings workouts response did not contain series.")
            series.extend(window_series)
            if not body.get("more"):
                break
            offset = _int_or_zero(body.get("offset"))
        window_start = window_end + timedelta(days=1)
    return {"series": series}


def fetch_workouts_windowed_if_available(
    session: Any,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    try:
        return fetch_workouts_windowed(session, access_token, start_date=start_date, end_date=end_date)
    except SystemExit as exc:
        if "Withings status 2554" in str(exc):
            return {"series": []}
        if "Insufficient_scope" in str(exc):
            raise SystemExit(
                "Withings workouts require the user.activity OAuth scope. "
                "Re-authorize Withings with user.metrics and user.activity, then update the refresh token."
            ) from exc
        raise


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
    rows = normalize_measure_groups(measuregrps, config.timezone)
    if merge:
        rows = merge_measure_rows(read_measure_rows(config.withings.measures_csv), rows)
    written_paths.append(write_csv_file(config.withings.measures_csv, rows, MEASURE_FIELDS))
    return written_paths


def write_workouts(
    config: AppConfig,
    body: dict[str, Any],
    *,
    raw_name: str = "workouts.json",
    merge: bool = False,
) -> list[Path]:
    written_paths: list[Path] = []
    series = body.get("series", [])
    if not isinstance(series, list):
        raise SystemExit("Withings workouts response did not contain series.")

    written_paths.append(write_json_file(config.withings.raw_dir / raw_name, body))
    rows = normalize_workouts(series, config.timezone)
    if merge:
        rows = merge_workout_rows(read_workout_rows(config.withings.workouts_csv), rows)
    written_paths.append(write_csv_file(config.withings.workouts_csv, rows, WORKOUT_FIELDS))
    return written_paths


def write_activity(
    config: AppConfig,
    body: dict[str, Any],
    *,
    raw_name: str = "activity.json",
    merge: bool = False,
) -> list[Path]:
    written_paths: list[Path] = []
    activities = body.get("activities", [])
    if not isinstance(activities, list):
        raise SystemExit("Withings activity response did not contain activities.")

    written_paths.append(write_json_file(config.withings.raw_dir / raw_name, body))
    rows = normalize_activity_summaries(activities)
    if merge:
        rows = merge_activity_rows(read_activity_rows(config.withings.activity_csv), rows)
    written_paths.append(write_csv_file(config.withings.activity_csv, rows, ACTIVITY_FIELDS))
    return written_paths


def write_sleep(
    config: AppConfig,
    body: dict[str, Any],
    *,
    raw_name: str = "sleep.json",
    merge: bool = False,
) -> list[Path]:
    written_paths: list[Path] = []
    series = body.get("series", [])
    if not isinstance(series, list):
        raise SystemExit("Withings sleep response did not contain series.")

    written_paths.append(write_json_file(config.withings.raw_dir / raw_name, body))
    if not series:
        print(
            "Withings sleep API returned no summaries. Sleep visible in the Withings app may not be "
            "available through the public API, including sleep imported from Apple Health. "
            "Existing sleep.csv was preserved.",
            file=sys.stderr,
        )
        return written_paths

    rows = normalize_sleep_summaries(series, config.timezone)
    if merge:
        rows = merge_sleep_rows(read_sleep_rows(config.withings.sleep_csv), rows)
    written_paths.append(write_csv_file(config.withings.sleep_csv, rows, SLEEP_FIELDS))
    return written_paths


def read_measure_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def has_cached_height(path: Path) -> bool:
    return any(_int_or_zero(row.get("type")) == 4 for row in read_measure_rows(path))


def read_workout_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_activity_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_sleep_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def latest_measure_date(rows: list[dict[str, Any]]) -> date | None:
    return _latest_date(_date_from_iso(row.get("date")) for row in rows)


def latest_workout_date(rows: list[dict[str, Any]]) -> date | None:
    return _latest_date(_date_from_iso(str(row.get("start_time", "")).split("T", maxsplit=1)[0]) for row in rows)


def latest_activity_date(rows: list[dict[str, Any]]) -> date | None:
    return _latest_date(_date_from_iso(row.get("date")) for row in rows)


def latest_sleep_date(rows: list[dict[str, Any]]) -> date | None:
    return _latest_date(_date_from_iso(row.get("wake_date")) for row in rows)


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


def merge_workout_rows(
    existing_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in [*existing_rows, *new_rows]:
        rows_by_key[(str(row.get("source", "")), str(row.get("source_id", "")))] = row
    return sorted(
        rows_by_key.values(),
        key=lambda row: (
            str(row.get("start_time", "")),
            str(row.get("source", "")),
            str(row.get("source_id", "")),
        ),
    )


def merge_activity_rows(
    existing_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_date: dict[str, dict[str, Any]] = {}
    for row in [*existing_rows, *new_rows]:
        rows_by_date[str(row.get("date", ""))] = row
    return sorted(rows_by_date.values(), key=lambda row: str(row.get("date", "")))


def merge_sleep_rows(
    existing_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in [*existing_rows, *new_rows]:
        rows_by_key[(str(row.get("source", "")), str(row.get("source_id", "")))] = row
    return sorted(
        rows_by_key.values(),
        key=lambda row: (
            str(row.get("wake_date", "")),
            str(row.get("end_time", "")),
            str(row.get("source_id", "")),
        ),
    )


def normalize_measure_groups(
    measuregrps: list[dict[str, Any]],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in measuregrps:
        timestamp = _int_or_zero(group.get("date"))
        local_datetime = (
            datetime.fromtimestamp(timestamp, tz=local_timezone).isoformat()
            if timestamp
            else ""
        )
        for measure in group.get("measures", []):
            measure_type = _int_or_zero(measure.get("type"))
            type_name, unit_name = BODY_MEASURE_TYPES.get(measure_type, (f"type_{measure_type}", ""))
            rows.append(
                {
                    "grpid": group.get("grpid", ""),
                    "date": (
                        datetime.fromtimestamp(timestamp, tz=local_timezone).date().isoformat()
                        if timestamp
                        else ""
                    ),
                    "datetime_local": local_datetime,
                    "type": measure_type,
                    "type_name": type_name,
                    "value": _measure_value(measure),
                    "unit": unit_name,
                }
            )
    return rows


def normalize_activity_summaries(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for activity in activities:
        rows.append(
            {
                "date": str(activity.get("date", "")),
                "step_count": str(activity.get("steps", "")),
                "distance_km": _meters_to_km(activity.get("distance")),
            }
        )
    return rows


def normalize_sleep_summaries(
    series: list[dict[str, Any]],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for summary in series:
        start_timestamp = _int_or_zero(summary.get("startdate"))
        end_timestamp = _int_or_zero(summary.get("enddate"))
        if not start_timestamp or not end_timestamp:
            continue
        data = summary.get("data", {})
        if not isinstance(data, dict):
            data = {}
        start_time = datetime.fromtimestamp(start_timestamp, tz=local_timezone)
        end_time = datetime.fromtimestamp(end_timestamp, tz=local_timezone)
        rows.append(
            {
                "source": "withings",
                "source_id": str(summary.get("id") or f"{start_timestamp}-{end_timestamp}"),
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "timezone": str(summary.get("timezone") or local_timezone.key),
                "wake_date": end_time.date().isoformat(),
                "total_sleep_min": _seconds_to_minutes(
                    data.get("total_sleep_time")
                    or data.get("asleepduration")
                    or _stage_sleep_seconds(data)
                ),
                "time_in_bed_min": _seconds_to_minutes(data.get("total_timeinbed")),
                "awake_min": _seconds_to_minutes(data.get("wakeupduration")),
                "awake_count": _optional_number(data.get("wakeupcount")),
                "sleep_score": _optional_number(data.get("sleep_score")),
                "sleep_efficiency": _optional_decimal(data.get("sleep_efficiency")),
                "light_sleep_min": _seconds_to_minutes(data.get("lightsleepduration")),
                "deep_sleep_min": _seconds_to_minutes(data.get("deepsleepduration")),
                "rem_sleep_min": _seconds_to_minutes(data.get("remsleepduration")),
            }
        )
    return rows


def normalize_workouts(
    series: list[dict[str, Any]],
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for workout in series:
        start_timestamp = _int_or_zero(workout.get("startdate") or workout.get("date"))
        end_timestamp = _int_or_zero(workout.get("enddate"))
        data = workout.get("data", {})
        if not isinstance(data, dict):
            data = {}
        category = _int_or_zero(workout.get("category"))
        if category in IGNORED_WORKOUT_CATEGORIES:
            continue
        rows.append(
            {
                "source": "withings",
                "source_id": str(workout.get("id") or f"{start_timestamp}-{category}"),
                "start_time": (
                    datetime.fromtimestamp(start_timestamp, tz=local_timezone).isoformat()
                    if start_timestamp
                    else ""
                ),
                "end_time": (
                    datetime.fromtimestamp(end_timestamp, tz=local_timezone).isoformat()
                    if end_timestamp
                    else ""
                ),
                "duration_min": _workout_duration_min(workout, data, start_timestamp, end_timestamp),
                "distance_km": _workout_distance_km(data),
                "step_count": str(_int_or_zero(data.get("steps"))),
                "activity_type": WORKOUT_CATEGORIES.get(category, f"category_{category}"),
                "raw_type": WORKOUT_CATEGORIES.get(category, f"category_{category}"),
            }
        )
    return rows


def _workout_duration_min(
    workout: dict[str, Any],
    data: dict[str, Any],
    start_timestamp: int,
    end_timestamp: int,
) -> str:
    duration = _int_or_zero(data.get("effduration") or workout.get("duration"))
    if not duration and start_timestamp and end_timestamp:
        duration = max(0, end_timestamp - start_timestamp)
    return f"{duration / 60:.2f}"


def _workout_distance_km(data: dict[str, Any]) -> str:
    distance = _float_or_zero(data.get("manual_distance") or data.get("distance"))
    return _meters_to_km(distance)


def _meters_to_km(value: Any) -> str:
    distance = _float_or_zero(value)
    return f"{distance / 1000:.2f}" if distance else ""


def _stage_sleep_seconds(data: dict[str, Any]) -> int:
    return sum(
        _int_or_zero(data.get(field))
        for field in ("lightsleepduration", "deepsleepduration", "remsleepduration")
    )


def _seconds_to_minutes(value: Any) -> str:
    seconds = _int_or_zero(value)
    return f"{seconds / 60:.2f}" if seconds else ""


def _optional_number(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return ""


def _optional_decimal(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return ""


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


def _start_timestamp(
    value: date,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> int:
    return int(datetime.combine(value, time.min, tzinfo=local_timezone).timestamp())


def _end_timestamp(
    value: date,
    local_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> int:
    return int(datetime.combine(value, time.max, tzinfo=local_timezone).timestamp())


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _latest_date(values: Iterable[date | None]) -> date | None:
    dates = [value for value in values if value is not None]
    if not dates:
        return None
    return max(dates)


def _date_from_iso(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _json_response(response: Any, error_message: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise SystemExit(error_message) from exc


def _require(value: str, name: str) -> None:
    if not value:
        raise SystemExit(f"Missing {name}.")


def _requests() -> Any:
    try:
        import requests
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `poetry install`.") from exc
    return requests
