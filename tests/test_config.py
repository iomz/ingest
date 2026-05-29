from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from life_log_sync.config import load_config, render_toml, update_strava_tokens


class ConfigTest(unittest.TestCase):
    def test_loads_public_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = Path(temp_dir) / "life-log-sync.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{root}/app-data"

[strava]
client_id = "client"
client_secret = "secret"
refresh_token = "refresh"

[sync.strava]
days = 14
per_page = 50
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.data_dir, root / "app-data")
            self.assertEqual(config.today_context_path, root / "app-data/generated/today_context.md")
            self.assertEqual(config.strava.activities_csv, root / "app-data/strava/activities.csv")
            self.assertEqual(config.strava.raw_dir, root / "app-data/strava/raw")
            self.assertEqual(config.strava.client_id, "client")
            self.assertEqual(config.strava.days, 14)
            self.assertEqual(config.strava.per_page, 50)

    def test_loads_flat_sync_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "life-log-sync.toml"
            config_path.write_text(
                """
[strava]
access_token = "access"

[sync]
days = 7
per_page = 10
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.strava.access_token, "access")
            self.assertEqual(config.strava.days, 7)

    def test_uses_xdg_data_home_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "life-log-sync.toml"
            config_path.write_text("[strava]\naccess_token = \"access\"\n", encoding="utf-8")

            with patch.dict("os.environ", {"XDG_DATA_HOME": str(Path(temp_dir) / "xdg")}):
                config = load_config(config_path)

            self.assertEqual(config.data_dir, Path(temp_dir) / "xdg/life-log-sync")
            self.assertEqual(config.generated_dir, Path(temp_dir) / "xdg/life-log-sync/generated")

    def test_uses_xdg_config_home_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config/life-log-sync.toml"
            config_path.parent.mkdir()
            config_path.write_text("[strava]\naccess_token = \"access\"\n", encoding="utf-8")

            with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(config_path.parent)}):
                config = load_config()

            self.assertEqual(config.path, config_path)

    def test_updates_strava_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = Path(temp_dir) / "life-log-sync.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{root}/app-data"

[strava]
refresh_token = "old"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            update_strava_tokens(
                config,
                {"access_token": "new-access", "refresh_token": "new-refresh", "expires_at": 123},
            )

            updated = load_config(config_path)
            self.assertEqual(updated.strava.access_token, "new-access")
            self.assertEqual(updated.strava.refresh_token, "new-refresh")
            self.assertEqual(updated.strava.expires_at, 123)

    def test_renders_nested_tables(self) -> None:
        rendered = render_toml({"sync": {"strava": {"days": 30}}})
        self.assertIn("[sync.strava]", rendered)
        self.assertIn("days = 30", rendered)


if __name__ == "__main__":
    unittest.main()
