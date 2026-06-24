from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ingest.config import load_config, render_toml, update_withings_tokens


class ConfigTest(unittest.TestCase):
    def test_loads_public_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{root}/app-data"

[withings]
client_id = "withings-client"
secret = "withings-secret"

[sync.withings]
days = 21
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.data_dir, root / "app-data")
            self.assertEqual(config.daily_context_path, root / "app-data/generated/daily_context.md")
            self.assertEqual(config.withings.client_id, "withings-client")
            self.assertEqual(config.withings.client_secret, "withings-secret")
            self.assertEqual(config.withings.measures_csv, root / "app-data/withings/body_measures.csv")
            self.assertEqual(config.withings.activity_csv, root / "app-data/withings/activity.csv")
            self.assertEqual(config.withings.workouts_csv, root / "app-data/withings/workouts.csv")
            self.assertEqual(config.withings.raw_dir, root / "app-data/withings/raw")
            self.assertEqual(config.withings.days, 21)
            self.assertEqual(config.hevy.workouts_csv, root / "app-data/hevy/workouts.csv")
            self.assertEqual(config.hevy.sets_csv, root / "app-data/hevy/sets.csv")
            self.assertEqual(config.hevy.raw_dir, root / "app-data/hevy/raw")
            self.assertEqual(config.hevy.browser_dir, root / "app-data/hevy/browser")
            self.assertEqual(config.hevy.login_timeout_seconds, 300)
            self.assertFalse(config.suunto.enabled)
            self.assertEqual(config.suunto.command, "suuntool")
            self.assertEqual(config.suunto.workouts_csv, root / "app-data/suunto/workouts.csv")
            self.assertEqual(config.suunto.raw_dir, root / "app-data/suunto/raw")
            self.assertEqual(config.suunto.days, 30)

    def test_loads_enabled_suunto_command_and_sync_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            config_path.write_text(
                """
[suunto]
enabled = true
command = "~/bin/suuntool"

[sync.suunto]
days = 14
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertTrue(config.suunto.enabled)
            self.assertEqual(config.suunto.command, str(Path("~/bin/suuntool").expanduser()))
            self.assertEqual(config.suunto.days, 14)

    def test_loads_flat_sync_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                """
[sync]
days = 7
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.withings.days, 7)

    def test_defaults_withings_sync_days_to_thirty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text("", encoding="utf-8")

            config = load_config(config_path)

            self.assertEqual(config.withings.days, 30)

    def test_uses_xdg_data_home_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text("", encoding="utf-8")

            with patch.dict("os.environ", {"XDG_DATA_HOME": str(Path(temp_dir) / "xdg")}):
                config = load_config(config_path)

            self.assertEqual(config.data_dir, Path(temp_dir) / "xdg/ingest")
            self.assertEqual(config.generated_dir, Path(temp_dir) / "xdg/ingest/generated")

    def test_uses_xdg_config_home_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config/ingest.toml"
            config_path.parent.mkdir()
            config_path.write_text("", encoding="utf-8")

            with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(config_path.parent)}):
                config = load_config()

            self.assertEqual(config.path, config_path)

    def test_updates_withings_tokens_and_rotates_refresh_token_when_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                """
[withings]
refresh_token = "old-refresh"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            update_withings_tokens(
                config,
                {"access_token": "new-access", "refresh_token": "new-refresh", "expires_at": 123},
            )

            updated = load_config(config_path)
            self.assertEqual(updated.withings.access_token, "new-access")
            self.assertEqual(updated.withings.refresh_token, "new-refresh")
            self.assertEqual(updated.withings.expires_at, 123)

    def test_preserves_withings_refresh_token_when_refresh_response_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                """
[withings]
refresh_token = "old-refresh"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            update_withings_tokens(config, {"access_token": "new-access", "expires_at": 123})

            updated = load_config(config_path)
            self.assertEqual(updated.withings.access_token, "new-access")
            self.assertEqual(updated.withings.refresh_token, "old-refresh")

    def test_renders_nested_tables(self) -> None:
        rendered = render_toml({"sync": {"withings": {"days": 30}}})
        self.assertIn("[sync.withings]", rendered)
        self.assertIn("days = 30", rendered)


if __name__ == "__main__":
    unittest.main()
