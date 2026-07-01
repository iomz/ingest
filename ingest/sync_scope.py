from __future__ import annotations

from typing import Any

from ingest.config import AppConfig
from ingest.context import BUILTIN_CONTEXT_DEFAULTS, SUPPORTED_CONTEXT_SOURCES
from ingest.plugins.contract import PluginSyncScope

CONTEXT_CAPABILITY_SCOPES: dict[tuple[str, ...], tuple[str, ...]] = {
    ("activity",): ("activity",),
    ("activity", "workout"): ("activity",),
    ("activity", "workout", "sets"): ("activity.strength",),
    ("activity", "workout", "load"): ("activity",),
    ("measurement",): ("measurement.body",),
    ("measurement", "steps"): ("measurement.steps",),
    ("measurement", "weight"): ("measurement.body",),
    ("measurement", "lean_mass"): ("measurement.body",),
    ("measurement", "fat_mass"): ("measurement.body",),
    ("measurement", "blood_pressure"): ("measurement.blood_pressure",),
    ("recovery",): ("recovery.sleep",),
    ("recovery", "sleep"): ("recovery.sleep",),
}


def build_plugin_sync_scope(config: AppConfig, plugin: str) -> PluginSyncScope:
    requested: set[str] = set()
    for path, capabilities in CONTEXT_CAPABILITY_SCOPES.items():
        if _context_source(config, path) == plugin:
            requested.update(capabilities)
    return PluginSyncScope(tuple(sorted(requested)))


def _context_source(config: AppConfig, path: tuple[str, ...]) -> str | None:
    configured = _configured_context_source(config, path)
    source = configured if configured is not None else _builtin_context_source(path)
    if source is None or source == "none":
        return source
    if source not in SUPPORTED_CONTEXT_SOURCES.get(path, set()):
        return "none"
    return source


def _configured_context_source(config: AppConfig, path: tuple[str, ...]) -> str | None:
    section: Any = getattr(config.context, path[0])
    if not isinstance(section, dict):
        return None
    if len(path) == 1:
        return _string_context_value(section.get("default"))
    for index, key in enumerate(path[1:], start=1):
        if not isinstance(section, dict):
            return None
        value = section.get(key)
        if isinstance(value, dict):
            section = value
            if index == len(path) - 1:
                return _string_context_value(section.get("default"))
            continue
        if value is not None:
            return _string_context_value(value)
        return None
    return None


def _string_context_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def _builtin_context_source(path: tuple[str, ...]) -> str | None:
    for length in range(len(path), 0, -1):
        source = BUILTIN_CONTEXT_DEFAULTS.get(path[:length])
        if source is not None:
            return source
    return None
