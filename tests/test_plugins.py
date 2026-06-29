from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

from ingest.plugins import REPOSITORY_PLUGINS, PluginLoadError, PluginManifest, load_plugin, load_repository_plugins


class PluginManifestTest(unittest.TestCase):
    def test_loads_builtin_plugins_by_name(self) -> None:
        expected_provides = {
            "hevy": {
                "activity.strength.exercise",
                "activity.strength.reps",
                "activity.strength.volume_kg",
            },
            "suunto": {
                "activity.run.distance_km",
                "activity.swim.distance_km",
                "activity.strength.tss_score",
            },
            "vitalsync": {
                "recovery.sleep.time_in_bed_min",
                "recovery.sleep.awake_min",
                "measurement.steps",
            },
            "withings": {
                "measurement.body.weight",
                "measurement.body.diastolic_blood_pressure",
                "measurement.steps",
            },
        }

        for name, provides in expected_provides.items():
            with self.subTest(name=name):
                manifest = load_plugin(name)

                self.assertEqual(manifest.name, name)
                self.assertTrue(provides.issubset(set(manifest.provides)))
                self.assertIsNotNone(manifest.sync)
                self.assertIsNotNone(manifest.register_cli)

    def test_loads_repository_plugins_from_package_list(self) -> None:
        manifests = load_repository_plugins()

        self.assertEqual(tuple(manifest.name for manifest in manifests), REPOSITORY_PLUGINS)

    def test_unknown_plugin_gives_clear_error(self) -> None:
        with self.assertRaisesRegex(PluginLoadError, "Unknown ingest plugin 'missing'"):
            load_plugin("missing")

    def test_malformed_plugin_module_gives_clear_error(self) -> None:
        module_name = "ingest.plugins.malformed"
        module = types.ModuleType(module_name)
        sys.modules[module_name] = module
        try:
            with self.assertRaisesRegex(PluginLoadError, "must export PluginManifest as manifest"):
                load_plugin("malformed")
        finally:
            sys.modules.pop(module_name, None)

    def test_manifest_name_mismatch_gives_clear_error(self) -> None:
        module_name = "ingest.plugins.badname"
        module = types.ModuleType(module_name)
        module.manifest = PluginManifest(name="other", provides=("activity.example",))
        sys.modules[module_name] = module
        try:
            with self.assertRaisesRegex(PluginLoadError, "manifest name mismatch"):
                load_plugin("badname")
        finally:
            sys.modules.pop(module_name, None)

    def test_manifest_callable_signature_mismatch_gives_clear_error(self) -> None:
        module_name = "ingest.plugins.badsync"
        module = types.ModuleType(module_name)
        module.manifest = PluginManifest(name="badsync", provides=("activity.example",), sync=lambda: [])
        sys.modules[module_name] = module
        try:
            with self.assertRaisesRegex(PluginLoadError, "manifest sync must accept 1 argument"):
                load_plugin("badsync")
        finally:
            sys.modules.pop(module_name, None)

    def test_cli_core_does_not_define_plugin_specific_commands(self) -> None:
        root = Path(__file__).resolve().parents[1]
        cli_source = root.joinpath("ingest/cli.py").read_text(encoding="utf-8")

        for forbidden in [
            "hevy",
            "suunto",
            "vitalsync",
            "withings",
            "def sync_hevy",
            "def sync_suunto",
            "def sync_withings",
            "def auth_withings",
            "def auth_vitalsync",
            "register-client",
            "exchange-code",
        ]:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, cli_source)

    def test_strava_assumptions_are_gone_from_code_tree(self) -> None:
        root = Path(__file__).resolve().parents[1]
        matches: list[Path] = []
        for path in [*root.joinpath("ingest").rglob("*.py"), *root.joinpath("tests").rglob("*.py")]:
            if path.name == "test_plugins.py":
                continue
            if "strava" in path.read_text(encoding="utf-8").lower():
                matches.append(path.relative_to(root))

        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
