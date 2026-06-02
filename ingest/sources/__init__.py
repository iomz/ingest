"""Source adapters.

Adapters fetch raw data and write normalized local records. Daily state builders
decide how records contribute to AI-readable context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ingest.config import AppConfig


class SourceAdapter(Protocol):
    name: str

    def sync(self, config: AppConfig) -> list[Path]:
        """Fetch recent source data and write normalized records."""
