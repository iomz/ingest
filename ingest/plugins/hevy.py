from __future__ import annotations

import asyncio
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from ingest import prompts
from ingest.app_data import write_csv_file
from ingest.config import AppConfig
from ingest.plugins.contract import PluginCliRegistry, PluginManifest

WORKOUT_FIELDS = [
    "source",
    "source_id",
    "start_time",
    "end_time",
    "duration_min",
    "distance_km",
    "activity_type",
    "raw_type",
    "name",
    "notes",
]

SET_FIELDS = [
    "source",
    "source_id",
    "workout_source_id",
    "workout_name",
    "start_time",
    "exercise",
    "set_index",
    "set_type",
    "weight_kg",
    "reps",
    "distance_km",
    "duration_seconds",
    "rpe",
    "volume_kg",
]

SETTINGS_URL = "https://hevy.com/settings"
EXPORT_URL = "https://hevy.com/settings?export"
EXPORT_TIMEOUT_MS = 30_000
LOGIN_EMAIL_SELECTOR = (
    'input[label="Email or username"], input[label="Email"], '
    'input[type="email"], input[name*="email" i], input[autocomplete="email"]'
)
LOGIN_PASSWORD_SELECTOR = 'input[label="Password"], input[type="password"], input[name*="password" i]'
LOGIN_SUBMIT_SELECTOR = 'button[type="submit"]'

def sync(config: AppConfig) -> list[Path]:
    export_path = export_workouts_csv(config)
    return import_workouts_csv(config, export_path)


def register_cli(registry: PluginCliRegistry) -> None:
    @registry.sync_app.command("hevy")
    def sync_hevy(ctx: typer.Context) -> None:
        registry.print_paths(registry.run_sync(registry.get_config(ctx), "hevy"))

    @registry.import_app.command("hevy")
    def import_hevy(
        ctx: typer.Context,
        csv: Path = typer.Option(..., "--csv", help="Path to Hevy workout CSV export."),
    ) -> None:
        registry.print_paths(import_workouts_csv(registry.get_config(ctx), csv))

    @registry.auth_app.command("hevy")
    def auth_hevy(ctx: typer.Context) -> None:
        config = registry.get_config(ctx)
        email = prompts.text("Hevy email or username")
        password = prompts.password("Hevy password")
        authenticate(config, email=email, password=password)
        print(f"Hevy browser session ready: {config.hevy.browser_dir}")


def sync_unavailable_reason(config: AppConfig) -> str:
    if not config.hevy.configured:
        return "missing [plugin.hevy] config table"
    return ""


manifest = PluginManifest(
    name="hevy",
    provides=(
        "activity.strength.exercise",
        "activity.strength.set_type",
        "activity.strength.workout_name",
        "activity.strength.reps",
        "activity.strength.volume_kg",
    ),
    sync=sync,
    sync_unavailable_reason=sync_unavailable_reason,
    register_cli=register_cli,
)


def import_workouts_csv(config: AppConfig, csv_path: Path) -> list[Path]:
    export_rows = read_export_rows(csv_path)
    workout_rows = normalize_workout_rows(export_rows)
    set_rows = normalize_set_rows(export_rows)
    return [
        write_csv_file(config.hevy.workouts_csv, workout_rows, WORKOUT_FIELDS),
        write_csv_file(config.hevy.sets_csv, set_rows, SET_FIELDS),
    ]


def export_workouts_csv(config: AppConfig) -> Path:
    config.hevy.raw_dir.mkdir(parents=True, exist_ok=True)
    _ensure_private_browser_dir(config.hevy.browser_dir)
    target_path = config.hevy.raw_dir / "workouts_export.csv"
    return asyncio.run(_export_workouts_csv(config, target_path))


def authenticate(config: AppConfig, *, email: str, password: str) -> None:
    _ensure_private_browser_dir(config.hevy.browser_dir)
    asyncio.run(_authenticate(config, email=email, password=password))


async def _authenticate(config: AppConfig, *, email: str, password: str) -> None:
    async_playwright = _async_playwright()
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.hevy.browser_dir),
            accept_downloads=True,
            channel="chrome",
            chromium_sandbox=True,
            headless=False,
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(EXPORT_URL, wait_until="domcontentloaded")
            if await _has_login_ui(page):
                await _fill_login_form(page, email, password)
            await _wait_for_export_ui(page, config.hevy.login_timeout_seconds)
        finally:
            await context.close()


async def _export_workouts_csv(config: AppConfig, target_path: Path) -> Path:
    _ensure_private_browser_dir(config.hevy.browser_dir)
    async_playwright = _async_playwright()
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.hevy.browser_dir),
            accept_downloads=True,
            channel="chrome",
            chromium_sandbox=True,
            headless=False,
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(EXPORT_URL, wait_until="domcontentloaded")
            await _wait_for_export_ui(page, config.hevy.login_timeout_seconds, config.hevy.login_poll_interval_seconds)
            download = await _trigger_workout_export(page)
            await download.save_as(target_path)
        finally:
            await context.close()

    return target_path


def _ensure_private_browser_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def read_export_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Missing Hevy CSV export: {path}")

    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_workout_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def read_set_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


async def _wait_for_export_ui(page: Any, timeout_seconds: int, poll_interval_seconds: int = 2) -> None:
    deadline = asyncio.get_running_loop().time() + max(1, timeout_seconds)
    _status("Waiting for Hevy settings export controls. Log in in the opened browser if needed.")
    while asyncio.get_running_loop().time() < deadline:
        if await _has_export_ui(page):
            _status("Found Hevy export controls.")
            return
        if await _has_login_ui(page):
            await _wait_briefly(page, poll_interval_seconds)
            continue
        await _return_to_settings(page)
        await _scroll_settings(page)
        if await _has_export_ui(page):
            _status("Found Hevy export controls.")
            return
        _status(f"Still waiting for Hevy export controls at {page.url}")
        await _wait_briefly(page, poll_interval_seconds)
    raise SystemExit(
        "Could not find Hevy export controls. Log in in the opened browser window, "
        "open Settings if needed, then rerun `ingest sync hevy`."
    )


async def _trigger_workout_export(page: Any) -> Any:
    for action in (_click_export_workouts, _click_export_menu_then_workouts, _click_export_data_then_workouts):
        try:
            return await action(page)
        except Exception:
            continue
    raise SystemExit("Could not trigger Hevy workout export. The Hevy settings UI may have changed.")


async def _click_export_workouts(page: Any) -> Any:
    return await _download_after_click(page, _workout_export_locator(page), "Export Workout Data")


async def _click_export_menu_then_workouts(page: Any) -> Any:
    _status("Opening export menu.")
    await _first(_export_locator(page)).click(timeout=EXPORT_TIMEOUT_MS)
    return await _download_after_click(page, _workout_export_locator(page), "Export Workout Data")


async def _click_export_data_then_workouts(page: Any) -> Any:
    _status("Opening export/import data panel.")
    await _first(page.get_by_text(re.compile(r"export\s*&?\s*import\s*data", re.I))).click(timeout=EXPORT_TIMEOUT_MS)
    await _first(page.get_by_text(re.compile(r"export\s+data", re.I))).click(timeout=EXPORT_TIMEOUT_MS)
    return await _download_after_click(page, _workout_export_locator(page), "Export Workout Data")


def _export_locator(page: Any) -> Any:
    return page.get_by_text(re.compile(r"\bexport\b", re.I))


def _workout_export_locator(page: Any) -> Any:
    return page.locator("button").filter(has_text=re.compile(r"^\s*export\s+workout\s+data\s*$", re.I))


async def _download_after_click(page: Any, locator: Any, label: str) -> Any:
    button = _first(locator)
    await button.wait_for(state="visible", timeout=EXPORT_TIMEOUT_MS)
    await button.scroll_into_view_if_needed(timeout=EXPORT_TIMEOUT_MS)
    download_task = asyncio.create_task(page.wait_for_event("download", timeout=EXPORT_TIMEOUT_MS))
    try:
        for attempt in range(1, 5):
            _status(f"Clicking {label} (attempt {attempt}).")
            await _click_export_button(page, button, attempt)
            _status("Waiting for Hevy export download.")
            done, _pending = await asyncio.wait({download_task}, timeout=2)
            if done:
                return download_task.result()
        return await download_task
    except Exception:
        if not download_task.done():
            download_task.cancel()
        raise


async def _click_export_button(page: Any, button: Any, attempt: int) -> None:
    if attempt == 1:
        await button.click(timeout=EXPORT_TIMEOUT_MS)
        return
    if attempt == 2:
        await button.click(timeout=EXPORT_TIMEOUT_MS, force=True)
        return
    if attempt == 3:
        await button.evaluate("(element) => element.click()")
        return
    box = await button.bounding_box(timeout=EXPORT_TIMEOUT_MS)
    if not box:
        await button.click(timeout=EXPORT_TIMEOUT_MS, force=True)
        return
    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


async def _return_to_settings(page: Any) -> None:
    if page.url.startswith(EXPORT_URL):
        return
    try:
        await page.goto(EXPORT_URL, wait_until="domcontentloaded", timeout=EXPORT_TIMEOUT_MS)
    except Exception:
        pass


async def _scroll_settings(page: Any) -> None:
    try:
        await page.mouse.wheel(0, 1500)
    except Exception:
        pass


async def _has_export_ui(page: Any) -> bool:
    try:
        return await _first(_export_locator(page)).is_visible(timeout=1000)
    except Exception:
        return False


async def _has_login_ui(page: Any) -> bool:
    try:
        email = _first(page.locator(LOGIN_EMAIL_SELECTOR))
        password = _first(page.locator(LOGIN_PASSWORD_SELECTOR))
        return await email.is_visible(timeout=1000) and await password.is_visible(timeout=1000)
    except Exception:
        return False


async def _fill_login_form(page: Any, email: str, password: str) -> None:
    _status("Filling Hevy login form from interactive auth prompt.")
    email_input = _first(page.locator(LOGIN_EMAIL_SELECTOR))
    password_input = _first(page.locator(LOGIN_PASSWORD_SELECTOR))
    await email_input.fill(email, timeout=EXPORT_TIMEOUT_MS)
    await password_input.fill(password, timeout=EXPORT_TIMEOUT_MS)
    await _click_login_submit(page)


async def _click_login_submit(page: Any) -> None:
    submit = _first(page.locator(LOGIN_SUBMIT_SELECTOR).filter(has_text=re.compile(r"log\s*in|login|sign\s*in", re.I)))
    try:
        await page.wait_for_function(
            """selector => {
                const element = document.querySelector(selector);
                return element && getComputedStyle(element).pointerEvents !== "none";
            }""",
            arg=LOGIN_SUBMIT_SELECTOR,
            timeout=EXPORT_TIMEOUT_MS,
        )
        await submit.click(timeout=EXPORT_TIMEOUT_MS)
    except Exception:
        await submit.click(timeout=EXPORT_TIMEOUT_MS, force=True)


async def _wait_briefly(page: Any, poll_interval_seconds: int) -> None:
    await page.wait_for_timeout(max(1, poll_interval_seconds) * 1000)


def _status(message: str) -> None:
    print(f"Hevy sync: {message}", file=sys.stderr, flush=True)


def _first(locator: Any) -> Any:
    first = getattr(locator, "first")
    return first() if callable(first) else first


def normalize_workout_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    workouts: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        title = _value(row, "title", "workout name", "workout")
        start_time = _parse_hevy_time(_value(row, "start_time", "date"))
        if not start_time:
            continue

        end_time = _parse_hevy_time(_value(row, "end_time"))
        key = (title, start_time)
        workout = workouts.setdefault(
            key,
            {
                "source": "hevy",
                "source_id": _source_id(title, start_time),
                "start_time": start_time,
                "end_time": end_time,
                "duration_min": _duration_min(start_time, end_time),
                "distance_km": "",
                "activity_type": "strength",
                "raw_type": "strength",
                "name": title or "Hevy workout",
                "notes": _value(row, "description", "notes"),
            },
        )
        if end_time and not workout["end_time"]:
            workout["end_time"] = end_time
            workout["duration_min"] = _duration_min(start_time, end_time)

    return sorted(workouts.values(), key=lambda workout: str(workout.get("start_time", "")))


def normalize_set_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        title = _value(row, "title", "workout name", "workout")
        start_time = _parse_hevy_time(_value(row, "start_time", "date"))
        exercise = _value(row, "exercise_title", "exercise name", "exercise")
        if not start_time or not exercise:
            continue

        workout_source_id = _source_id(title, start_time)
        set_index = _value(row, "set_index", "set")
        weight_kg = _weight_kg(row)
        reps = _optional_float(_value(row, "reps"))
        volume_kg = (weight_kg or 0.0) * (reps or 0.0)
        normalized_rows.append(
            {
                "source": "hevy",
                "source_id": f"{workout_source_id}-{exercise}-{set_index}",
                "workout_source_id": workout_source_id,
                "workout_name": title or "Hevy workout",
                "start_time": start_time,
                "exercise": exercise,
                "set_index": set_index,
                "set_type": _value(row, "set_type"),
                "weight_kg": _format_number(weight_kg),
                "reps": _format_number(reps),
                "distance_km": _value(row, "distance_km"),
                "duration_seconds": _value(row, "duration_seconds"),
                "rpe": _value(row, "rpe"),
                "volume_kg": _format_number(volume_kg),
            }
        )
    return sorted(
        normalized_rows,
        key=lambda row: (
            str(row.get("start_time", "")),
            str(row.get("exercise", "")),
            _int_value(row.get("set_index")),
        ),
    )


def _value(row: dict[str, str], *names: str) -> str:
    normalized = {_normalize_key(key): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_normalize_key(name), "")
        if value:
            return value.strip()
    return ""


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _parse_hevy_time(value: str) -> str:
    if not value:
        return ""

    normalized = value.strip()
    for fmt in (
        "%b %d, %Y, %I:%M %p",
        "%B %d, %Y, %I:%M %p",
        "%d %b %Y, %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(normalized, fmt).isoformat()
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return ""


def _duration_min(start_time: str, end_time: str) -> str:
    if not start_time or not end_time:
        return "0.00"
    try:
        start = datetime.fromisoformat(start_time)
        end = datetime.fromisoformat(end_time)
        duration = (end - start).total_seconds()
    except (TypeError, ValueError):
        return "0.00"
    return f"{max(0.0, duration) / 60:.2f}"


def _source_id(title: str, start_time: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in title).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"{start_time}-{slug or 'workout'}"


def _optional_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _weight_kg(row: dict[str, str]) -> float | None:
    weight_kg = _optional_float(_value(row, "weight_kg", "weight"))
    if weight_kg is not None:
        return weight_kg
    weight_lbs = _optional_float(_value(row, "weight_lbs"))
    if weight_lbs is None:
        return None
    return weight_lbs * 0.45359237


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _async_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `poetry install` to install Playwright.") from exc
    return async_playwright
