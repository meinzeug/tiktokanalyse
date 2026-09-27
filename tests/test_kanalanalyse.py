import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kanalanalyse import (ProfileSample, follower_forecast, follower_range,
                          load_profile_history, parse_profile_html,
                          parse_profile_url, period_change, profile_history_path,
                          save_profile_sample)
from tiktokanalyse import ParseError


def profile_html(username="zeitkante", followers="5694"):
    payload = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {
        "user": {"id": "7664186897100096515", "uniqueId": username,
                 "nickname": "Zeitkante"},
        "statsV2": {"followerCount": followers, "followingCount": "316",
                    "heartCount": "47020", "videoCount": "135", "friendCount": "86"}
    }}}}
    return ("<script id='__UNIVERSAL_DATA_FOR_REHYDRATION__'>" +
            json.dumps(payload) + "</script>")


class ChannelTests(unittest.TestCase):
    def test_url_and_exact_profile_stats(self):
        url, username = parse_profile_url("https://www.tiktok.com/@zeitkante?lang=de")
        self.assertEqual((url, username), ("https://www.tiktok.com/@zeitkante", "zeitkante"))
        sample = parse_profile_html(profile_html(), username, timestamp=1000)
        self.assertEqual((sample.followers, sample.likes, sample.videos), (5694, 47020, 135))
        with self.assertRaises(ParseError):
            parse_profile_html(profile_html(username="anderer"), username)
        with self.assertRaises(ParseError):
            parse_profile_html(profile_html(followers="5.7K"), username)

    def test_profile_history_growth_and_forecast(self):
        samples = [ProfileSample(100000 + i * 1800, "123", "zeitkante", "Zeitkante",
                                 1000 + i * 10, 200, 500 + i * 20, 10, 2)
                   for i in range(4)]
        self.assertEqual(period_change(samples, "followers", 3600)[0], 20)
        self.assertIsNone(period_change(samples, "followers", 86400))
        model = follower_forecast(samples)
        self.assertIsNotNone(model)
        low, middle, high = follower_range(samples[-1].followers, *model, 2)
        self.assertLessEqual(low, middle)
        self.assertLessEqual(middle, high)
        with TemporaryDirectory() as directory:
            path = profile_history_path(Path(directory), "zeitkante")
            self.assertNotEqual(path.name, "zeitkante.jsonl")
            for sample in samples:
                save_profile_sample(path, sample)
            self.assertEqual(load_profile_history(path, "zeitkante"), samples)


if __name__ == "__main__":
    unittest.main()
