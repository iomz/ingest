from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_auth_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as state_file:
            data = json.load(state_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read plugin auth state {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Plugin auth state must be a JSON object: {path}")
    return data


def write_auth_state(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            json.dump(data, temp_file, ensure_ascii=False, indent=2, sort_keys=True)
            temp_file.write("\n")
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()
    return path
