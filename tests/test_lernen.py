from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from prognose_lernen import Learner, PRIORS
from trendanalyse import Point, fit


def measured(hours=10, rate=1200, base=10000):
    return tuple(Point(100000+i*300, round(base+rate*i/12)) for i in range(hours*12+1))


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/"video.lernen.sqlite3"

    def test_learns_from_errors_and_keeps_horizons_separate(self):
        data = measured(20)
        model = fit(data)
        with Learner(self.path) as learner:
            cold = learner.predict("views", model, 3600)
            learner.bootstrap({"views": data})
            learner.observe({"views": data}, {"views": model})
            warm = learner.predict("views", model, 3600)
            actual_next = data[-1].value+1200
            self.assertGreater(warm.count, 10)
            self.assertLess(abs(warm.middle-actual_next), abs(cold.middle-actual_next))
            self.assertGreater(warm.weights["linear"], PRIORS["linear"])
            self.assertLess(warm.weights["flat"], PRIORS["flat"])
            distant = learner.predict("views", model, 172800)
            self.assertEqual(distant.count, 0)
            self.assertEqual(distant.weights, PRIORS)

    def test_forecast_is_immutable_and_only_learns_after_target_is_observed(self):
        data = measured(3)
        initial = data[:13]
        with Learner(self.path) as learner:
            learner.observe({"views": initial}, {"views": fit(initial)})
            stored = dict(learner.db.execute("SELECT * FROM forecasts WHERE horizon=3600").fetchone())
            short = data[:20]
            learner.observe({"views": short}, {"views": fit(short)})
            self.assertEqual(learner.predict("views", fit(short), 3600).count, 0)
            later = data[:25]
            learner.observe({"views": later}, {"views": fit(later)})
            outcome = dict(learner.db.execute("SELECT * FROM forecasts WHERE horizon=3600 ORDER BY origin LIMIT 1").fetchone())
            self.assertEqual(outcome["prediction"], stored["prediction"])
            self.assertEqual(outcome["weights"], stored["weights"])
            self.assertEqual(outcome["actual"], later[-1].value)
            self.assertEqual(learner.predict("views", fit(later), 3600).count, 1)
            # Nachträglich früheren Stand berechnen: keine späteren Ergebnisse verwenden.
            self.assertEqual(learner.predict("views", fit(initial), 3600).count, 0)

    def test_history_survives_restart_and_is_not_scored_twice(self):
        data = measured(6)
        with Learner(self.path) as learner:
            learner.bootstrap({"views": data})
            learner.observe({"views": data}, {"views": fit(data)})
            original = learner.predict("views", fit(data), 3600)
            rows = learner.db.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
        with Learner(self.path) as learner:
            learner.bootstrap({"views": data})
            learner.observe({"views": data}, {"views": fit(data)})
            restored = learner.predict("views", fit(data), 3600)
            self.assertEqual(restored, original)
            self.assertEqual(learner.db.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0], rows)

    def test_replay_is_marked_and_does_not_use_future_values(self):
        data = measured(5)
        with Learner(self.path) as learner:
            learner.bootstrap({"views": data})
            case = learner.db.execute("SELECT * FROM forecasts WHERE horizon=3600 ORDER BY origin LIMIT 1").fetchone()
            prefix = tuple(point for point in data if point.timestamp <= case["origin"])
            with Learner(Path(self.directory.name)/"fresh.sqlite3") as fresh:
                expected = fresh.predict("views", fit(prefix), 3600)
            self.assertEqual(case["prediction"], expected.middle)
            self.assertEqual(case["source"], "replay")
            self.assertEqual(learner.predict("views", fit(data), 3600).live_count, 0)
            self.assertEqual(learner.db.execute("SELECT COUNT(*) FROM forecasts WHERE actual IS NULL AND skipped IS NULL").fetchone()[0], 0)
            learner.observe({"views": data}, {"views": fit(data)})
            self.assertEqual(learner.db.execute("SELECT COUNT(*) FROM forecasts WHERE source='live'").fetchone()[0], len(learner.horizons))

    def test_missing_measurements_are_excluded_from_training(self):
        initial = measured(1)
        with Learner(self.path) as learner:
            learner.observe({"views": initial}, {"views": fit(initial)})
            gap = initial+(Point(initial[-1].timestamp+7200, 15000),)
            learner.observe({"views": gap}, {"views": fit(gap)})
            self.assertGreater(learner.skipped_count("views"), 0)
            self.assertEqual(learner.db.execute("SELECT COUNT(*) FROM forecasts WHERE actual IS NOT NULL").fetchone()[0], 0)

    def test_metrics_train_independently_and_signed_counts_can_fall(self):
        series = {"views": measured(8), "likes": measured(8, rate=-6, base=100),
                  "comments": measured(8, rate=0, base=42)}
        with Learner(self.path) as learner:
            learner.bootstrap(series)
            models = {metric: fit(points, monotone=metric == "views") for metric, points in series.items()}
            learner.observe(series, models)
            view = learner.predict("views", models["views"], 3600)
            likes = learner.predict("likes", models["likes"], 3600)
            comments = learner.predict("comments", models["comments"], 3600)
            self.assertGreater(view.middle, series["views"][-1].value)
            self.assertLess(likes.middle, series["likes"][-1].value)
            self.assertEqual(comments.middle, 42)
            self.assertEqual(learner.skipped_count("likes"), 0)
            self.assertNotEqual(view.weights, comments.weights)

    def test_channel_learns_followers_and_profile_likes_for_its_own_horizons(self):
        series = {"followers": measured(30, rate=12, base=6000),
                  "likes": measured(30, rate=48, base=50000)}
        with Learner(self.path, channel=True) as learner:
            learner.bootstrap(series)
            models = {metric: fit(points, channel=True) for metric, points in series.items()}
            learner.observe(series, models)
            forecasts = learner.predict_all("followers", models["followers"])
            self.assertIn(30*86400, forecasts)
            self.assertNotIn(300, forecasts)
            self.assertGreater(forecasts[86400].count, 0)
            self.assertGreater(learner.predict("likes", models["likes"], 86400).count, 0)
            self.assertEqual(forecasts[30*86400].count, 0)


if __name__ == "__main__":
    unittest.main()
