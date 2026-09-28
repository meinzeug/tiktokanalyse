from io import StringIO
from pathlib import Path
import time
import unittest

from rich.console import Console

from analyse_tui import Report
from tiktokanalyse import LABELS, Sample


class TuiTests(unittest.TestCase):
    def test_all_forecast_horizons_and_controls_fit_small_terminal(self):
        samples = [Sample(100000+i*30, 100000+i*50, 10, 2, 1, 0) for i in range(121)]
        report = Report(samples, "@test", LABELS, "views", False, Path("test.jsonl"), 30)
        for width, height in ((60, 20), (80, 24), (120, 40)):
            with self.subTest(width=width, height=height):
                console = Console(width=width, height=height, file=StringIO())
                console.print(report.screen(console, 2, "Aktuell", time.monotonic()+30, False))
                rendered = console.file.getvalue()
                self.assertIn("48 h", rendered)
                self.assertIn("Vorsichtig", rendered)
                self.assertIn("q Ende", rendered)
                self.assertEqual(len(rendered.splitlines()), height)

    def test_overview_chart_is_not_cut_off(self):
        samples = [Sample(100000+i*30, 100000+i*50, 10, 2, 1, 0) for i in range(121)]
        report = Report(samples, "@test", LABELS, "views", False, Path("test.jsonl"), 30)
        console = Console(width=80, height=24, file=StringIO())
        console.print(report.screen(console, 1, "Aktuell", time.monotonic()+30, False))
        lines = console.file.getvalue().splitlines()
        self.assertTrue(lines[-4].rstrip().endswith("╯"))

    def test_empty_history_and_fetch_error_still_offer_controls(self):
        report = Report([], "@test", LABELS, "views", False, Path("test.jsonl"), 30)
        console = Console(width=80, height=24, file=StringIO())
        console.print(report.screen(console, 1, "Abruf fehlgeschlagen", time.monotonic()+30, False))
        text = console.file.getvalue()
        self.assertIn("Abruf fehlgeschlagen", text)
        self.assertIn("Sammelt Daten", text)
        self.assertIn("q Ende", text)

    def test_likes_and_comments_have_the_same_horizons_and_metric_switch(self):
        samples = [Sample(100000+i*30, 100000+i*50, 100+i, 20+i//3, 1, 0) for i in range(121)]
        report = Report(samples, "@test", LABELS, "views", False, Path("test.jsonl"), 30)
        for expected in ("likes", "comments", "views"):
            report.cycle_metric()
            self.assertEqual(report.primary, expected)
            console = Console(width=60, height=20, file=StringIO())
            console.print(report.screen(console, 2, "Aktuell", time.monotonic()+30, False))
            text = console.file.getvalue()
            self.assertIn(LABELS[expected], text)
            for hours in (1, 2, 6, 12, 24, 48):
                self.assertIn(f"{hours} h", text)
            self.assertIn("q Ende", text)
