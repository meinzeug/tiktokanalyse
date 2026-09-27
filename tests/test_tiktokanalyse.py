import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tiktokanalyse import (ParseError, Sample, forecast, forecast_range,
                          hourly_rate, history_path, load_history, parse_html,
                          parse_url, save_sample)


VIDEO_ID = "7689442016555568416"


def html_for(video_id=VIDEO_ID, views="148200"):
    payload = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {
        "itemInfo": {"itemStruct": {"id": video_id, "statsV2": {
            "playCount": views, "diggCount": "3706", "commentCount": "574",
            "shareCount": "850", "collectCount": "663"
        }}}
    }}}
    return ("<html><script id='__UNIVERSAL_DATA_FOR_REHYDRATION__' "
            "type='application/json'>" + json.dumps(payload) + "</script></html>")


class AnalysisTests(unittest.TestCase):
    def test_url_and_parser_match_exact_video(self):
        url, creator, video_id = parse_url(
            "https://www.tiktok.com/@zeitkante/video/7689442016555568416?lang=de")
        self.assertEqual((creator, video_id), ("zeitkante", VIDEO_ID))
        self.assertEqual(url, f"https://www.tiktok.com/@zeitkante/video/{VIDEO_ID}")
        sample = parse_html(html_for(), VIDEO_ID, timestamp=1000)
        self.assertEqual((sample.views, sample.likes, sample.favorites), (148200, 3706, 663))

    def test_rejects_other_video_and_missing_stats(self):
        with self.assertRaises(ParseError):
            parse_html(html_for(video_id="123"), VIDEO_ID)
        with self.assertRaises(ParseError):
            parse_html(html_for(views="1.2M"), VIDEO_ID)

    def test_history_and_forecast(self):
        samples = [Sample(1000 + i * 300, 1000 + i * 100, 10, 2, 1, 0)
                   for i in range(4)]
        self.assertIsNone(hourly_rate(samples, "views", 3600))
        self.assertEqual(hourly_rate(samples, "views", 900), 1200)
        estimate = forecast(samples)
        self.assertIsNotNone(estimate)
        low, middle, high = forecast_range(samples[-1].views, *estimate, 1)
        self.assertLessEqual(low, middle)
        self.assertLessEqual(middle, high)
        with TemporaryDirectory() as directory:
            path = history_path(Path(directory), VIDEO_ID)
            for sample in samples:
                save_sample(path, VIDEO_ID, sample)
            self.assertEqual(load_history(path, VIDEO_ID), samples)

    def test_conservative_forecast_grows_at_every_horizon(self):
        current = 150500
        previous = current
        for hours in (1, 2, 6, 12, 24, 48):
            low, middle, high = forecast_range(current, 5222, 1.21, hours)
            self.assertGreater(low, previous)
            self.assertLess(low, middle)
            self.assertLess(middle, high)
            previous = low
        self.assertEqual(forecast_range(current, 0, 1.21, 48), (current,) * 3)


if __name__ == "__main__":
    unittest.main()
