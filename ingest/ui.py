from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TerminalTheme:
    name: str
    styles: dict[str, str]

    def style(self, role: str) -> str:
        return self.styles[role]


_BASE_STYLES = {
    "title": "bold",
    "subsection": "bold",
    "trend_workout": "bold",
    "trend_performance": "bold",
    "trend_body": "bold",
    "label": "bold white",
    "metric_label": "bold white",
    "section_daily_snapshot": "bold cyan",
    "section_trends": "bold cyan",
    "section_body": "bold cyan",
    "section_activities": "bold cyan",
    "section_data_coverage": "bold cyan",
    "section_machine_handoff": "bold cyan",
    "primary_value": "bright_cyan",
    "muted": "dim",
    "missing": "dim",
    "limited_history": "yellow",
    "baseline_forming": "yellow",
    "positive": "green",
    "warning": "yellow",
    "negative": "red",
}

DEFAULT_THEME = TerminalTheme(name="default", styles=_BASE_STYLES)
COLORFUL_THEME = TerminalTheme(
    name="colorful",
    styles={
        **_BASE_STYLES,
        "section_daily_snapshot": "bold deep_pink2",
        "section_trends": "bold orange_red1",
        "section_body": "bold dark_cyan",
        "section_activities": "bold dodger_blue1",
        "section_data_coverage": "bold yellow2",
        "section_machine_handoff": "bold magenta",
        "trend_workout": "bold white",
        "trend_performance": "bold white",
        "trend_body": "bold white",
        "primary_value": "bright_cyan",
        "limited_history": "bold light_salmon3",
        "baseline_forming": "bold honeydew2",
        "positive": "bold spring_green2",
        "warning": "bold sandy_brown",
        "negative": "bold deep_pink2",
        "missing": "dim",
        "muted": "dim",
    },
)

THEMES = {
    DEFAULT_THEME.name: DEFAULT_THEME,
    COLORFUL_THEME.name: COLORFUL_THEME,
}


def terminal_theme(name: str) -> TerminalTheme:
    return THEMES[name]
