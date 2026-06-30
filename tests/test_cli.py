from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import anyio

from ingest.cli import _sync_all_async, app, main
from typer.testing import CliRunner
from ingest.config import load_config
from ingest.plugins import PluginManifest
from ingest.plugins import hevy, suunto, vitalsync, withings


@contextlib.contextmanager
def patch_manifest_sync(plugin_module: object, **mock_kwargs: object):
    sync_mock = mock.Mock(**mock_kwargs)
    manifest = plugin_module.manifest
    original_sync = manifest.sync
    original_sync_scoped = manifest.sync_scoped
    object.__setattr__(manifest, "sync", sync_mock)
    if original_sync_scoped is not None:
        object.__setattr__(manifest, "sync_scoped", lambda config, _scope: sync_mock(config))
    try:
        yield sync_mock
    finally:
        object.__setattr__(manifest, "sync", original_sync)
        object.__setattr__(manifest, "sync_scoped", original_sync_scoped)


def write_auth_state(data_dir: Path, plugin: str, state: dict[str, object]) -> None:
    path = data_dir / plugin / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


class CliTest(unittest.TestCase):
    def test_parser_accepts_new_sync_commands(self) -> None:
        runner = CliRunner()
        help_paths = [
            ["today", "--help"],
            ["day", "--help"],
            ["yesterday", "--help"],
            ["sync", "hevy", "--help"],
            ["sync", "suunto", "--help"],
            ["sync", "vitalsync", "--help"],
            ["sync", "withings", "--help"],
            ["sync", "all", "--help"],
            ["import", "hevy", "--help"],
            ["auth", "withings", "--help"],
            ["auth", "withings", "auth-url", "--help"],
            ["auth", "hevy", "--help"],
            ["auth", "vitalsync", "register-client", "--help"],
            ["auth", "vitalsync", "refresh-token", "--help"],
        ]

        for args in help_paths:
            with self.subTest(args=args):
                result = runner.invoke(app, args)
                self.assertEqual(result.exit_code, 0, result.output)

        self.assertIn("--backfill-since", runner.invoke(app, ["sync", "withings", "--help"]).output)

    def test_parser_rejects_removed_alias_commands(self) -> None:
        runner = CliRunner()

        self.assertNotEqual(runner.invoke(app, ["context", "today"]).exit_code, 0)

        self.assertNotEqual(runner.invoke(app, ["withings", "sync"]).exit_code, 0)

        self.assertNotEqual(runner.invoke(app, ["oauth", "withings", "auth-url"]).exit_code, 0)

        self.assertNotEqual(runner.invoke(app, ["backfill", "withings", "--from", "2026-01-01"]).exit_code, 0)

    def test_auth_withings_auth_url_prints_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n\n[plugin.withings]\n', encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "auth",
                        "withings",
                        "auth-url",
                        "--redirect-uri",
                        "https://callback.example",
                        "--client-id",
                        "client",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("https://account.withings.com/oauth2_user/authorize2", output)
            self.assertIn("client_id=client", output)

    def test_auth_withings_wizard_uses_local_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n\n[plugin.withings]\n', encoding="utf-8")

            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                mock.patch(
                    "ingest.plugins.withings.prompts.text",
                    side_effect=["client", "http://127.0.0.1:8000/callback"],
                ) as text_prompt,
                mock.patch("ingest.plugins.withings.prompts.password", return_value="secret") as password_prompt,
                mock.patch("ingest.plugins.withings.prompts.confirm", return_value=True) as confirm_prompt,
                mock.patch(
                    "ingest.plugins.withings.authorization_url", return_value="https://withings.example/auth"
                ) as auth_url,
                mock.patch("ingest.plugins.withings.capture_local_oauth_code", return_value="code") as capture_code,
                mock.patch("ingest.plugins.withings.exchange_authorization_code") as exchange_code,
                mock.patch("ingest.plugins.withings.secrets.token_urlsafe", return_value="state"),
            ):
                exit_code = main(["--config", str(config_path), "auth", "withings"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(text_prompt.call_count, 2)
            password_prompt.assert_called_once_with("Withings client secret")
            confirm_prompt.assert_called_once()
            auth_url.assert_called_once()
            _config_arg, auth_kwargs = auth_url.call_args
            self.assertEqual(auth_kwargs["state"], "state")
            capture_code.assert_called_once_with(
                "https://withings.example/auth",
                "http://127.0.0.1:8000/callback",
                expected_state="state",
                timeout_seconds=300,
            )
            exchange_code.assert_called_once()
            _config_arg, kwargs = exchange_code.call_args
            self.assertEqual(
                kwargs,
                {
                    "code": "code",
                    "redirect_uri": "http://127.0.0.1:8000/callback",
                    "client_id": "client",
                    "client_secret": "secret",
                },
            )
            self.assertIn("https://withings.example/auth", stdout.getvalue())
            self.assertIn(str(data_dir / "withings/auth.json"), stdout.getvalue())

    def test_auth_withings_wizard_accepts_pasted_redirect_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n\n[plugin.withings]\n', encoding="utf-8")

            with (
                mock.patch(
                    "ingest.plugins.withings.prompts.text",
                    side_effect=[
                        "client",
                        "https://callback.example/withings",
                        "https://callback.example/withings?state=state&code=code",
                    ],
                ),
                mock.patch("ingest.plugins.withings.prompts.password", return_value="secret"),
                mock.patch("ingest.plugins.withings.authorization_url", return_value="https://withings.example/auth"),
                mock.patch("ingest.plugins.withings.exchange_authorization_code") as exchange_code,
                mock.patch("ingest.plugins.withings.secrets.token_urlsafe", return_value="state"),
            ):
                exit_code = main(["--config", str(config_path), "auth", "withings"])

            self.assertEqual(exit_code, 0)
            _config_arg, kwargs = exchange_code.call_args
            self.assertEqual(kwargs["code"], "code")

    def test_auth_hevy_prompts_and_saves_browser_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n\n[plugin.hevy]\n', encoding="utf-8")

            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                mock.patch("ingest.plugins.hevy.prompts.text", return_value="iori@example.com") as text_prompt,
                mock.patch("ingest.plugins.hevy.prompts.password", return_value="secret") as password_prompt,
                mock.patch("ingest.plugins.hevy.authenticate") as authenticate,
            ):
                exit_code = main(["--config", str(config_path), "auth", "hevy"])

            self.assertEqual(exit_code, 0)
            text_prompt.assert_called_once_with("Hevy email or username")
            password_prompt.assert_called_once_with("Hevy password")
            authenticate.assert_called_once()
            _config_arg, kwargs = authenticate.call_args
            self.assertEqual(kwargs, {"email": "iori@example.com", "password": "secret"})
            self.assertIn(str(data_dir / "hevy/browser"), stdout.getvalue())

    def test_auth_vitalsync_register_client_saves_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f'[app]\ndata_dir = "{data_dir}"\n\n[plugin.vitalsync]\nendpoint = "https://receiver.example/vitalsync/v1"\n',
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                mock.patch("ingest.plugins.vitalsync.register_client") as register_client,
            ):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "auth",
                        "vitalsync",
                        "register-client",
                        "--pairing-token",
                        "pair",
                        "--client-label",
                        "ingest on test",
                    ]
                )

            self.assertEqual(exit_code, 0)
            register_client.assert_called_once()
            _config_arg, kwargs = register_client.call_args
            self.assertEqual(kwargs["pairing_token"], "pair")
            self.assertEqual(kwargs["client_label"], "ingest on test")
            self.assertEqual(stdout.getvalue(), f"{data_dir / 'vitalsync/auth.json'}\n")

    def test_auth_vitalsync_refresh_token_saves_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n\n[plugin.vitalsync]\n', encoding="utf-8")
            write_auth_state(data_dir, "vitalsync", {"client_id": "client", "refresh_token": "refresh"})

            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                mock.patch("ingest.plugins.vitalsync.refresh_configured_access_token") as refresh_token,
            ):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "auth",
                        "vitalsync",
                        "refresh-token",
                    ]
                )

            self.assertEqual(exit_code, 0)
            refresh_token.assert_called_once()
            self.assertEqual(stdout.getvalue(), f"{data_dir / 'vitalsync/auth.json'}\n")

    def test_ingest_day_prints_terminal_content_without_sync_when_data_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[plugin.hevy]\n\n"
                    '[context.activity]\nworkout = "withings"\n'
                ),
                encoding="utf-8",
            )
            withings_csv_path = data_dir / "withings/body_measures.csv"
            withings_csv_path.parent.mkdir(parents=True)
            withings_csv_path.write_text(
                "\n".join(
                    [
                        "grpid,date,datetime_local,type,type_name,value,unit",
                        "1,2026-05-29,2026-05-29T06:00:00,1,weight,70.50,kg",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            workouts_csv_path = data_dir / "withings/workouts.csv"
            workouts_csv_path.write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type",
                        "withings,1,2026-05-29T08:00:00,2026-05-29T08:30:00,30.00,1.00,walk,walk",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                patch_manifest_sync(withings) as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "day",
                        "2026-05-29",
                    ]
                )

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            output = stdout.getvalue()
            context_path = data_dir / "generated/daily_context.md"
            self.assertIn("Physical Context — 2026-05-29", output)
            self.assertIn("Daily Snapshot", output)
            self.assertIn("    walk  withings:1 / 1.00 km / 30 min", output)
            self.assertFalse(context_path.exists())

    def test_ingest_day_does_not_sync_missing_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                (f'[app]\ndata_dir = "{data_dir}"\n\n' '[context.activity]\nworkout = "hevy"\n'),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                patch_manifest_sync(withings) as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "day",
                        "2026-05-29",
                    ]
                )

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            self.assertIn("No primary activities found", stdout.getvalue())

    def test_ingest_day_uses_hevy_activity_without_withings_workout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                (f'[app]\ndata_dir = "{data_dir}"\n\n' '[context.activity]\nworkout = "hevy"\n'),
                encoding="utf-8",
            )
            withings_csv_path = data_dir / "withings/body_measures.csv"
            withings_csv_path.parent.mkdir(parents=True)
            withings_csv_path.write_text(
                "\n".join(
                    [
                        "grpid,date,datetime_local,type,type_name,value,unit",
                        "1,2026-05-29,2026-05-29T06:00:00,1,weight,70.50,kg",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            hevy_csv_path = data_dir / "hevy/workouts.csv"
            hevy_csv_path.parent.mkdir(parents=True)
            hevy_csv_path.write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type,name",
                        "hevy,push,2026-05-29T17:29:00,2026-05-29T18:45:00,76.00,,strength,strength,Push Day",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                patch_manifest_sync(withings) as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "day", "2026-05-29"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            self.assertIn("Workout: Hevy", stdout.getvalue())
            self.assertIn("Measurement: Withings", stdout.getvalue())
            self.assertIn("Workout", stdout.getvalue())
            self.assertIn("    Push Day / 76 min", stdout.getvalue())
            self.assertNotIn("unknown distance", stdout.getvalue())

    def test_import_hevy_writes_normalized_workouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            export_path = root / "hevy.csv"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[plugin.hevy]\n\n"
                    "[plugin.suunto]\nenabled = false\n\n"
                    "[plugin.vitalsync]\nenabled = false\n\n"
                    "[plugin.withings]\n\n"
                    '[context.activity]\nworkout = "withings"\n'
                ),
                encoding="utf-8",
            )
            write_auth_state(data_dir, "withings", {"access_token": "access"})
            export_path.write_text(
                "\n".join(
                    [
                        "title,start_time,end_time,exercise_title,set_index",
                        'Push Day,"28 Mar 2025, 17:29","28 Mar 2025, 18:45",Bench Press,1',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["--config", str(config_path), "import", "hevy", "--csv", str(export_path)])

            self.assertEqual(exit_code, 0)
            output_path = data_dir / "hevy/workouts.csv"
            sets_path = data_dir / "hevy/sets.csv"
            self.assertEqual(stdout.getvalue(), f"{output_path}\n{sets_path}\n")
            self.assertIn("Push Day", output_path.read_text(encoding="utf-8"))

    def test_sync_hevy_prints_written_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            output_path = data_dir / "hevy/workouts.csv"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[plugin.hevy]\n\n"
                    '[context.activity]\nworkout = "withings"\n'
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            manifest = PluginManifest(
                name="hevy",
                provides=("activity.strength.workout_name",),
                sync=mock.Mock(return_value=[output_path]),
            )
            with (
                mock.patch("ingest.cli.load_plugin", return_value=manifest) as load_plugin,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "sync", "hevy"])

            self.assertEqual(exit_code, 0)
            load_plugin.assert_has_calls([mock.call("hevy"), mock.call("hevy")])
            manifest.sync.assert_called_once()
            self.assertEqual(stdout.getvalue(), f"{output_path}\n")

    def test_sync_all_includes_hevy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            withings_path = data_dir / "withings/body_measures.csv"
            hevy_path = data_dir / "hevy/workouts.csv"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[plugin.hevy]\n\n"
                    "[plugin.suunto]\nenabled = false\n\n"
                    "[plugin.vitalsync]\nenabled = false\n\n"
                    "[plugin.withings]\n\n"
                    '[context.activity]\nworkout = "withings"\n'
                ),
                encoding="utf-8",
            )
            write_auth_state(data_dir, "withings", {"access_token": "access"})

            stdout = io.StringIO()
            with (
                patch_manifest_sync(withings, return_value=[withings_path]) as withings_sync,
                patch_manifest_sync(hevy, return_value=[hevy_path]) as hevy_sync,
                patch_manifest_sync(suunto) as suunto_sync,
                patch_manifest_sync(vitalsync) as vitalsync_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "sync", "all"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_called_once()
            hevy_sync.assert_called_once()
            suunto_sync.assert_not_called()
            vitalsync_sync.assert_not_called()
            self.assertEqual(stdout.getvalue(), f"{hevy_path}\n{withings_path}\n")

    def test_sync_all_includes_enabled_suunto(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            withings_path = data_dir / "withings/body_measures.csv"
            hevy_path = data_dir / "hevy/workouts.csv"
            suunto_path = data_dir / "suunto/workouts.csv"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[plugin.hevy]\n\n"
                    "[plugin.suunto]\nenabled = true\n\n"
                    "[plugin.vitalsync]\nenabled = false\n\n"
                    "[plugin.withings]\n"
                ),
                encoding="utf-8",
            )
            write_auth_state(data_dir, "withings", {"access_token": "access"})

            stdout = io.StringIO()
            with (
                patch_manifest_sync(withings, return_value=[withings_path]),
                patch_manifest_sync(hevy, return_value=[hevy_path]),
                patch_manifest_sync(suunto, return_value=[suunto_path]) as suunto_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "sync", "all"])

            self.assertEqual(exit_code, 0)
            suunto_sync.assert_called_once()
            self.assertEqual(stdout.getvalue(), f"{hevy_path}\n{suunto_path}\n{withings_path}\n")

    def test_sync_all_includes_enabled_vitalsync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            withings_path = data_dir / "withings/body_measures.csv"
            hevy_path = data_dir / "hevy/workouts.csv"
            vitalsync_path = data_dir / "vitalsync/sleep.csv"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[plugin.hevy]\n\n"
                    "[plugin.suunto]\nenabled = false\n\n"
                    "[plugin.vitalsync]\nenabled = true\n\n"
                    "[plugin.withings]\n"
                ),
                encoding="utf-8",
            )
            write_auth_state(data_dir, "vitalsync", {"access_token": "access"})
            write_auth_state(data_dir, "withings", {"access_token": "access"})

            stdout = io.StringIO()
            with (
                patch_manifest_sync(withings, return_value=[withings_path]),
                patch_manifest_sync(hevy, return_value=[hevy_path]),
                patch_manifest_sync(vitalsync, return_value=[vitalsync_path]) as vitalsync_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "sync", "all"])

            self.assertEqual(exit_code, 0)
            vitalsync_sync.assert_called_once()
            self.assertEqual(stdout.getvalue(), f"{hevy_path}\n{vitalsync_path}\n{withings_path}\n")

    def test_sync_all_serializes_config_mutating_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[plugin.hevy]\n\n"
                    "[plugin.suunto]\nenabled = false\n\n"
                    "[plugin.vitalsync]\nenabled = true\n\n"
                    "[plugin.withings]\n"
                ),
                encoding="utf-8",
            )
            write_auth_state(data_dir, "vitalsync", {"access_token": "access"})
            write_auth_state(data_dir, "withings", {"access_token": "access"})
            active_sources: set[str] = set()
            active_lock = threading.Lock()
            overlaps: list[tuple[str, str]] = []

            def sync_source(name: str) -> list[Path]:
                with active_lock:
                    for active_source in active_sources:
                        if {name, active_source} == {"vitalsync", "withings"}:
                            overlaps.append((name, active_source))
                    active_sources.add(name)
                time.sleep(0.05)
                with active_lock:
                    active_sources.remove(name)
                return []

            with (
                patch_manifest_sync(withings, side_effect=lambda _config: sync_source("withings")),
                patch_manifest_sync(hevy, return_value=[]),
                patch_manifest_sync(vitalsync, side_effect=lambda _config: sync_source("vitalsync")),
            ):
                exit_code = main(["--config", str(config_path), "sync", "all"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(overlaps, [])

    def test_sync_all_fetches_sources_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[plugin.hevy]

[plugin.withings]
""".strip(),
                encoding="utf-8",
            )
            write_auth_state(data_dir, "withings", {"access_token": "access"})
            barrier = threading.Barrier(2, timeout=1)

            def sync_source(_config: object) -> list[Path]:
                barrier.wait()
                return []

            with (
                patch_manifest_sync(withings, side_effect=sync_source),
                patch_manifest_sync(hevy, side_effect=sync_source),
            ):
                exit_code = main(["--config", str(config_path), "sync", "all"])

            self.assertEqual(exit_code, 0)

    def test_sync_explicit_disabled_plugin_warns_and_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text("[plugin.hevy]\nenabled = false\n", encoding="utf-8")
            stderr = io.StringIO()

            with (
                patch_manifest_sync(hevy, return_value=[]) as hevy_sync,
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main(["--config", str(config_path), "sync", "hevy"])

            self.assertEqual(exit_code, 0)
            hevy_sync.assert_not_called()
            self.assertIn("plugin.hevy is disabled; skipping.", stderr.getvalue())

    def test_sync_hevy_runs_without_plugin_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            output_path = data_dir / "hevy/workouts.csv"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            stdout = io.StringIO()

            with (
                patch_manifest_sync(hevy, return_value=[output_path]) as hevy_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "sync", "hevy"])

            self.assertEqual(exit_code, 0)
            hevy_sync.assert_called_once()
            self.assertEqual(stdout.getvalue(), f"{output_path}\n")

    def test_sync_withings_runs_with_auth_state_without_plugin_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            output_path = data_dir / "withings/body_measures.csv"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            write_auth_state(data_dir, "withings", {"access_token": "access"})
            stdout = io.StringIO()

            with (
                patch_manifest_sync(withings, return_value=[output_path]) as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "sync", "withings"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_called_once()
            self.assertEqual(stdout.getvalue(), f"{output_path}\n")

    def test_sync_withings_backfill_since_runs_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            output_path = data_dir / "withings/body_measures.csv"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            write_auth_state(data_dir, "withings", {"access_token": "access"})
            stdout = io.StringIO()

            with (
                patch_manifest_sync(withings) as withings_sync,
                mock.patch("ingest.plugins.withings.backfill", return_value=[output_path]) as backfill,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "sync",
                        "withings",
                        "--backfill-since",
                        "2026-01-01",
                        "--end-date",
                        "2026-01-31",
                    ]
                )

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            backfill.assert_called_once()
            _config_arg, kwargs = backfill.call_args
            self.assertEqual(kwargs["start_date"].isoformat(), "2026-01-01")
            self.assertEqual(kwargs["end_date"].isoformat(), "2026-01-31")
            self.assertEqual(stdout.getvalue(), f"{output_path}\n")

    def test_sync_all_does_not_swallow_base_exceptions(self) -> None:
        class CancellationSignal(BaseException):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            config_path.write_text(
                f'[app]\ndata_dir = "{root / "app-data"}"\n\n[plugin.suunto]\nenabled = true\n',
                encoding="utf-8",
            )
            config = load_config(config_path)

            with (
                patch_manifest_sync(withings, return_value=[]),
                patch_manifest_sync(hevy, return_value=[]),
                patch_manifest_sync(suunto, side_effect=CancellationSignal),
            ):
                with self.assertRaises(BaseExceptionGroup) as raised:
                    anyio.run(_sync_all_async, config)

            self.assertTrue(any(isinstance(exc, CancellationSignal) for exc in raised.exception.exceptions))

    def test_today_sync_runs_all_sources_before_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            withings_path = data_dir / "withings/body_measures.csv"
            hevy_path = data_dir / "hevy/workouts.csv"
            config_path.write_text(
                (
                    f'[app]\ndata_dir = "{data_dir}"\n\n'
                    "[plugin.hevy]\n\n"
                    "[plugin.suunto]\nenabled = false\n\n"
                    "[plugin.vitalsync]\nenabled = false\n\n"
                    "[plugin.withings]\n"
                ),
                encoding="utf-8",
            )
            write_auth_state(data_dir, "withings", {"access_token": "access"})

            stdout = io.StringIO()
            with (
                mock.patch(
                    "ingest.cli._local_today",
                    return_value=__import__("datetime").date(2026, 5, 29),
                ),
                patch_manifest_sync(withings, return_value=[withings_path]) as withings_sync,
                patch_manifest_sync(hevy, return_value=[hevy_path]) as hevy_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "today", "--sync"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_called_once()
            hevy_sync.assert_called_once()
            self.assertIn("Physical Context — 2026-05-29", stdout.getvalue())
            self.assertIn("Daily Snapshot", stdout.getvalue())
            self.assertNotIn("# Physical Context - 2026-05-29", stdout.getvalue())

    def test_ingest_yesterday_uses_previous_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            withings_csv_path = data_dir / "withings/body_measures.csv"
            withings_csv_path.parent.mkdir(parents=True)
            withings_csv_path.write_text(
                "\n".join(
                    [
                        "grpid,date,datetime_local,type,type_name,value,unit",
                        "1,2026-06-02,2026-06-02T06:00:00,1,weight,70.50,kg",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            workouts_csv_path = data_dir / "withings/workouts.csv"
            workouts_csv_path.write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type",
                        "withings,1,2026-06-02T08:00:00,2026-06-02T08:30:00,30.00,1.00,walk,walk",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                mock.patch(
                    "ingest.cli._local_today",
                    return_value=__import__("datetime").date(2026, 6, 3),
                ),
                patch_manifest_sync(withings) as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "yesterday"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            self.assertIn("Physical Context — 2026-06-02", stdout.getvalue())
            self.assertNotIn("# Physical Context - 2026-06-02", stdout.getvalue())

    def test_ingest_today_renders_without_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                (f'[app]\ndata_dir = "{data_dir}"\n\n' '[context.activity]\nworkout = "withings"\n'),
                encoding="utf-8",
            )
            withings_csv_path = data_dir / "withings/body_measures.csv"
            withings_csv_path.parent.mkdir(parents=True)
            withings_csv_path.write_text(
                "\n".join(
                    [
                        "grpid,date,datetime_local,type,type_name,value,unit",
                        "1,2026-05-29,2026-05-29T06:00:00,1,weight,70.50,kg",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            workouts_csv_path = data_dir / "withings/workouts.csv"
            workouts_csv_path.write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type",
                        "withings,1,2026-05-29T08:00:00,2026-05-29T08:30:00,30.00,1.00,walk,walk",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                mock.patch(
                    "ingest.cli._local_today",
                    return_value=__import__("datetime").date(2026, 5, 29),
                ),
                patch_manifest_sync(withings) as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "today"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            output = stdout.getvalue()
            self.assertIn("Physical Context — 2026-05-29", output)
            self.assertIn("Daily Snapshot", output)
            self.assertIn("Movement  unavailable steps / 1.00 km walk", output)
            self.assertNotIn("Activity score", output)
            self.assertNotIn("·", output)
            self.assertNotIn("| Area | Status |", output)

    def test_ingest_today_markdown_prints_generated_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                (f'[app]\ndata_dir = "{data_dir}"\n\n' '[context.activity]\nworkout = "withings"\n'),
                encoding="utf-8",
            )
            withings_csv_path = data_dir / "withings/body_measures.csv"
            withings_csv_path.parent.mkdir(parents=True)
            withings_csv_path.write_text(
                "\n".join(
                    [
                        "grpid,date,datetime_local,type,type_name,value,unit",
                        "1,2026-05-29,2026-05-29T06:00:00,1,weight,70.50,kg",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with (
                mock.patch(
                    "ingest.cli._local_today",
                    return_value=__import__("datetime").date(2026, 5, 29),
                ),
                patch_manifest_sync(withings) as withings_sync,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "today", "--markdown"])

            self.assertEqual(exit_code, 0)
            withings_sync.assert_not_called()
            output = stdout.getvalue()
            context_path = data_dir / "generated/daily_context.md"
            self.assertTrue(output.startswith("# Physical Context - 2026-05-29\n"))
            self.assertEqual(output, context_path.read_text(encoding="utf-8"))

    def test_ingest_day_markdown_prints_generated_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["--config", str(config_path), "day", "2026-05-29", "--markdown"])

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            context_path = data_dir / "generated/daily_context.md"
            self.assertTrue(output.startswith("# Physical Context - 2026-05-29\n"))
            self.assertEqual(output, context_path.read_text(encoding="utf-8"))

    def test_ingest_yesterday_markdown_prints_generated_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")

            stdout = io.StringIO()
            with (
                mock.patch(
                    "ingest.cli._local_today",
                    return_value=__import__("datetime").date(2026, 5, 30),
                ),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(["--config", str(config_path), "yesterday", "--markdown"])

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            context_path = data_dir / "generated/daily_context.md"
            self.assertTrue(output.startswith("# Physical Context - 2026-05-29\n"))
            self.assertEqual(output, context_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
