"""In-repository plugins.

Plugins fetch raw data and write normalized local records. Daily state builders
decide how plugin records contribute to AI-readable context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ingest.config import AppConfig


class Plugin(Protocol):
    name: str

    def sync(self, config: AppConfig) -> list[Path]:
        """Fetch recent source data and write normalized records."""
