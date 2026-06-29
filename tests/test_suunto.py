from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import anyio
from rich.console import Console

from ingest.config import load_config
from ingest.context import (
    build_daily_state,
    generate_daily_context,
    render_daily_context,
    render_daily_terminal_context,
)
from ingest.sources import suunto


class SuuntoSourceTest(unittest.TestCase):
    def test_direct_context_render_preserves_suunto_source_and_precedence(self) -> None:
        content = render_daily_context(
            date(2026, 6, 24),
            [
                {
                    "source": "withings",
                    "source_id": "mirror",
                    "start_time": "2026-06-24T14:14:21",
                    "duration_min": "140.13",
                    "distance_km": "5.20",
                    "activity_type": "walk",
                    "raw_type": "walk",
                    "name": "Imported Walk",
                },
                {
                    "source": "suunto",
                    "source_id": "suunto-walk",
                    "start_time": "2026-06-24T05:14:21+00:00",
                    "duration_min": "68.69",
                    "distance_km": "5.20",
                    "activity_type": "walk",
                    "raw_type": "WALKING",
                    "name": "Walking",
                    "tss_score": "46",
                    "tss_method": "HR",
                },
            ],
        )

        self.assertIn("| Load | TSS 46.0", content)
        self.assertIn("| TSS | 46.0 TSS |", content)
        self.assertIn("- Workout source: Suunto", content)
        self.assertIn("- Activity count: 1 primary", content)
        self.assertNotIn("Imported Walk", content)

    def test_build_daily_state_discards_withings_workouts_when_suunto_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"
timezone = "Asia/Tokyo"

[suunto]
enabled = true
""".strip(),
                encoding="utf-8",
            )
            withings_path = data_dir / "withings/workouts.csv"
            withings_path.parent.mkdir(parents=True)
            withings_path.write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,step_count,activity_type,raw_type,name",
                        "withings,elliptical-copy,2026-06-26T22:20:00+09:00,2026-06-26T23:07:00+09:00,47.00,,0,category_18,category_18,Elliptical",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            suunto_path = data_dir / "suunto/workouts.csv"
            suunto_path.parent.mkdir(parents=True)
            suunto_path.write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,crosstrainer,2026-06-26T22:20:00+09:00,2026-06-26T23:07:00+09:00,47.00,,0,crosstrainer,CROSSTRAINER,Crosstrainer,,380,126,140,33,hr,,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            state = build_daily_state(config, date(2026, 6, 26))
            content = generate_daily_context(config, date(2026, 6, 26)).read_text(encoding="utf-8")

            self.assertEqual([activity.source for activity in state.activities], ["suunto"])
            self.assertIn("- Workout source: Suunto", content)
            self.assertIn("- Activity count: 1 primary", content)
            self.assertIn("Crosstrainer", content)
            self.assertNotIn("elliptical-copy", content)
            self.assertNotIn("category_18", content)

    def test_sync_invokes_configured_command_and_merges_workout_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            command_path = root / "bin/suuntool"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f"""
[app]
data_dir = "{data_dir}"

[suunto]
enabled = true
command = "{command_path}"
""".strip(),
                encoding="utf-8",
            )
            workouts_path = data_dir / "suunto/workouts.csv"
            workouts_path.parent.mkdir(parents=True)
            workouts_path.write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,old,2026-06-01T08:00:00+09:00,2026-06-01T08:30:00+09:00,30.00,5.00,0,run,RUNNING,Running,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            start_ms = int(datetime(2026, 6, 2, 3, tzinfo=timezone.utc).timestamp() * 1000)
            workout = {
                "key": "new",
                "activityId": 21,
                "startTime": start_ms,
                "stopTime": start_ms + 1_800_000,
                "totalTime": 1800,
                "totalDistance": 1000,
                "stepCount": 12,
            }
            process = SimpleNamespace(
                returncode=0,
                stdout=(json.dumps(workout) + "\n").encode(),
                stderr=b"",
            )
            config = load_config(config_path)

            with mock.patch(
                "ingest.sources.suunto.anyio.run_process",
                new=mock.AsyncMock(return_value=process),
            ) as run_process:
                paths = anyio.run(suunto.sync_async, config)

            run_process.assert_awaited_once_with(
                [
                    str(command_path),
                    "workouts",
                    "list",
                    "--since",
                    "2026-06-01",
                    "--stream",
                ],
                check=False,
            )
            self.assertEqual(
                paths,
                [data_dir / "suunto/raw/workouts_sync.json", workouts_path],
            )
            output = workouts_path.read_text(encoding="utf-8")
            self.assertIn("suunto,old,", output)
            self.assertIn("suunto,new,", output)
            self.assertIn(",1.00,12,swim,SWIMMING,Swimming,", output)
            self.assertEqual(json.loads(paths[0].read_text(encoding="utf-8")), [workout])

    def test_sync_uses_fallback_window_without_existing_workouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "ingest.toml"
            config_path.write_text(
                f'[app]\ndata_dir = "{root / "app-data"}"\n\n[sync.suunto]\ndays = 3\n',
                encoding="utf-8",
            )
            config = load_config(config_path)

            with mock.patch(
                "ingest.sources.suunto.fetch_workouts",
                new=mock.AsyncMock(return_value=[]),
            ) as fetch_workouts:
                anyio.run(lambda: suunto.sync_async(config, end_date=date(2026, 6, 10)))

            fetch_workouts.assert_awaited_once_with(config.suunto, date(2026, 6, 8))

    def test_missing_suuntool_reports_install_and_config_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text("", encoding="utf-8")
            config = load_config(config_path)

            with mock.patch(
                "ingest.sources.suunto.anyio.run_process",
                new=mock.AsyncMock(side_effect=FileNotFoundError),
            ):
                with self.assertRaisesRegex(SystemExit, "Install and log in to suuntool"):
                    anyio.run(suunto.fetch_workouts, config.suunto, date(2026, 6, 1))

    def test_failed_suuntool_surfaces_machine_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.toml"
            config_path.write_text("", encoding="utf-8")
            config = load_config(config_path)
            process = SimpleNamespace(
                returncode=4,
                stdout=b"",
                stderr=b'{"error":{"code":"AUTH_EXPIRED","message":"login required"}}\n',
            )

            with mock.patch(
                "ingest.sources.suunto.anyio.run_process",
                new=mock.AsyncMock(return_value=process),
            ):
                with self.assertRaisesRegex(SystemExit, "AUTH_EXPIRED"):
                    anyio.run(suunto.fetch_workouts, config.suunto, date(2026, 6, 1))

    def test_rejects_malformed_ndjson(self) -> None:
        with self.assertRaisesRegex(SystemExit, "line 2"):
            suunto.parse_workouts('{"key":"ok"}\nnot-json\n')

    def test_rejects_workout_without_key(self) -> None:
        with self.assertRaisesRegex(SystemExit, "missing key"):
            suunto.normalize_workouts([{"activityId": 1, "startTime": 1_782_255_600_000}])

    def test_rejects_workout_with_invalid_start_time(self) -> None:
        with self.assertRaisesRegex(SystemExit, "invalid startTime"):
            suunto.normalize_workouts([{"key": "bad-start", "activityId": 1, "startTime": "invalid"}])

    def test_rejects_workout_without_activity_identity(self) -> None:
        with self.assertRaisesRegex(SystemExit, "invalid activityId"):
            suunto.normalize_workouts([{"key": "missing-activity", "startTime": 1_782_255_600_000}])

    def test_preserves_unknown_numeric_activity_id(self) -> None:
        rows = suunto.normalize_workouts(
            [{"key": "unknown", "activityId": 999, "startTime": 1_782_255_600_000}]
        )

        self.assertEqual(rows[0]["raw_type"], "activity_999")
        self.assertEqual(rows[0]["activity_type"], "activity 999")

    def test_names_known_indoor_and_crosstrainer_activity_ids(self) -> None:
        rows = suunto.normalize_workouts(
            [
                {"key": "indoor", "activityId": 17, "startTime": 1_782_255_600_000},
                {"key": "crosstrainer", "activityId": 55, "startTime": 1_782_259_200_000},
            ]
        )

        self.assertEqual(rows[0]["raw_type"], "INDOOR")
        self.assertEqual(rows[0]["name"], "Indoor")
        self.assertEqual(rows[1]["raw_type"], "CROSSTRAINER")
        self.assertEqual(rows[1]["name"], "Crosstrainer")

    def test_normalizes_workout_timestamps_in_utc(self) -> None:
        start_ms = int(datetime(2026, 6, 2, 23, 30, tzinfo=timezone.utc).timestamp() * 1000)

        rows = suunto.normalize_workouts(
            [
                {
                    "key": "utc-boundary",
                    "activityId": 1,
                    "startTime": start_ms,
                    "stopTime": start_ms + 1_800_000,
                }
            ]
        )

        self.assertEqual(rows[0]["start_time"], "2026-06-02T23:30:00+00:00")
        self.assertEqual(rows[0]["end_time"], "2026-06-03T00:00:00+00:00")

    def test_normalizes_optional_workout_metrics(self) -> None:
        rows = suunto.normalize_workouts(
            [
                {
                    "key": "metrics",
                    "activityId": 1,
                    "startTime": 1_782_255_600_000,
                    "totalTime": 3600,
                    "energyConsumption": 321.5,
                    "hrdata": {
                        "avg": 120,
                        "max": 160,
                        "workoutAvgHR": 145,
                        "workoutMaxHR": 172,
                    },
                    "tss": {
                        "trainingStressScore": 62.4,
                        "calculationMethod": "HR",
                        "intensityFactor": 0.84,
                    },
                    "recoveryTime": 28_800,
                    "extensions": [{"type": "FitnessExtension", "vo2Max": 52.1}],
                }
            ]
        )

        self.assertEqual(rows[0]["energy_kcal"], "321.5")
        self.assertEqual(rows[0]["avg_hr"], "145")
        self.assertEqual(rows[0]["max_hr"], "172")
        self.assertEqual(rows[0]["tss_score"], "62.4")
        self.assertEqual(rows[0]["tss_method"], "HR")
        self.assertEqual(rows[0]["intensity_factor"], "0.84")
        self.assertEqual(rows[0]["recovery_time_seconds"], "28800")

    def test_missing_optional_workout_metrics_remain_empty(self) -> None:
        row = suunto.normalize_workouts(
            [{"key": "minimal", "activityId": 1, "startTime": 1_782_255_600_000}]
        )[0]

        for field in [
            "energy_kcal",
            "avg_hr",
            "max_hr",
            "tss_score",
            "tss_method",
            "intensity_factor",
            "recovery_time_seconds",
        ]:
            self.assertEqual(row[field], "")

    def test_normalizes_fallback_hr_fields(self) -> None:
        rows = suunto.normalize_workouts(
            [
                {
                    "key": "fallback-avg-max",
                    "activityId": 1,
                    "startTime": 1_782_255_600_000,
                    "hrdata": {"avg": 111, "max": 149, "hrmax": 190},
                },
                {
                    "key": "fallback-hrmax",
                    "activityId": 1,
                    "startTime": 1_782_255_700_000,
                    "hrdata": {"avg": 112, "hrmax": 151},
                },
            ]
        )

        self.assertEqual(rows[0]["avg_hr"], "111")
        self.assertEqual(rows[0]["max_hr"], "149")
        self.assertEqual(rows[1]["avg_hr"], "112")
        self.assertEqual(rows[1]["max_hr"], "151")

    def test_daily_state_includes_normalized_suunto_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            workouts_path = data_dir / "suunto/workouts.csv"
            workouts_path.parent.mkdir(parents=True)
            workouts_path.write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,run-1,2026-06-02T12:00:00+09:00,2026-06-02T12:45:00+09:00,45.00,8.00,7000,run,RUNNING,Running,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            state = build_daily_state(load_config(config_path), date(2026, 6, 2))

            self.assertEqual(len(state.activities), 1)
            self.assertEqual(state.activities[0].source, "suunto")
            self.assertEqual(state.activities[0].activity_type, "run")
            self.assertEqual(state.activities[0].distance_km, 8.0)

    def test_report_renders_suunto_activity_and_daily_load_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            workouts_path = data_dir / "suunto/workouts.csv"
            workouts_path.parent.mkdir(parents=True)
            workouts_path.write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,prior,2026-06-01T03:00:00+00:00,2026-06-01T04:00:00+00:00,60.00,10.00,7000,run,RUNNING,Running,,,,,100,HR,,",
                        "suunto,walk-1,2026-06-02T08:00:00+00:00,2026-06-02T08:30:00+00:00,30.00,2.50,3000,walk,WALKING,Walking,,200,100,130,12.5,HR,0.60,7200",
                        "suunto,run-1,2026-06-02T12:00:00+00:00,2026-06-02T13:00:00+00:00,60.00,10.00,7000,run,RUNNING,Running,,400,130,170,30.2,POWER,0.85,28800",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            written = generate_daily_context(config, date(2026, 6, 2))
            content = written.read_text(encoding="utf-8")
            state = build_daily_state(config, date(2026, 6, 2))
            terminal_output = io.StringIO()
            render_daily_terminal_context(
                state,
                Console(file=terminal_output, width=160, color_system=None, force_terminal=False),
            )

            self.assertIn(
                "| Load | TSS 42.7 · CTL 3.3 · ATL 17.2 · TSB -13.9 · "
                "warming up |",
                content,
            )
            self.assertIn("| HR | avg 120 · max 170 |", content)
            self.assertIn("| Energy | 600 kcal |", content)
            self.assertIn(
                "Suunto metrics: TSS 42.7, average HR 120, maximum HR 170, "
                "activity energy 600 kcal, end-of-day ingest-defined CTL 3.3, "
                "ATL 17.2, TSB -13.9, TSB state warming up, "
                "Training load history limited; ATL/TSB warming up.",
                content,
            )
            self.assertNotIn("previous-day", content)
            for wording in [
                "Activity score",
                "fatigue risk",
                "Compatibility:",
                "Recovery load score",
                "Recovery Flags",
            ]:
                self.assertNotIn(wording, content)
            self.assertIn(
                "WALKING: Walking (2.50 km, 30 min, 3,000 steps, 200 kcal, "
                "HR 100-130, TSS(hr) 12.5)",
                content,
            )
            self.assertIn(
                "Load      TSS 42.7 / CTL 3.3 / ATL 17.2 / TSB -13.9 / "
                "warming up",
                terminal_output.getvalue(),
            )
            self.assertIn(
                "Training load history  Limited; ATL/TSB warming up",
                terminal_output.getvalue(),
            )
            self.assertIn("HR        avg 120 / max 170", terminal_output.getvalue())
            self.assertIn("Energy    600 kcal", terminal_output.getvalue())
            self.assertNotIn("recovery", content.lower())

    def test_report_pairs_hevy_and_suunto_strength_as_one_enriched_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            hevy_dir = data_dir / "hevy"
            hevy_dir.mkdir(parents=True)
            (hevy_dir / "workouts.csv").write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,activity_type,raw_type,name",
                        "hevy,lower-body,2026-06-05T13:52:00,2026-06-05T15:25:00,93.00,,strength,strength,Lower body",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (hevy_dir / "sets.csv").write_text(
                "\n".join(
                    [
                        "source,source_id,workout_source_id,workout_name,start_time,exercise,set_index,set_type,weight_kg,reps,distance_km,duration_seconds,rpe,volume_kg",
                        "hevy,s1,lower-body,Lower body,2026-06-05T13:52:00,Squat,1,normal,100,5,,,,500",
                        "hevy,s2,lower-body,Lower body,2026-06-05T13:52:00,Squat,2,normal,100,5,,,,500",
                        "hevy,s3,lower-body,Lower body,2026-06-05T13:52:00,Deadlift,1,normal,140,5,,,,700",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            suunto_dir = data_dir / "suunto"
            suunto_dir.mkdir(parents=True)
            (suunto_dir / "workouts.csv").write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,strength-1,2026-06-05T04:00:00+00:00,2026-06-05T05:12:00+00:00,72.00,,0,strength,STRENGTH,Strength,,420,118,156,38.4,HR,,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            state = build_daily_state(config, date(2026, 6, 5))
            content = generate_daily_context(config, date(2026, 6, 5)).read_text(
                encoding="utf-8"
            )
            terminal_output = io.StringIO()
            render_daily_terminal_context(
                state,
                Console(
                    file=terminal_output,
                    width=160,
                    color_system=None,
                    force_terminal=False,
                ),
            )

            self.assertEqual(len(state.activities), 1)
            self.assertEqual(state.activities[0].source, "suunto")
            self.assertEqual(state.activities[0].detail_source_id, "lower-body")
            self.assertEqual(state.activities[0].duration_min, 72.0)
            self.assertEqual(state.activities[0].tss_score, 38.4)
            self.assertIn("| Strength | Lower body · 72 min · 3 sets · 1700 kg |", content)
            self.assertIn("| Load | TSS 38.4", content)
            self.assertIn("| HR | avg 118 · max 156 |", content)
            self.assertIn("| Energy | 420 kcal |", content)
            self.assertIn(
                "- STRENGTH suunto:strength-1 / 72 min / 420 kcal / "
                "HR 118-156 / TSS(hr) 38.4",
                content,
            )
            self.assertIn("  - Hevy Lower body / 3 sets / 1700 kg volume", content)
            self.assertIn("- Workout source: Hevy, Suunto", content)
            self.assertIn("- Activity count: 1 primary", content)
            self.assertIn(
                "suunto:strength-1 / 72 min / 420 kcal / HR 118-156 / TSS(hr) 38.4",
                terminal_output.getvalue(),
            )
            self.assertIn(
                "Hevy Lower body / 3 sets / 1,700 kg volume",
                terminal_output.getvalue(),
            )

    def test_unpaired_hevy_strength_has_details_without_tss(self) -> None:
        content = render_daily_context(
            date(2026, 6, 5),
            [
                {
                    "source": "hevy",
                    "source_id": "hevy-only",
                    "start_time": "2026-06-05T13:00:00+09:00",
                    "duration_min": "60",
                    "activity_type": "strength",
                    "name": "Upper body",
                }
            ],
            hevy_sets=[
                {
                    "workout_source_id": "hevy-only",
                    "exercise": "Bench Press",
                    "weight_kg": "80",
                    "reps": "5",
                    "volume_kg": "400",
                }
            ],
        )

        self.assertIn("| Strength | Upper body · 60 min · 1 sets · 400 kg |", content)
        self.assertIn("- Upper body: 60 min", content)
        self.assertNotIn("| Load |", content)
        self.assertNotIn("TSS(hr)", content)

    def test_unpaired_suunto_strength_keeps_load_hr_and_energy(self) -> None:
        content = render_daily_context(
            date(2026, 6, 5),
            [
                {
                    "source": "suunto",
                    "source_id": "suunto-only",
                    "start_time": "2026-06-05T13:00:00+09:00",
                    "duration_min": "45",
                    "activity_type": "strength",
                    "raw_type": "GYM",
                    "name": "Gym",
                    "energy_kcal": "300",
                    "avg_hr": "110",
                    "max_hr": "150",
                    "tss_score": "25",
                    "tss_method": "HR",
                }
            ],
        )

        self.assertIn("| Strength | Gym · 45 min |", content)
        self.assertIn("| Load | TSS 25.0", content)
        self.assertIn("| HR | avg 110 · max 150 |", content)
        self.assertIn("| Energy | 300 kcal |", content)
        self.assertIn("- Gym: 45 min / 300 kcal / HR 110-150 / TSS(hr) 25.0", content)

    def test_ambiguous_strength_pairing_keeps_all_sessions_visible(self) -> None:
        content = render_daily_context(
            date(2026, 6, 5),
            [
                {
                    "source": "hevy",
                    "source_id": "hevy-a",
                    "start_time": "2026-06-05T13:00:00+09:00",
                    "duration_min": "60",
                    "activity_type": "strength",
                    "name": "Routine A",
                },
                {
                    "source": "hevy",
                    "source_id": "hevy-b",
                    "start_time": "2026-06-05T13:10:00+09:00",
                    "duration_min": "60",
                    "activity_type": "strength",
                    "name": "Routine B",
                },
                {
                    "source": "suunto",
                    "source_id": "suunto-ambiguous",
                    "start_time": "2026-06-05T13:05:00+09:00",
                    "duration_min": "55",
                    "activity_type": "strength",
                    "raw_type": "GYM",
                    "name": "Gym",
                    "tss_score": "30",
                    "tss_method": "HR",
                },
            ],
        )

        self.assertIn("- Routine A: 60 min", content)
        self.assertIn("- Routine B: 60 min", content)
        self.assertIn("- Gym: 55 min / TSS(hr) 30.0", content)
        self.assertIn("- Activity count: 3 primary", content)

    def test_indoor_strength_pairing_allows_small_end_gap(self) -> None:
        content = render_daily_context(
            date(2026, 6, 26),
            [
                {
                    "source": "hevy",
                    "source_id": "hevy-gap",
                    "start_time": "2026-06-26T13:00:00+09:00",
                    "duration_min": "60",
                    "activity_type": "strength",
                    "name": "Lower body",
                },
                {
                    "source": "suunto",
                    "source_id": "activity_17",
                    "start_time": "2026-06-26T14:05:00+09:00",
                    "duration_min": "55",
                    "activity_type": "indoor",
                    "raw_type": "INDOOR",
                    "name": "Indoor",
                    "energy_kcal": "420",
                    "avg_hr": "118",
                    "max_hr": "156",
                    "tss_score": "38.4",
                    "tss_method": "HR",
                },
            ],
            hevy_sets=[
                {
                    "workout_source_id": "hevy-gap",
                    "exercise": "Squat",
                    "weight_kg": "100",
                    "reps": "5",
                    "volume_kg": "500",
                }
            ],
        )

        self.assertIn("| Strength | Lower body · 55 min · 1 sets · 500 kg |", content)
        self.assertIn("| Load | TSS 38.4", content)
        self.assertIn(
            "- INDOOR suunto:activity_17 / 55 min / 420 kcal / HR 118-156 / TSS(hr) 38.4",
            content,
        )
        self.assertIn("  - Hevy Lower body / 1 sets / 500 kg volume", content)
        self.assertIn("- Activity count: 1 primary", content)
        self.assertNotIn("unknown distance", content)

    def test_persisted_activity_17_strength_pairing_uses_indoor_label(self) -> None:
        content = render_daily_context(
            date(2026, 6, 26),
            [
                {
                    "source": "hevy",
                    "source_id": "hevy-overlap",
                    "start_time": "2026-06-26T16:08:00+09:00",
                    "duration_min": "52",
                    "activity_type": "strength",
                    "name": "Lower",
                },
                {
                    "source": "suunto",
                    "source_id": "activity_17",
                    "start_time": "2026-06-26T15:58:08+09:00",
                    "end_time": "2026-06-26T17:01:30+09:00",
                    "duration_min": "63.35",
                    "activity_type": "activity 17",
                    "raw_type": "activity_17",
                    "name": "Activity 17",
                    "energy_kcal": "448",
                    "avg_hr": "115",
                    "max_hr": "153",
                    "tss_score": "45.12153",
                    "tss_method": "HR",
                },
            ],
            hevy_sets=[
                {
                    "workout_source_id": "hevy-overlap",
                    "exercise": "Squat",
                    "weight_kg": "40",
                    "reps": "8",
                    "volume_kg": "320",
                }
            ],
        )

        self.assertIn("| Strength | Lower · 63 min · 1 sets · 320 kg |", content)
        self.assertIn(
            "- INDOOR suunto:activity_17 / 63 min / 448 kcal / HR 115-153 / TSS(hr) 45.1",
            content,
        )
        self.assertIn("  - Hevy Lower / 1 sets / 320 kg volume", content)
        self.assertIn("- Activity count: 1 primary", content)
        self.assertNotIn("Activity 17", content)

    def test_crosstrainer_without_distance_renders_as_other_duration_activity(self) -> None:
        content = render_daily_context(
            date(2026, 6, 26),
            [
                {
                    "source": "suunto",
                    "source_id": "activity_55",
                    "start_time": "2026-06-26T15:30:00+09:00",
                    "duration_min": "35",
                    "activity_type": "crosstrainer",
                    "raw_type": "CROSSTRAINER",
                    "name": "Crosstrainer",
                    "energy_kcal": "260",
                    "avg_hr": "120",
                    "max_hr": "150",
                    "tss_score": "22",
                    "tss_method": "HR",
                }
            ],
        )

        self.assertIn("### Other", content)
        self.assertIn(
            "- CROSSTRAINER: Crosstrainer (35 min, 260 kcal, HR 120-150, TSS(hr) 22.0)",
            content,
        )
        self.assertNotIn("unknown distance", content)

    def test_persisted_activity_55_uses_crosstrainer_label(self) -> None:
        content = render_daily_context(
            date(2026, 6, 26),
            [
                {
                    "source": "suunto",
                    "source_id": "activity_55",
                    "start_time": "2026-06-26T17:04:36+09:00",
                    "duration_min": "47.14",
                    "activity_type": "activity 55",
                    "raw_type": "activity_55",
                    "name": "Activity 55",
                    "energy_kcal": "380",
                    "avg_hr": "126",
                    "max_hr": "140",
                    "tss_score": "32.95363",
                    "tss_method": "HR",
                }
            ],
        )

        self.assertIn(
            "- CROSSTRAINER: Crosstrainer (47 min, 380 kcal, HR 126-140, TSS(hr) 33.0)",
            content,
        )
        self.assertNotIn("Activity 55", content)
        self.assertNotIn("unknown distance", content)

    def test_report_prefers_suunto_when_withings_elapsed_duration_is_longer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            withings_dir = data_dir / "withings"
            withings_dir.mkdir(parents=True)
            (withings_dir / "workouts.csv").write_text(
                "\n".join(
                    [
                        "source,source_id,start_time,end_time,duration_min,distance_km,step_count,activity_type,raw_type,name",
                        "withings,mirror-1,2026-06-24T14:14:21,2026-06-24T16:34:29,140.13,5.20,6723,walk,walk,Imported Walk",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (withings_dir / "activity.csv").write_text(
                "date,step_count,distance_km\n2026-06-24,8949,6.10\n",
                encoding="utf-8",
            )
            vitalsync_dir = data_dir / "vitalsync"
            vitalsync_dir.mkdir(parents=True)
            (vitalsync_dir / "steps.csv").write_text(
                "source,date,step_count,distance_km\nvitalsync,2026-06-24,8949,6.10\n",
                encoding="utf-8",
            )
            (withings_dir / "body_measures.csv").write_text(
                "\n".join(
                    [
                        "grpid,date,datetime_local,type,type_name,value,unit",
                        "body-1,2026-06-24,2026-06-24T06:00:00+09:00,1,weight,98.60,kg",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            suunto_dir = data_dir / "suunto"
            suunto_dir.mkdir(parents=True)
            (suunto_dir / "workouts.csv").write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,suunto-walk,2026-06-24T05:14:21+00:00,2026-06-24T07:34:29+00:00,68.69,5.20,6620,walk,WALKING,Walking,,407,108,140,46,HR,0.72,7680",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            state = build_daily_state(config, date(2026, 6, 24))
            written = generate_daily_context(config, date(2026, 6, 24))
            content = written.read_text(encoding="utf-8")

            self.assertEqual(len(state.activities), 1)
            self.assertEqual(state.activities[0].source, "suunto")
            self.assertIn("| Movement | 8,949 steps · 5.20 km walk |", content)
            self.assertIn(
                "| Walking distance | 5.20 km | 5.20 km/week | 1.21 km/week | "
                "First recorded walk |",
                content,
            )
            self.assertIn(
                "| TSS | 46.0 TSS | 46.0 TSS/week | 10.7 TSS/week | "
                "Baseline forming |",
                content,
            )
            self.assertIn("### Workout", content)
            self.assertNotIn("### Activity", content)
            self.assertNotIn("#### Volume", content)
            self.assertNotIn("### Training Load", content)
            self.assertIn("### Performance", content)
            self.assertIn("### Body", content)
            self.assertNotIn("Walking TSS", content)
            self.assertNotIn("+1244%", content)
            self.assertIn("- Workout source: Suunto", content)
            self.assertIn("- Step source: Vitalsync", content)
            self.assertIn("- Body source: Withings", content)
            self.assertIn("- Activity count: 1 primary", content)
            self.assertIn(
                "- Training load history: Limited; ATL/TSB warming up",
                content,
            )
            self.assertIn(
                "Recorded 1 primary activity, 5.20 km walking, 69 min moving time, and 8,949 steps.",
                content,
            )
            self.assertIn(
                "| Load | TSS 46.0 · CTL 1.1 · ATL 6.1 · TSB -5.0 · "
                "warming up |",
                content,
            )
            self.assertIn("| HR | avg 108 · max 140 |", content)
            self.assertIn("| Energy | 407 kcal |", content)
            self.assertNotIn("10.40 km", content)
            self.assertNotIn("209 min moving time", content)
            self.assertNotIn("Recorded 2 primary activities", content)
            self.assertNotIn("Imported Walk", content)
            self.assertNotIn("Activity score", content)
            self.assertNotIn("2.1h", content)
            self.assertNotIn("recovery time", content.lower())

    def test_swim_day_uses_activity_first_movement_and_swimming_trends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(f'[app]\ndata_dir = "{data_dir}"\n', encoding="utf-8")
            workouts_path = data_dir / "suunto/workouts.csv"
            workouts_path.parent.mkdir(parents=True)
            workouts_path.write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,prior-run,2026-06-24T03:00:00+00:00,2026-06-24T03:30:00+00:00,30.00,5.00,0,run,RUNNING,Running,,,,,20,HR,,",
                        "suunto,today-swim,2026-06-25T03:00:00+00:00,2026-06-25T03:46:00+00:00,46.00,1.50,0,swim,SWIMMING,Swimming,,250,112,145,30.7,HR,,",
                        "suunto,today-run,2026-06-25T05:00:00+00:00,2026-06-25T05:15:00+00:00,15.00,2.00,0,run,RUNNING,Running,,,,,10,HR,,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)
            state = build_daily_state(config, date(2026, 6, 25))
            written = generate_daily_context(config, date(2026, 6, 25))
            content = written.read_text(encoding="utf-8")
            terminal_output = io.StringIO()
            render_daily_terminal_context(
                state,
                Console(file=terminal_output, width=160, color_system=None, force_terminal=False),
            )

            self.assertIn("| Movement | 46 min swim · steps unavailable |", content)
            self.assertIn("Movement  46 min swim / steps unavailable", terminal_output.getvalue())
            self.assertIn("Workout", terminal_output.getvalue())
            self.assertIn("Performance", terminal_output.getvalue())
            self.assertIn("Body", terminal_output.getvalue())
            self.assertNotIn("Training Load", terminal_output.getvalue())
            self.assertNotIn("Volume", terminal_output.getvalue())
            self.assertIn(
                "| Swimming distance | 1.50 km | 1.50 km/week | 0.35 km/week | "
                "First recorded swim |",
                content,
            )
            self.assertIn(
                "| Swimming duration | 46 min | 46 min/week | 11 min/week | "
                "First recorded swim |",
                content,
            )
            self.assertIn(
                "| Swimming pace | 3:04 min/100m | 3:04 min/100m | "
                "3:04 min/100m | Baseline forming |",
                content,
            )
            self.assertIn(
                "Swimming pace baseline is still forming.",
                content,
            )
            self.assertIn(
                "| TSS | 40.7 TSS | 60.7 TSS/week | 14.2 TSS/week | "
                "Training load history limited |",
                content,
            )
            trends = content.split("## Trends", 1)[1].split("\n## ", 1)[0]
            self.assertIn("### Workout", trends)
            self.assertIn("| Swimming distance |", trends)
            self.assertIn("| Swimming duration |", trends)
            self.assertIn("| TSS |", trends)
            self.assertIn("### Performance", trends)
            self.assertIn("| Swimming pace |", trends)
            self.assertIn("### Body", trends)
            self.assertNotIn("### Activity", trends)
            self.assertNotIn("#### Volume", trends)
            self.assertNotIn("### Training Load", trends)
            self.assertIn("| Load | TSS 40.7", content)
            self.assertNotIn("Swimming TSS", content)
            self.assertNotIn("Running TSS", content)
            self.assertNotIn("Walking distance", content)
            self.assertIn("First recorded swim", content)
            self.assertNotIn("+2900%", content)
            self.assertIn("TSB state warming up", content)
            self.assertNotIn("Fatigue / Improving fitness", content)
            self.assertIn("Swimming included 46 min and 1.50 km.", content)
            self.assertNotIn("Walking trend", content)

    def test_utc_suunto_workout_groups_by_tokyo_local_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "app-data"
            config_path = root / "ingest.toml"
            config_path.write_text(
                f'[app]\ndata_dir = "{data_dir}"\ntimezone = "Asia/Tokyo"\n',
                encoding="utf-8",
            )
            workouts_path = data_dir / "suunto/workouts.csv"
            workouts_path.parent.mkdir(parents=True)
            workouts_path.write_text(
                "\n".join(
                    [
                        ",".join(suunto.WORKOUT_FIELDS),
                        "suunto,midnight,2026-06-23T15:30:00+00:00,2026-06-23T16:00:00+00:00,30.00,2.00,2000,walk,WALKING,Walking,,,,,,,,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            previous_day = build_daily_state(config, date(2026, 6, 23))
            local_day = build_daily_state(config, date(2026, 6, 24))

            self.assertEqual(previous_day.activities, [])
            self.assertEqual(len(local_day.activities), 1)
            self.assertEqual(
                local_day.activities[0].start_time,
                "2026-06-24T00:30:00+09:00",
            )


if __name__ == "__main__":
    unittest.main()
