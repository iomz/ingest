from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ingest.config import load_config, render_toml, update_vitalsync_tokens, update_withings_tokens


class ConfigTest(unittest.TestCase):
    def test_loads_public_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{root}/app-data"

[plugin.withings]
client_id = "withings-client"
secret = "withings-secret"
sync_days = 21
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.data_dir, root / "app-data")
            self.assertEqual(str(config.timezone), "Asia/Tokyo")
            self.assertEqual(config.daily_context_path, root / "app-data/generated/daily_context.md")
            self.assertEqual(config.withings.client_id, "withings-client")
            self.assertEqual(config.withings.client_secret, "withings-secret")
            self.assertEqual(config.withings.measures_csv, root / "app-data/withings/body_measures.csv")
            self.assertEqual(config.withings.activity_csv, root / "app-data/withings/activity.csv")
            self.assertEqual(config.withings.workouts_csv, root / "app-data/withings/workouts.csv")
            self.assertEqual(config.withings.sleep_csv, root / "app-data/withings/sleep.csv")
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
            self.assertFalse(config.vitalsync.enabled)
            self.assertEqual(config.vitalsync.base_url, "https://api.sazanka.io/vitalsync/v1")
            self.assertEqual(config.vitalsync.sleep_csv, root / "app-data/vitalsync/sleep.csv")
            self.assertEqual(config.vitalsync.steps_csv, root / "app-data/vitalsync/steps.csv")
            self.assertEqual(
                config.vitalsync.blood_pressure_csv,
                root / "app-data/vitalsync/blood_pressure.csv",
            )
            self.assertEqual(config.vitalsync.raw_dir, root / "app-data/vitalsync/raw")
            self.assertEqual(config.vitalsync.source_bundle_id, "com.lexwarelabs.goodmorning")
            self.assertEqual(config.vitalsync.days, 30)
            self.assertEqual(config.ui.theme, "default")
            self.assertEqual(config.ui.body_weight_goal, "maintenance")

    def test_loads_ui_theme_and_body_weight_goal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                """
[ui]
theme = "colorful"
body_weight_goal = "loss"
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.ui.theme, "colorful")
            self.assertEqual(config.ui.body_weight_goal, "loss")

    def test_rejects_unknown_ui_theme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text('[ui]\ntheme = "rainbow"\n', encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "ui.theme"):
                load_config(config_path)

    def test_rejects_unknown_body_weight_goal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                '[ui]\nbody_weight_goal = "cut"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "ui.body_weight_goal"):
                load_config(config_path)

    def test_loads_explicit_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text('[app]\ntimezone = "America/New_York"\n', encoding="utf-8")

            config = load_config(config_path)

            self.assertEqual(str(config.timezone), "America/New_York")

    def test_rejects_invalid_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text('[app]\ntimezone = "Not/AZone"\n', encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "valid IANA timezone"):
                load_config(config_path)

    def test_rejects_malformed_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text('[app]\ntimezone = "/etc/passwd"\n', encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "valid IANA timezone"):
                load_config(config_path)

    def test_loads_enabled_suunto_command_and_sync_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            config_path.write_text(
                """
[plugin.suunto]
enabled = true
command = "~/bin/suuntool"
sync_days = 14
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertTrue(config.suunto.enabled)
            self.assertEqual(config.suunto.command, str(Path("~/bin/suuntool").expanduser()))
            self.assertEqual(config.suunto.days, 14)

    def test_loads_enabled_vitalsync_config_and_sync_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            config_path.write_text(
                """
[plugin.vitalsync]
enabled = true
base_url = "https://api.example/vitalsync/v1/"
client_id = "client_123"
refresh_token = "refresh"
source_bundle_id = ""
sync_days = 10
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertTrue(config.vitalsync.enabled)
            self.assertEqual(config.vitalsync.base_url, "https://api.example/vitalsync/v1")
            self.assertEqual(config.vitalsync.client_id, "client_123")
            self.assertEqual(config.vitalsync.refresh_token, "refresh")
            self.assertEqual(config.vitalsync.source_bundle_id, "")
            self.assertEqual(config.vitalsync.days, 10)

    def test_loads_context_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                """
[context.activity]
default = "suunto"

[context.activity.workout]
default = "suunto"
sets = "hevy"
load = "suunto"

[context.measurement]
default = "withings"
steps = "vitalsync"
blood_pressure = "vitalsync"

[context.recovery]
default = "vitalsync"
sleep = "vitalsync"
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.context.activity["default"], "suunto")
            self.assertEqual(config.context.activity["workout"]["sets"], "hevy")
            self.assertEqual(config.context.measurement["steps"], "vitalsync")
            self.assertEqual(config.context.recovery["sleep"], "vitalsync")

    def test_loads_plugin_sync_days(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                """
[plugin.withings]
sync_days = 7
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
            config_path = Path(temp_dir) / "config/ingest/config.toml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("", encoding="utf-8")

            with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(Path(temp_dir) / "config")}):
                config = load_config()

            self.assertEqual(config.path, config_path)

    def test_missing_default_config_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_root = Path(temp_dir) / "config"
            config_path = config_root / "ingest/config.toml"

            with (
                patch.dict("os.environ", {"XDG_CONFIG_HOME": str(config_root)}),
                self.assertRaises(SystemExit) as context,
            ):
                load_config()

            self.assertTrue(config_path.parent.exists())
            self.assertFalse(config_path.exists())
            self.assertIn(str(config_path), str(context.exception))

    def test_updates_withings_tokens_and_rotates_refresh_token_when_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                """
[plugin.withings]
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
[plugin.withings]
refresh_token = "old-refresh"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            update_withings_tokens(config, {"access_token": "new-access", "expires_at": 123})

            updated = load_config(config_path)
            self.assertEqual(updated.withings.access_token, "new-access")
            self.assertEqual(updated.withings.refresh_token, "old-refresh")

    def test_updates_vitalsync_client_tokens_from_registration_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text(
                """
[plugin.vitalsync]
client_id = "old-client"
refresh_token = "old-refresh"
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            update_vitalsync_tokens(
                config,
                {
                    "client_id": "new-client",
                    "refresh_token": "new-refresh",
                    "access_token": "new-access",
                    "expires_at": "2026-06-29T12:00:00Z",
                },
            )

            updated = load_config(config_path)
            self.assertEqual(updated.vitalsync.client_id, "new-client")
            self.assertEqual(updated.vitalsync.refresh_token, "new-refresh")
            self.assertEqual(updated.vitalsync.access_token, "new-access")
            self.assertEqual(updated.vitalsync.expires_at, "2026-06-29T12:00:00Z")

    def test_renders_nested_tables(self) -> None:
        rendered = render_toml({"plugin": {"withings": {"sync_days": 30}}})
        self.assertIn("[plugin.withings]", rendered)
        self.assertIn("sync_days = 30", rendered)


if __name__ == "__main__":
    unittest.main()
