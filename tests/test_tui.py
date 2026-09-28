from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from rich.console import Console

from analyse_tui import Report
from kanalanalyse import LABELS as CHANNEL_LABELS, ProfileSample
from prognose_lernen import Learner
from prognose_zeiten import HORIZON_RANGES, horizon_label
from tiktokanalyse import LABELS, Sample


class TuiTests(unittest.TestCase):
    def test_all_forecast_horizons_and_controls_fit_small_terminal(self):
        samples = [Sample(100000+i*30, 100000+i*50, 10, 2, 1, 0) for i in range(121)]
        report = Report(samples, "@test", LABELS, "views", False, Path("test.jsonl"), 30)
        self.assertEqual(report.horizon, "4w")
        for horizon, (title, seconds) in HORIZON_RANGES.items():
            report.horizon = horizon
            for width, height in ((60, 20), (80, 24), (120, 40)):
                with self.subTest(horizon=horizon, width=width, height=height):
                    console = Console(width=width, height=height, file=StringIO())
                    console.print(report.screen(console, 2, "Aktuell", time.monotonic()+30, False))
                    rendered = console.file.getvalue()
                    for value in seconds:
                        self.assertIn(horizon_label(value, hours_only=horizon == "48h"), rendered)
                    self.assertIn(title, rendered)
                    self.assertIn("Vorsichtig", rendered)
                    self.assertIn("geprüft: 0/6 Ziele", rendered)
                    self.assertIn("-/+ Zeitraum", rendered)
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
            for days in (1, 2, 7, 14, 21, 28):
                self.assertIn(f"{days} d", text)
            self.assertIn("q Ende", text)

    def test_range_switch_stops_at_limits_and_survives_metric_changes(self):
        report = Report([], "@test", LABELS, "views", False, Path("test.jsonl"), 30)
        report.change_horizon(-1)
        report.change_horizon(-1)
        self.assertEqual(report.horizon, "48h")
        for expected in ("4w", "1y", "10y", "10y"):
            report.change_horizon(1)
            report.cycle_metric()
            self.assertEqual(report.horizon, expected)
        report.change_horizon(-1)
        self.assertEqual(report.horizon, "1y")

    def test_long_forecasts_and_learning_fit_for_video_and_channel_metrics(self):
        videos = [Sample(100000+i*30, 10**9+i*10**6, 10**7+i*50000, 10**5+i*500, 1, 0)
                  for i in range(121)]
        channels = [ProfileSample(100000+i*30, "123", "test", "Test", 10000+i, 2, 50000+i*10, 10, 1)
                    for i in range(121)]
        with TemporaryDirectory() as directory:
            for channel, samples, labels, primary in ((False, videos, LABELS, "views"),
                                                      (True, channels, CHANNEL_LABELS, "followers")):
                path = Path(directory)/f"{channel}.jsonl"
                with Learner(path.with_suffix(".sqlite3"), channel=channel) as learner:
                    report = Report(samples, "@test", labels, primary, channel, path, 30, learner, "10y")
                    for metric in report.available_metrics:
                        report.primary = metric
                        for page in (2, 5):
                            with self.subTest(channel=channel, metric=metric, page=page):
                                console = Console(width=60, height=20, file=StringIO())
                                console.print(report.screen(console, page, "Aktuell", time.monotonic()+30, False))
                                rendered = console.file.getvalue()
                                for year in (1, 2, 3, 5, 7, 10):
                                    self.assertIn(f"{year} J.", rendered)
                                self.assertIn(labels[metric], rendered)
                                self.assertIn("q Ende", rendered)
                                self.assertTrue(rendered.splitlines()[-4].rstrip().endswith("╯"))
                                self.assertEqual(len(rendered.splitlines()), 20)
