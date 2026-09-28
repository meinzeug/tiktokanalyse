from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from prognose_lernen import Learner, PRIORS, LEGACY_VIDEO_HORIZONS
from prognose_zeiten import DAY, YEAR
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

    def test_summary_preserves_stored_learning_through_gap_and_restart(self):
        series = {"views": measured(1), "likes": measured(1, rate=12, base=100)}
        data = measured(2)
        with Learner(self.path) as learner:
            models = {metric: fit(points, monotone=metric == "views") for metric, points in series.items()}
            learner.observe(series, models, source="replay")
            initial = learner.summary("views")
            self.assertEqual(initial["total"], len(learner.horizons))
            self.assertEqual(initial["pending"], initial["total"])
            self.assertEqual(initial["evaluated"], 0)
            self.assertTrue(all(row["mae"] is None and row["bias"] is None
                                for row in initial["horizons"].values()))
            likes = learner.summary("likes")
            for count in (14, 15):
                points = data[:count]
                learner.observe({"views": points}, {"views": fit(points)})
            completed = learner.summary("views")
            self.assertEqual(completed["evaluated"], 2)
            short = completed["horizons"][300]
            self.assertEqual(short["count"], 2)
            self.assertEqual(short["live_count"], 1)
            errors = [row["prediction"]-row["actual"] for row in learner.db.execute(
                "SELECT prediction,actual FROM forecasts WHERE metric='views' AND actual IS NOT NULL")]
            self.assertAlmostEqual(short["mae"], sum(abs(error) for error in errors)/2)
            self.assertAlmostEqual(short["bias"], sum(errors)/2)

            gap = points+(Point(points[-1].timestamp+7200, 15000),)
            model = fit(gap)
            self.assertFalse(model.ready)
            learner.observe({"views": gap}, {"views": model})
            restored = learner.summary("views")
            self.assertEqual(restored["total"], completed["total"])
            self.assertEqual(restored["evaluated"], 2)
            self.assertEqual(restored["skipped"], 4)
            self.assertEqual(restored["pending"], restored["total"]-6)
            skipped = restored["horizons"][900]
            self.assertEqual((skipped["count"], skipped["pending"], skipped["skipped"]), (0, 0, 1))
            self.assertIsNone(skipped["mae"])
            self.assertIsNone(skipped["bias"])
            self.assertEqual(learner.summary("likes"), likes)

        with Learner(self.path) as learner:
            changes = learner.db.total_changes
            self.assertEqual(learner.summary("views"), restored)
            self.assertEqual(learner.summary("likes"), likes)
            self.assertEqual(learner.summary("comments"), {
                "total": 0, "evaluated": 0, "pending": 0, "skipped": 0, "horizons": {}})
            self.assertEqual(learner.db.total_changes, changes)

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

    def test_four_week_forecast_settles_after_restart_without_training_years(self):
        initial = measured(1)
        data = measured(1+28*24)
        with Learner(self.path) as learner:
            learner.observe({"views": initial}, {"views": fit(initial)})
            original = dict(learner.db.execute("SELECT * FROM forecasts WHERE horizon=?", (28*DAY,)).fetchone())
            learner.observe({"views": data[:-1]}, {"views": fit(data[:-1])})
            self.assertEqual(learner.predict("views", fit(data[:-1]), 28*DAY).count, 0)
        with Learner(self.path) as learner:
            learner.observe({"views": data}, {"views": fit(data)})
            result = dict(learner.db.execute("SELECT * FROM forecasts WHERE horizon=? ORDER BY origin", (28*DAY,)).fetchone())
            for key in ("origin", "target", "prediction", "candidates", "weights", "low", "high"):
                self.assertEqual(result[key], original[key])
            self.assertEqual(result["actual"], data[-1].value)
            self.assertEqual(learner.predict("views", fit(data), 28*DAY).live_count, 1)
            distant = learner.predict("views", fit(data), int(10*YEAR))
            self.assertEqual(distant.count, 0)
            self.assertEqual(distant.weights, PRIORS)
            self.assertIsNotNone(learner.db.execute("SELECT target FROM forecasts WHERE horizon=?", (int(10*YEAR),)).fetchone())

    def test_old_database_gains_new_horizons_without_replacing_forecasts(self):
        initial = measured(1)
        data = measured(30*24)
        with Learner(self.path) as learner:
            learner.horizons = LEGACY_VIDEO_HORIZONS
            learner.observe({"views": initial}, {"views": fit(initial)})
            with learner.db:
                learner.db.execute("INSERT INTO metadata VALUES ('bootstrapped', '1')")
            original = [tuple(row) for row in learner.db.execute(
                "SELECT metric,horizon,origin,prediction,weights FROM forecasts ORDER BY horizon")]
        with Learner(self.path) as learner:
            learner.bootstrap({"views": data})
            existing = [tuple(row) for row in learner.db.execute(
                "SELECT metric,horizon,origin,prediction,weights FROM forecasts WHERE source='live' ORDER BY horizon")]
            self.assertEqual(existing, original)
            self.assertGreater(learner.predict("views", fit(data), 28*DAY).count, 0)
            self.assertEqual(learner.predict("views", fit(data), 28*DAY).live_count, 0)
            count = learner.db.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
            learner.bootstrap({"views": data})
            self.assertEqual(learner.db.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0], count)

    def test_ten_year_scenarios_stay_ordered_finite_and_nonnegative(self):
        for metric, data, monotone in (("views", measured(3), True),
                                       ("likes", measured(3, rate=-6, base=100), False),
                                       ("comments", measured(3, rate=0, base=42), False)):
            with self.subTest(metric=metric), Learner(self.path) as learner:
                forecasts = learner.predict_all(metric, fit(data, monotone=monotone))
                self.assertIn(28*DAY, forecasts)
                self.assertIn(int(10*YEAR), forecasts)
                previous = data[-1].value
                for forecast in forecasts.values():
                    self.assertGreaterEqual(forecast.low, 0)
                    self.assertLessEqual(forecast.low, forecast.middle)
                    self.assertLessEqual(forecast.middle, forecast.high)
                    self.assertIsInstance(forecast.high, int)
                    if monotone:
                        self.assertGreaterEqual(forecast.middle, previous)
                        previous = forecast.middle
                if metric == "likes":
                    self.assertLess(forecasts[int(10*YEAR)].middle, data[-1].value)
                elif metric == "comments":
                    self.assertEqual(forecasts[int(10*YEAR)].middle, 42)


if __name__ == "__main__":
    unittest.main()
