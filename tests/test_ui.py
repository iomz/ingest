from __future__ import annotations

import unittest

from ingest.ui import terminal_theme


class UITest(unittest.TestCase):
    def test_default_theme_keeps_section_headers_calm(self) -> None:
        theme = terminal_theme("default")

        self.assertEqual(theme.style("section_daily_snapshot"), "bold cyan")
        self.assertEqual(theme.style("section_trends"), "bold cyan")
        self.assertEqual(theme.style("section_body"), "bold cyan")
        self.assertEqual(theme.style("trend_body"), "bold")

    def test_colorful_theme_uses_named_section_roles(self) -> None:
        theme = terminal_theme("colorful")

        self.assertEqual(theme.style("section_daily_snapshot"), "bold deep_pink2")
        self.assertEqual(theme.style("section_trends"), "bold orange_red1")
        self.assertEqual(theme.style("section_body"), "bold dark_cyan")
        self.assertEqual(theme.style("section_activities"), "bold dodger_blue1")
        self.assertEqual(theme.style("section_data_coverage"), "bold yellow2")
        self.assertEqual(theme.style("section_machine_handoff"), "bold magenta")
        self.assertEqual(theme.style("trend_workout"), "bold white")
        self.assertEqual(theme.style("trend_performance"), "bold white")
        self.assertEqual(theme.style("trend_body"), "bold white")
        self.assertEqual(theme.style("primary_value"), "bright_cyan")
        self.assertEqual(theme.style("positive"), "bold spring_green2")
        self.assertEqual(theme.style("warning"), "bold sandy_brown")
        self.assertEqual(theme.style("negative"), "bold deep_pink2")
        self.assertEqual(theme.style("limited_history"), "bold light_salmon3")
        self.assertEqual(theme.style("baseline_forming"), "bold honeydew2")

    def test_missing_theme_role_fails_clearly(self) -> None:
        theme = terminal_theme("default")

        with self.assertRaisesRegex(KeyError, "missing_role"):
            theme.style("missing_role")


if __name__ == "__main__":
    unittest.main()
