"""In-repository plugins.

Plugins fetch raw data and write normalized local records. Daily state builders
decide how plugin records contribute to AI-readable context.
"""

from __future__ import annotations

from ingest.plugins.contract import (
    PluginCliRegistry,
    PluginLoadError,
    PluginManifest,
    REPOSITORY_PLUGINS,
    load_plugin,
    load_repository_plugins,
)

__all__ = [
    "PluginCliRegistry",
    "PluginLoadError",
    "PluginManifest",
    "REPOSITORY_PLUGINS",
    "load_plugin",
    "load_repository_plugins",
]
