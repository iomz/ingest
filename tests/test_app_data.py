from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from life_log_sync.app_data import AppDataDirectory, resolve_data_dir


class AppDataTest(unittest.TestCase):
    def test_resolves_xdg_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {"XDG_DATA_HOME": temp_dir}):
                self.assertEqual(resolve_data_dir(), Path(temp_dir) / "life-log-sync")

    def test_rejects_paths_outside_data_dir(self) -> None:
        data_dir = AppDataDirectory("/tmp/life-log-sync")

        with self.assertRaises(ValueError):
            data_dir.path("../secret.md")

        with self.assertRaises(ValueError):
            data_dir.path("/tmp/secret.md")


if __name__ == "__main__":
    unittest.main()
