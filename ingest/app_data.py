from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

APP_NAME = "ingest"


def resolve_data_dir(override: Path | str | None = None) -> Path:
    if override:
        return Path(override).expanduser()

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / APP_NAME
    return Path("~/.local/share").expanduser() / APP_NAME


def resolve_config_path(override: Path | str | None = None) -> Path:
    if override:
        return Path(override).expanduser()

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / f"{APP_NAME}.toml"
    return Path("~/.config").expanduser() / f"{APP_NAME}.toml"


def default_config_path() -> Path:
    return resolve_config_path()


class AppDataDirectory:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()

    def path(self, relative_path: Path | str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Data paths must be relative and stay inside the app data directory: {relative}")
        return self.root / relative

    def write_json(self, relative_path: Path | str, data: Any) -> Path:
        path = self.path(relative_path)
        return write_json_file(path, data)

    def write_csv(self, relative_path: Path | str, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> Path:
        path = self.path(relative_path)
        return write_csv_file(path, rows, fieldnames)


def write_json_file(path: Path | str, data: Any) -> Path:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return path


def write_csv_file(path: Path | str, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> Path:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as temp_file:
            writer = csv.DictWriter(temp_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fieldnames})
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()

    return path


def write_text_file(path: Path | str, content: str) -> Path:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, content)
    return path


def _atomic_write_text(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(content)
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()
