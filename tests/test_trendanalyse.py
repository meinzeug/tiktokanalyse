import math
import unittest

from trendanalyse import Point, backtest, fit, project


def series(fn, hours=6, step=30):
    return tuple(Point(100000 + second, round(fn(second/3600)))
                 for second in range(0, int(hours*3600)+1, step))


class TrendTests(unittest.TestCase):
    def test_observed_decay_reduces_future_growth_and_learns_half_life(self):
        slowing = series(lambda h: 100000 + 6000 / math.log(2) * (1-2**(-h)))
        model = fit(slowing)
        self.assertEqual(model.state, "Bremst ab")
        self.assertTrue(model.measured_decay)
        self.assertAlmostEqual(model.half_life, 1, delta=0.2)
        self.assertAlmostEqual(model.rate, 6000/64, delta=8)
        # Bekannte künstliche Sättigung; darf nicht mit dem alten Tempo weiterlaufen.
        self.assertAlmostEqual(project(model, 24)[1], 100000+6000/math.log(2), delta=30)
        self.assertLess(project(model, 24)[1]-slowing[-1].value, 200)

    def test_acceleration_is_detected_but_does_not_explode(self):
        model = fit(series(lambda h: 100000 + 1000*h + 500*h*h))
        self.assertEqual(model.state, "Beschleunigt")
        self.assertGreater(model.boost, 0)
        low, mid, high = project(model, 48)
        self.assertLessEqual(low, mid)
        self.assertLessEqual(mid, high)
        self.assertLess(mid-model.points[-1].value, model.recent_rate*48*2)

    def test_plateau_stops_central_forecast(self):
        model = fit(series(lambda h: 100000+6000*min(h, 4)))
        self.assertIn("Plateau", model.state)
        self.assertEqual(model.rate, 0)
        for hours in (1, 2, 6, 12, 24, 48):
            self.assertEqual(project(model, hours)[1], 124000)

    def test_follower_loss_is_signed_and_cannot_cross_zero(self):
        model = fit(series(lambda h: 10000-50*h), channel=True)
        self.assertLess(model.rate, 0)
        previous = model.points[-1].value
        for hours in (24, 48, 168, 720):
            low, mid, high = project(model, hours)
            self.assertGreaterEqual(low, 0)
            self.assertLessEqual(low, mid)
            self.assertLessEqual(mid, high)
            self.assertLessEqual(mid, previous)
            previous = mid

    def test_rounding_and_irregular_polling_do_not_dominate_rate(self):
        regular = series(lambda h: 100000 + 100*math.floor(h*12), hours=2, step=30)
        irregular = tuple(point for i, point in enumerate(regular) if i % 4 != 1)
        a, b = fit(regular), fit(irregular)
        self.assertAlmostEqual(a.rate, 1200, delta=150)
        self.assertAlmostEqual(a.rate, b.rate, delta=60)

    def test_gap_and_counter_correction_require_fresh_history(self):
        initial = series(lambda h: 100000+6000*h, hours=1)
        gap = initial + tuple(Point(initial[-1].timestamp+7200+i*30, 120000+i) for i in range(4))
        model = fit(gap)
        self.assertTrue(model.gap)
        self.assertFalse(model.ready)
        corrected = initial + (Point(initial[-1].timestamp+30, 100000),)
        model = fit(corrected)
        self.assertEqual(model.corrections, 1)
        self.assertFalse(model.ready)

    def test_temporary_counter_drop_does_not_create_false_burst(self):
        points = list(series(lambda h: 100000+6000*h, hours=1))
        points[-5] = Point(points[-5].timestamp, 100000)
        model = fit(tuple(points))
        self.assertEqual(model.corrections, 1)
        self.assertAlmostEqual(model.rate, 6000, delta=50)

    def test_backtest_uses_earlier_windows_and_reports_actual_error(self):
        data = series(lambda h: 100000+6000/math.log(2)*(1-2**(-h)))
        check = backtest(data)
        self.assertIsNotNone(check)
        self.assertGreaterEqual(check.count, 3)
        self.assertLess(check.mae, check.naive_mae)
        self.assertIsNone(backtest(data[:10]))

    def test_short_history_is_not_a_prediction(self):
        self.assertFalse(fit(series(lambda h: 100000+h*1000, hours=0.05)).ready)
        self.assertFalse(fit((), channel=True).ready)

    def test_channel_rates_visible_before_forecasts_are_ready(self):
        model = fit(series(lambda h: 1000+h*60, hours=0.5), channel=True)
        self.assertFalse(model.ready)
        self.assertGreater(model.rate, 0)
        self.assertTrue(model.rate_series)


if __name__ == "__main__":
    unittest.main()
