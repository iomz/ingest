from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import Protocol

import typer

from ingest.config import AppConfig


class SyncCallable(Protocol):
    def __call__(self, config: AppConfig) -> list[Path]: ...


class SyncUnavailableReasonCallable(Protocol):
    def __call__(self, config: AppConfig) -> str: ...


@dataclass(frozen=True)
class PluginCliRegistry:
    sync_app: typer.Typer
    import_app: typer.Typer
    auth_app: typer.Typer
    get_config: Callable[[typer.Context], AppConfig]
    run_sync: Callable[[AppConfig, str], list[Path]]
    sync_ready: Callable[[AppConfig, str, bool], bool]
    print_paths: Callable[[list[Path]], None]
    date_arg: Callable[[str], date]
    optional_date_arg: Callable[[str | None], date | None]


class RegisterCliCallable(Protocol):
    def __call__(self, registry: PluginCliRegistry) -> None: ...


@dataclass(frozen=True)
class PluginManifest:
    name: str
    provides: tuple[str, ...]
    sync: SyncCallable | None = None
    sync_unavailable_reason: SyncUnavailableReasonCallable | None = None
    register_cli: RegisterCliCallable | None = None
    serial_sync: bool = False


class PluginLoadError(SystemExit):
    pass


def load_plugin(name: str) -> PluginManifest:
    module_name = f"ingest.plugins.{name}"
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise PluginLoadError(f"Unknown ingest plugin {name!r}.") from exc
        raise

    manifest = getattr(module, "manifest", None)
    if not isinstance(manifest, PluginManifest):
        raise PluginLoadError(f"Plugin {name!r} must export PluginManifest as manifest.")
    if manifest.name != name:
        raise PluginLoadError(f"Plugin {name!r} manifest name mismatch: {manifest.name!r}.")
    if not manifest.provides:
        raise PluginLoadError(f"Plugin {name!r} manifest must declare provides.")
    if manifest.sync is not None and not callable(manifest.sync):
        raise PluginLoadError(f"Plugin {name!r} manifest sync must be callable or None.")
    if manifest.sync_unavailable_reason is not None and not callable(manifest.sync_unavailable_reason):
        raise PluginLoadError(f"Plugin {name!r} manifest sync_unavailable_reason must be callable or None.")
    if manifest.register_cli is not None and not callable(manifest.register_cli):
        raise PluginLoadError(f"Plugin {name!r} manifest register_cli must be callable or None.")
    return manifest


REPOSITORY_PLUGINS = ("hevy", "suunto", "vitalsync", "withings")


def load_repository_plugins() -> tuple[PluginManifest, ...]:
    return tuple(load_plugin(name) for name in REPOSITORY_PLUGINS)
