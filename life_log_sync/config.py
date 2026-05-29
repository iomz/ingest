from __future__ import annotations

import json
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from life_log_sync.app_data import default_config_path, resolve_data_dir

DEFAULT_CONFIG_PATH = default_config_path()
DEFAULT_CONFIG_EXAMPLE_PATH = Path("config.example.toml")


@dataclass(frozen=True)
class StravaConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    access_token: str
    expires_at: int
    activities_csv: Path
    raw_dir: Path
    days: int
    per_page: int


@dataclass(frozen=True)
class AppConfig:
    path: Path
    data: dict[str, Any]
    data_dir: Path
    generated_dir: Path
    today_context_path: Path
    strava: StravaConfig


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
    generated_dir = _configured_data_path(data_dir, data.get("generated", {}), "generated.dir", Path("generated"))
    today_context_path = generated_dir / "today_context.md"
    strava = _load_strava_config(data, data_dir)
    return AppConfig(
        path=config_path,
        data=data,
        data_dir=data_dir,
        generated_dir=generated_dir,
        today_context_path=today_context_path,
        strava=strava,
    )


def save_config(config: AppConfig) -> None:
    write_toml(config.data, config.path)


def update_strava_tokens(config: AppConfig, token: dict[str, Any]) -> None:
    strava = config.data.setdefault("strava", {})
    strava["access_token"] = _required_token_value(token, "access_token")
    strava["refresh_token"] = _required_token_value(token, "refresh_token")
    if "expires_at" in token:
        strava["expires_at"] = token["expires_at"]
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


def _load_strava_config(data: dict[str, Any], data_dir: Path) -> StravaConfig:
    strava = data.get("strava", {})
    sync = _strava_sync_section(data)
    return StravaConfig(
        client_id=str(strava.get("client_id", "")).strip(),
        client_secret=str(strava.get("client_secret", "")).strip(),
        refresh_token=str(strava.get("refresh_token", "")).strip(),
        access_token=str(strava.get("access_token", "")).strip(),
        expires_at=_int_value(strava.get("expires_at", 0), "strava.expires_at"),
        activities_csv=_configured_data_path(
            data_dir,
            strava,
            "strava.activities_csv",
            Path("strava/activities.csv"),
        ),
        raw_dir=_configured_data_path(data_dir, strava, "strava.raw_dir", Path("strava/raw")),
        days=_positive_int(sync.get("days", 30), "sync.strava.days"),
        per_page=_positive_int(sync.get("per_page", 100), "sync.strava.per_page"),
    )


def _configured_data_path(data_dir: Path, section: dict[str, Any], name: str, default: Path) -> Path:
    key = name.rsplit(".", maxsplit=1)[-1]
    raw_path = str(section.get(key, "")).strip()
    path = Path(raw_path).expanduser() if raw_path else default
    if path.is_absolute():
        raise SystemExit(f"{name} must be relative to app.data_dir.")
    return data_dir / path


def _strava_sync_section(data: dict[str, Any]) -> dict[str, Any]:
    sync = data.get("sync", {})
    if isinstance(sync.get("strava"), dict):
        return sync["strava"]
    return sync


def _required_token_value(token: dict[str, Any], key: str) -> str:
    value = str(token.get(key, "")).strip()
    if not value:
        raise SystemExit(f"Strava token refresh response did not include {key}.")
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
