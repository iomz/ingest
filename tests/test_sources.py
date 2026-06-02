from __future__ import annotations

import unittest
from pathlib import Path

from ingest.sources import SourceAdapter


class DummySource:
    name = "dummy"

    def sync(self, config: object) -> list[Path]:
        return []


class SourceModelTest(unittest.TestCase):
    def test_source_adapter_is_generic(self) -> None:
        adapter: SourceAdapter = DummySource()

        self.assertEqual(adapter.name, "dummy")
        self.assertEqual(adapter.sync(object()), [])

    def test_strava_assumptions_are_gone_from_code_tree(self) -> None:
        root = Path(__file__).resolve().parents[1]
        matches: list[Path] = []
        for path in [*root.joinpath("ingest").rglob("*.py"), *root.joinpath("tests").rglob("*.py")]:
            if path.name == "test_sources.py":
                continue
            if "strava" in path.read_text(encoding="utf-8").lower():
                matches.append(path.relative_to(root))

        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
