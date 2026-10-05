import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import call, patch

from rich.console import Console

from tiktokanalyse import (ParseError, Sample, fetch_sample, history_path, load_history,
                          main, parse_html, parse_url, save_sample)
from trendanalyse import analyse, project, points_from, window_change


VIDEO_ID = "7689442016555568416"
PHOTO_ID = "7693220884814777632"
PHOTO_URL = f"https://www.tiktok.com/@zeitkante/photo/{PHOTO_ID}"
PHOTO_VIDEO_URL = f"https://www.tiktok.com/@zeitkante/video/{PHOTO_ID}"


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
    def test_photo_url_accepts_aliases_query_and_trailing_slash(self):
        for host in ("www.tiktok.com", "tiktok.com", "m.tiktok.com"):
            with self.subTest(host=host):
                self.assertEqual(
                    parse_url(f"https://{host}/@zeitkante/photo/{PHOTO_ID}/?lang=de"),
                    (PHOTO_URL, "zeitkante", PHOTO_ID),
                )

    def test_photo_fetch_uses_available_html_without_second_request(self):
        with patch("tiktokanalyse.fetch_html", return_value=html_for(PHOTO_ID)) as fetch:
            sample = fetch_sample(PHOTO_URL, PHOTO_ID)
        fetch.assert_called_once_with(PHOTO_URL)
        self.assertEqual((sample.views, sample.likes), (148200, 3706))

    def test_photo_fetch_uses_same_id_video_page_when_details_are_missing(self):
        empty = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{"__DEFAULT_SCOPE__": {}}</script>'
        with patch("tiktokanalyse.fetch_html", side_effect=[empty, html_for(PHOTO_ID)]) as fetch:
            sample = fetch_sample(PHOTO_URL, PHOTO_ID)
        self.assertEqual(fetch.call_args_list, [call(PHOTO_URL), call(PHOTO_VIDEO_URL)])
        self.assertEqual(sample.views, 148200)

    def test_photo_fallback_rejects_statistics_for_another_id(self):
        with patch("tiktokanalyse.fetch_html", side_effect=["<html></html>", html_for()]) as fetch:
            with self.assertRaises(ParseError):
                fetch_sample(PHOTO_URL, PHOTO_ID)
        self.assertEqual(fetch.call_args_list, [call(PHOTO_URL), call(PHOTO_VIDEO_URL)])

    def test_video_parse_errors_and_photo_network_errors_do_not_retry(self):
        video_url = f"https://www.tiktok.com/@zeitkante/video/{VIDEO_ID}"
        with patch("tiktokanalyse.fetch_html", return_value="<html></html>") as fetch:
            with self.assertRaises(ParseError):
                fetch_sample(video_url, VIDEO_ID)
        fetch.assert_called_once_with(video_url)
        with patch("tiktokanalyse.fetch_html", side_effect=RuntimeError("HTTP 429")) as fetch:
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                fetch_sample(PHOTO_URL, PHOTO_ID)
        fetch.assert_called_once_with(PHOTO_URL)

    def test_photo_cli_saves_and_reloads_independent_history(self):
        loaded_counts = []

        def record_load(path, post_id):
            samples = load_history(path, post_id)
            loaded_counts.append(len(samples))
            return samples

        output = StringIO()
        with TemporaryDirectory() as directory:
            args = [PHOTO_URL, "--once", "--data-dir", directory]
            with patch("tiktokanalyse.fetch_html", side_effect=[
                "<html></html>", html_for(PHOTO_ID, "235"), html_for(PHOTO_ID, "240")
            ]), patch("tiktokanalyse.load_history", side_effect=record_load), patch(
                "analyse_tui.Console", return_value=Console(file=output, width=120)
            ):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 0)
            path = history_path(Path(directory), PHOTO_ID)
            self.assertEqual(loaded_counts, [0, 1])
            self.assertEqual([sample.views for sample in load_history(path, PHOTO_ID)], [235, 240])
            self.assertTrue(path.with_suffix(".lernen.sqlite3").is_file())
            self.assertEqual(list(Path(directory).glob("*.jsonl")), [path])
            self.assertIn("Foto", output.getvalue())

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
        samples = [Sample(1000 + i * 30, 1000 + i * 10, 10, 2, 1, 0)
                   for i in range(31)]
        self.assertIsNone(window_change(points_from(samples, "views"), 3600))
        self.assertEqual(window_change(points_from(samples, "views"), 900), 300)
        estimate = analyse(samples, "views")
        self.assertTrue(estimate.ready)
        low, middle, high = project(estimate, 1)
        self.assertLessEqual(low, middle)
        self.assertLessEqual(middle, high)
        with TemporaryDirectory() as directory:
            path = history_path(Path(directory), VIDEO_ID)
            for sample in samples:
                save_sample(path, VIDEO_ID, sample)
            self.assertEqual(load_history(path, VIDEO_ID), samples)

    def test_conservative_forecast_grows_at_every_horizon(self):
        samples = [Sample(1000+i*30, 149000+i*50, 10, 2, 1, 0) for i in range(31)]
        model = analyse(samples, "views")
        current = samples[-1].views
        previous = current
        for hours in (1, 2, 6, 12, 24, 48):
            low, middle, high = project(model, hours)
            self.assertGreater(low, previous)
            self.assertLess(low, middle)
            self.assertLess(middle, high)
            previous = low


if __name__ == "__main__":
    unittest.main()
