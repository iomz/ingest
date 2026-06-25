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

DEFAULT_CONFIG_PATH = default_config_path()
DEFAULT_CONFIG_EXAMPLE_PATH = Path("config.example.toml")


@dataclass(frozen=True)
class WithingsConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    access_token: str
    expires_at: int
    measures_csv: Path
    activity_csv: Path
    workouts_csv: Path
    raw_dir: Path
    days: int


@dataclass(frozen=True)
class HevyConfig:
    workouts_csv: Path
    sets_csv: Path
    raw_dir: Path
    browser_dir: Path
    login_timeout_seconds: int


@dataclass(frozen=True)
class SuuntoConfig:
    enabled: bool
    command: str
    workouts_csv: Path
    raw_dir: Path
    days: int


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
    ui: UIConfig

    @property
    def today_context_path(self) -> Path:
        return self.daily_context_path


def load_config(path: Path | str | None = None) -> AppConfig:
    config_path = Path(path).expanduser() if path is not None else default_config_path()
    if not config_path.exists():
        raise SystemExit(
            f"Missing {config_path}. Copy {DEFAULT_CONFIG_EXAMPLE_PATH} to {config_path} and fill it in."
        )

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
        ui=ui,
    )


def save_config(config: AppConfig) -> None:
    write_toml(config.data, config.path)


def update_withings_tokens(config: AppConfig, token: dict[str, Any]) -> None:
    withings = config.data.setdefault("withings", {})
    withings["access_token"] = _required_token_value(token, "access_token", "Withings")
    refresh_token = str(token.get("refresh_token", "")).strip()
    if refresh_token:
        withings["refresh_token"] = refresh_token
    if "expires_in" in token:
        withings["expires_at"] = int(token["expires_in"]) + int(time.time())
    if "expires_at" in token:
        withings["expires_at"] = token["expires_at"]
    save_config(config)


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
    withings = data.get("withings", {})
    sync = _withings_sync_section(data)
    return WithingsConfig(
        client_id=str(withings.get("client_id", "")).strip(),
        client_secret=str(withings.get("client_secret") or withings.get("secret") or "").strip(),
        refresh_token=str(withings.get("refresh_token", "")).strip(),
        access_token=str(withings.get("access_token", "")).strip(),
        expires_at=_int_value(withings.get("expires_at", 0), "withings.expires_at"),
        measures_csv=_configured_data_path(
            data_dir,
            withings,
            "withings.measures_csv",
            Path("withings/body_measures.csv"),
        ),
        activity_csv=_configured_data_path(
            data_dir,
            withings,
            "withings.activity_csv",
            Path("withings/activity.csv"),
        ),
        workouts_csv=_configured_data_path(
            data_dir,
            withings,
            "withings.workouts_csv",
            Path("withings/workouts.csv"),
        ),
        raw_dir=_configured_data_path(data_dir, withings, "withings.raw_dir", Path("withings/raw")),
        days=_positive_int(sync.get("days", 30), "sync.withings.days"),
    )


def _load_hevy_config(data: dict[str, Any], data_dir: Path) -> HevyConfig:
    hevy = data.get("hevy", {})
    return HevyConfig(
        workouts_csv=_configured_data_path(
            data_dir,
            hevy,
            "hevy.workouts_csv",
            Path("hevy/workouts.csv"),
        ),
        sets_csv=_configured_data_path(data_dir, hevy, "hevy.sets_csv", Path("hevy/sets.csv")),
        raw_dir=_configured_data_path(data_dir, hevy, "hevy.raw_dir", Path("hevy/raw")),
        browser_dir=_configured_data_path(data_dir, hevy, "hevy.browser_dir", Path("hevy/browser")),
        login_timeout_seconds=_positive_int(
            hevy.get("login_timeout_seconds", 300),
            "hevy.login_timeout_seconds",
        ),
    )


def _load_suunto_config(data: dict[str, Any], data_dir: Path) -> SuuntoConfig:
    suunto = data.get("suunto", {})
    sync = data.get("sync", {}).get("suunto", {})
    return SuuntoConfig(
        enabled=bool(suunto.get("enabled", False)),
        command=str(Path(str(suunto.get("command", "")).strip() or "suuntool").expanduser()),
        workouts_csv=_configured_data_path(
            data_dir,
            suunto,
            "suunto.workouts_csv",
            Path("suunto/workouts.csv"),
        ),
        raw_dir=_configured_data_path(data_dir, suunto, "suunto.raw_dir", Path("suunto/raw")),
        days=_positive_int(sync.get("days", 30), "sync.suunto.days"),
    )


def _load_ui_config(data: dict[str, Any]) -> UIConfig:
    ui = data.get("ui", {})
    theme = str(ui.get("theme") or "default").strip().lower()
    body_weight_goal = str(ui.get("body_weight_goal") or "maintenance").strip().lower()
    if theme not in {"default", "colorful"}:
        raise SystemExit("ui.theme must be one of: default, colorful")
    if body_weight_goal not in {"loss", "maintenance", "gain"}:
        raise SystemExit(
            "ui.body_weight_goal must be one of: loss, maintenance, gain"
        )
    return UIConfig(theme=theme, body_weight_goal=body_weight_goal)


def _configured_data_path(data_dir: Path, section: dict[str, Any], name: str, default: Path) -> Path:
    key = name.rsplit(".", maxsplit=1)[-1]
    raw_path = str(section.get(key, "")).strip()
    path = Path(raw_path).expanduser() if raw_path else default
    if path.is_absolute():
        raise SystemExit(f"{name} must be relative to app.data_dir.")
    return data_dir / path


def _withings_sync_section(data: dict[str, Any]) -> dict[str, Any]:
    sync = data.get("sync", {})
    if isinstance(sync.get("withings"), dict):
        return sync["withings"]
    return sync


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


def _int_value(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{name} must be an integer.") from exc
