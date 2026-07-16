from __future__ import annotations

import json
import os
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ingest.app_data import default_config_path, resolve_data_dir
from ingest.auth_state import read_auth_state, write_auth_state

DEFAULT_CONFIG_PATH = default_config_path()
DEFAULT_CONFIG_EXAMPLE_PATH = Path("config.example.toml")


@dataclass(frozen=True)
class WithingsConfig:
    configured: bool
    enabled: bool
    auth_state_path: Path
    client_id: str
    client_secret: str
    refresh_token: str
    access_token: str
    expires_at: int
    measures_csv: Path
    activity_csv: Path
    workouts_csv: Path
    sleep_csv: Path
    raw_dir: Path
    days: int


@dataclass(frozen=True)
class HevyConfig:
    configured: bool
    enabled: bool
    workouts_csv: Path
    sets_csv: Path
    raw_dir: Path
    browser_dir: Path
    login_timeout_seconds: int
    login_poll_interval_seconds: int


@dataclass(frozen=True)
class SuuntoConfig:
    configured: bool
    enabled: bool
    command: str
    workouts_csv: Path
    raw_dir: Path
    days: int


@dataclass(frozen=True)
class VitalsyncConfig:
    configured: bool
    enabled: bool
    auth_state_path: Path
    endpoint: str
    client_id: str
    refresh_token: str
    access_token: str
    expires_at: str
    source_bundle_id: str
    sleep_csv: Path
    steps_csv: Path
    blood_pressure_csv: Path
    waist_circumference_csv: Path
    raw_dir: Path
    days: int


@dataclass(frozen=True)
class ContextConfig:
    activity: dict[str, Any]
    measurement: dict[str, Any]
    recovery: dict[str, Any]


@dataclass(frozen=True)
class UIConfig:
    theme: str
    body_weight_goal: str


@dataclass(frozen=True)
class AppConfig:
    path: Path
    data: dict[str, Any]
    data_dir: Path
    generated_dir: Path
    daily_context_path: Path
    timezone: ZoneInfo
    withings: WithingsConfig
    hevy: HevyConfig
    suunto: SuuntoConfig
    vitalsync: VitalsyncConfig
    context: ContextConfig
    ui: UIConfig

    @property
    def today_context_path(self) -> Path:
        return self.daily_context_path


def load_config(path: Path | str | None = None) -> AppConfig:
    config_path = Path(path).expanduser() if path is not None else default_config_path()
    if not config_path.exists():
        if path is None:
            config_path.parent.mkdir(parents=True, exist_ok=True)
        raise SystemExit(f"Missing {config_path}. Copy {DEFAULT_CONFIG_EXAMPLE_PATH} to {config_path} and fill it in.")

    try:
        with config_path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"Could not parse {config_path}: {exc}") from exc

    data_dir = _load_data_dir(data)
    timezone = _load_timezone(data)
    generated_dir = _configured_data_path(data_dir, data.get("generated", {}), "generated.dir", Path("generated"))
    daily_context_path = generated_dir / "daily_context.md"
    withings = _load_withings_config(data, data_dir)
    hevy = _load_hevy_config(data, data_dir)
    suunto = _load_suunto_config(data, data_dir)
    vitalsync = _load_vitalsync_config(data, data_dir)
    context = _load_context_config(data)
    ui = _load_ui_config(data)
    return AppConfig(
        path=config_path,
        data=data,
        data_dir=data_dir,
        generated_dir=generated_dir,
        daily_context_path=daily_context_path,
        timezone=timezone,
        withings=withings,
        hevy=hevy,
        suunto=suunto,
        vitalsync=vitalsync,
        context=context,
        ui=ui,
    )


def save_config(config: AppConfig) -> None:
    write_toml(config.data, config.path)


def update_withings_tokens(config: AppConfig, token: dict[str, Any]) -> None:
    withings = read_auth_state(config.withings.auth_state_path)
    withings["access_token"] = _required_token_value(token, "access_token", "Withings")
    refresh_token = str(token.get("refresh_token", "")).strip()
    if refresh_token:
        withings["refresh_token"] = refresh_token
    if "expires_in" in token:
        withings["expires_at"] = int(token["expires_in"]) + int(time.time())
    if "expires_at" in token:
        withings["expires_at"] = token["expires_at"]
    write_auth_state(config.withings.auth_state_path, withings)


def update_vitalsync_tokens(config: AppConfig, token: dict[str, Any]) -> None:
    vitalsync = read_auth_state(config.vitalsync.auth_state_path)
    client_id = str(token.get("client_id", "")).strip()
    refresh_token = str(token.get("refresh_token", "")).strip()
    if client_id:
        vitalsync["client_id"] = client_id
    if refresh_token:
        vitalsync["refresh_token"] = refresh_token
    vitalsync["access_token"] = _required_token_value(token, "access_token", "Vitalsync")
    if "expires_at" in token:
        vitalsync["expires_at"] = str(token["expires_at"])
    write_auth_state(config.vitalsync.auth_state_path, vitalsync)


def update_withings_auth_state(
    config: AppConfig,
    *,
    client_id: str = "",
    client_secret: str = "",
) -> None:
    state = read_auth_state(config.withings.auth_state_path)
    if client_id:
        state["client_id"] = client_id
    if client_secret:
        state["client_secret"] = client_secret
    write_auth_state(config.withings.auth_state_path, state)


def write_toml(data: dict[str, Any], path: Path) -> None:
    rendered = render_toml(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(rendered)
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def render_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        _render_section(lines, [section], values)
    return "\n".join(lines).rstrip() + "\n"


def _render_section(lines: list[str], path: list[str], values: dict[str, Any]) -> None:
    scalar_items = [(key, value) for key, value in values.items() if not isinstance(value, dict)]
    nested_items = [(key, value) for key, value in values.items() if isinstance(value, dict)]

    if scalar_items:
        lines.append(f"[{'.'.join(path)}]")
        for key, value in scalar_items:
            lines.append(f"{key} = {_format_toml_value(value)}")
        lines.append("")

    for key, nested_values in nested_items:
        _render_section(lines, [*path, key], nested_values)


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if value is None:
        return '""'
    return json.dumps(str(value))


def _load_data_dir(data: dict[str, Any]) -> Path:
    section = data.get("app") or data.get("data") or {}
    raw_path = str(section.get("data_dir") or section.get("dir") or "").strip()
    return resolve_data_dir(raw_path or None)


def _load_timezone(data: dict[str, Any]) -> ZoneInfo:
    app = data.get("app") or data.get("data") or {}
    name = str(app.get("timezone") or "Asia/Tokyo").strip()
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise SystemExit(f"app.timezone is not a valid IANA timezone: {name}") from exc


def _load_withings_config(data: dict[str, Any], data_dir: Path) -> WithingsConfig:
    withings = _plugin_section(data, "withings")
    auth_state_path = _configured_data_path(
        data_dir,
        withings,
        "plugin.withings.auth_state",
        Path("withings/auth.json"),
    )
    auth_state = read_auth_state(auth_state_path)
    return WithingsConfig(
        configured=_plugin_configured(data, "withings"),
        enabled=_bool_value(withings.get("enabled", True), "plugin.withings.enabled"),
        auth_state_path=auth_state_path,
        client_id=str(auth_state.get("client_id", "")).strip(),
        client_secret=str(auth_state.get("client_secret", "")).strip(),
        refresh_token=str(auth_state.get("refresh_token", "")).strip(),
        access_token=str(auth_state.get("access_token", "")).strip(),
        expires_at=_int_value(auth_state.get("expires_at", 0), "withings auth_state expires_at"),
        measures_csv=_configured_data_path(
            data_dir,
            withings,
            "plugin.withings.measures_csv",
            Path("withings/body_measures.csv"),
        ),
        activity_csv=_configured_data_path(
            data_dir,
            withings,
            "plugin.withings.activity_csv",
            Path("withings/activity.csv"),
        ),
        workouts_csv=_configured_data_path(
            data_dir,
            withings,
            "plugin.withings.workouts_csv",
            Path("withings/workouts.csv"),
        ),
        sleep_csv=_configured_data_path(
            data_dir,
            withings,
            "plugin.withings.sleep_csv",
            Path("withings/sleep.csv"),
        ),
        raw_dir=_configured_data_path(data_dir, withings, "plugin.withings.raw_dir", Path("withings/raw")),
        days=_positive_int(withings.get("sync_days", 30), "plugin.withings.sync_days"),
    )


def _load_hevy_config(data: dict[str, Any], data_dir: Path) -> HevyConfig:
    hevy = _plugin_section(data, "hevy")
    return HevyConfig(
        configured=_plugin_configured(data, "hevy"),
        enabled=_bool_value(hevy.get("enabled", True), "plugin.hevy.enabled"),
        workouts_csv=_configured_data_path(
            data_dir,
            hevy,
            "plugin.hevy.workouts_csv",
            Path("hevy/workouts.csv"),
        ),
        sets_csv=_configured_data_path(data_dir, hevy, "plugin.hevy.sets_csv", Path("hevy/sets.csv")),
        raw_dir=_configured_data_path(data_dir, hevy, "plugin.hevy.raw_dir", Path("hevy/raw")),
        browser_dir=_configured_data_path(data_dir, hevy, "plugin.hevy.browser_dir", Path("hevy/browser")),
        login_timeout_seconds=_positive_int(
            hevy.get("login_timeout_seconds", 300),
            "plugin.hevy.login_timeout_seconds",
        ),
        login_poll_interval_seconds=_positive_int(
            hevy.get("login_poll_interval_seconds", 2),
            "plugin.hevy.login_poll_interval_seconds",
        ),
    )


def _load_suunto_config(data: dict[str, Any], data_dir: Path) -> SuuntoConfig:
    suunto = _plugin_section(data, "suunto")
    return SuuntoConfig(
        configured=_plugin_configured(data, "suunto"),
        enabled=_bool_value(suunto.get("enabled", True), "plugin.suunto.enabled"),
        command=str(Path(str(suunto.get("command", "")).strip() or "suuntool").expanduser()),
        workouts_csv=_configured_data_path(
            data_dir,
            suunto,
            "plugin.suunto.workouts_csv",
            Path("suunto/workouts.csv"),
        ),
        raw_dir=_configured_data_path(data_dir, suunto, "plugin.suunto.raw_dir", Path("suunto/raw")),
        days=_positive_int(suunto.get("sync_days", 30), "plugin.suunto.sync_days"),
    )


def _load_vitalsync_config(data: dict[str, Any], data_dir: Path) -> VitalsyncConfig:
    vitalsync = _plugin_section(data, "vitalsync")
    auth_state_path = _configured_data_path(
        data_dir,
        vitalsync,
        "plugin.vitalsync.auth_state",
        Path("vitalsync/auth.json"),
    )
    auth_state = read_auth_state(auth_state_path)
    endpoint = str(
        vitalsync.get("endpoint") or vitalsync.get("provider") or "https://api.sazanka.io/vitalsync/v1"
    ).rstrip("/")
    return VitalsyncConfig(
        configured=_plugin_configured(data, "vitalsync"),
        enabled=_bool_value(vitalsync.get("enabled", True), "plugin.vitalsync.enabled"),
        auth_state_path=auth_state_path,
        endpoint=endpoint,
        client_id=str(auth_state.get("client_id", "")).strip(),
        refresh_token=str(auth_state.get("refresh_token", "")).strip(),
        access_token=str(auth_state.get("access_token", "")).strip(),
        expires_at=str(auth_state.get("expires_at", "")).strip(),
        source_bundle_id=str(vitalsync.get("source_bundle_id", "com.lexwarelabs.goodmorning")).strip(),
        sleep_csv=_configured_data_path(
            data_dir,
            vitalsync,
            "plugin.vitalsync.sleep_csv",
            Path("vitalsync/sleep.csv"),
        ),
        steps_csv=_configured_data_path(
            data_dir,
            vitalsync,
            "plugin.vitalsync.steps_csv",
            Path("vitalsync/steps.csv"),
        ),
        blood_pressure_csv=_configured_data_path(
            data_dir,
            vitalsync,
            "plugin.vitalsync.blood_pressure_csv",
            Path("vitalsync/blood_pressure.csv"),
        ),
        waist_circumference_csv=_configured_data_path(
            data_dir,
            vitalsync,
            "plugin.vitalsync.waist_circumference_csv",
            Path("vitalsync/waist_circumference.csv"),
        ),
        raw_dir=_configured_data_path(data_dir, vitalsync, "plugin.vitalsync.raw_dir", Path("vitalsync/raw")),
        days=_positive_int(vitalsync.get("sync_days", 30), "plugin.vitalsync.sync_days"),
    )


def _plugin_section(data: dict[str, Any], name: str) -> dict[str, Any]:
    plugins = data.get("plugin", {})
    if not isinstance(plugins, dict):
        raise SystemExit("plugin must be a table.")
    section = plugins.get(name, {})
    if not isinstance(section, dict):
        raise SystemExit(f"plugin.{name} must be a table.")
    return section


def _plugin_configured(data: dict[str, Any], name: str) -> bool:
    plugins = data.get("plugin", {})
    return isinstance(plugins, dict) and name in plugins


def _load_context_config(data: dict[str, Any]) -> ContextConfig:
    context = data.get("context", {})
    if not isinstance(context, dict):
        raise SystemExit("context must be a table.")
    return ContextConfig(
        activity=_context_section(context, "activity"),
        measurement=_context_section(context, "measurement"),
        recovery=_context_section(context, "recovery"),
    )


def _context_section(context: dict[str, Any], name: str) -> dict[str, Any]:
    section = context.get(name, {})
    if section == "":
        return {}
    if not isinstance(section, dict):
        raise SystemExit(f"context.{name} must be a table.")
    return section


def _load_ui_config(data: dict[str, Any]) -> UIConfig:
    ui = data.get("ui", {})
    theme = str(ui.get("theme") or "default").strip().lower()
    body_weight_goal = str(ui.get("body_weight_goal") or "maintenance").strip().lower()
    if theme not in {"default", "colorful"}:
        raise SystemExit("ui.theme must be one of: default, colorful")
    if body_weight_goal not in {"loss", "maintenance", "gain"}:
        raise SystemExit("ui.body_weight_goal must be one of: loss, maintenance, gain")
    return UIConfig(theme=theme, body_weight_goal=body_weight_goal)


def _configured_data_path(data_dir: Path, section: dict[str, Any], name: str, default: Path) -> Path:
    key = name.rsplit(".", maxsplit=1)[-1]
    raw_path = str(section.get(key, "")).strip()
    path = Path(raw_path).expanduser() if raw_path else default
    if path.is_absolute():
        raise SystemExit(f"{name} must be relative to app.data_dir.")
    return data_dir / path


def _required_token_value(token: dict[str, Any], key: str, service: str = "Withings") -> str:
    value = str(token.get(key, "")).strip()
    if not value:
        raise SystemExit(f"{service} token refresh response did not include {key}.")
    return value


def _positive_int(value: Any, name: str) -> int:
    number = _int_value(value, name)
    if number < 1:
        raise SystemExit(f"{name} must be greater than 0.")
    return number


def _bool_value(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise SystemExit(f"{name} must be a boolean.")


def _int_value(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{name} must be an integer.") from exc
