"""Live-Analyse öffentlicher TikTok-Profile."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse

from rich.console import Console

from analyse_tui import monitor
from tiktokanalyse import ParseError, ScriptExtractor, _number, fetch_html

METRICS = ("followers", "following", "likes", "videos", "friends")
LABELS = {"followers": "Follower", "following": "Gefolgt", "likes": "Profil-Likes",
          "videos": "Videos", "friends": "Freunde"}
STAT_KEYS = {"followers": "followerCount", "following": "followingCount",
             "likes": "heartCount", "videos": "videoCount", "friends": "friendCount"}


@dataclass(frozen=True)
class ProfileSample:
    timestamp: float
    user_id: str
    unique_id: str
    nickname: str
    followers: int
    following: int
    likes: int
    videos: int
    friends: int


def parse_profile_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        raise ValueError("Bitte eine HTTPS-Profiladresse von tiktok.com angeben.")
    match = re.fullmatch(r"/@([A-Za-z0-9._]+)", parsed.path.rstrip("/"))
    if not match:
        raise ValueError("Erwartet: https://www.tiktok.com/@name")
    username = match.group(1)
    return f"https://www.tiktok.com/@{username}", username


def parse_profile_html(html: str, username: str, timestamp: float | None = None) -> ProfileSample:
    extractor = ScriptExtractor()
    extractor.feed(html)
    if "__UNIVERSAL_DATA_FOR_REHYDRATION__" in extractor.scripts:
        try:
            data = json.loads(extractor.scripts["__UNIVERSAL_DATA_FOR_REHYDRATION__"])
            info = data["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]
            user = info["user"]
            stats = info.get("statsV2") or info["stats"]
            fallback = info.get("stats", {})
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ParseError("Profildaten fehlen im eingebetteten JSON (Sperre oder Layoutänderung?).") from exc
    elif "SIGI_STATE" in extractor.scripts:
        try:
            data = json.loads(extractor.scripts["SIGI_STATE"])
            module = data["UserModule"]
            user = module["users"][username]
            stats = module["stats"][username]
            fallback = stats
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ParseError("Profildaten fehlen im eingebetteten JSON (Sperre oder Layoutänderung?).") from exc
    else:
        raise ParseError("Kein eingebettetes Profil-JSON im HTML gefunden.")
    if not isinstance(user, dict) or not isinstance(stats, dict):
        raise ParseError("Profildaten sind unvollständig.")
    unique_id = user.get("uniqueId")
    if not isinstance(unique_id, str) or unique_id.casefold() != username.casefold():
        raise ParseError("Die HTML-Seite enthält Daten für einen anderen Kanal.")
    user_id = str(user.get("id", ""))
    if not user_id.isdigit():
        raise ParseError("Kanal-ID fehlt im HTML.")
    values = {}
    for name, key in STAT_KEYS.items():
        raw = stats.get(key, fallback.get(key) if isinstance(fallback, dict) else None)
        values[name] = _number(raw, key)
    nickname = user.get("nickname")
    return ProfileSample(timestamp if timestamp is not None else time.time(),
                         user_id, unique_id, nickname if isinstance(nickname, str) else "", **values)


def profile_history_path(data_dir: Path, username: str) -> Path:
    return data_dir / f"kanal_{username.casefold()}.jsonl"


def load_profile_history(path: Path, username: str) -> list[ProfileSample]:
    if not path.exists():
        return []
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if str(record.get("unique_id", "")).casefold() != username.casefold():
                continue
            sample = ProfileSample(
                timestamp=float(record["timestamp"]),
                user_id=str(record["user_id"]),
                unique_id=str(record["unique_id"]),
                nickname=str(record["nickname"]),
                **{name: _number(record[name], name) for name in METRICS},
            )
            if math.isfinite(sample.timestamp) and 0 < sample.timestamp <= time.time() + 300:
                samples.append(sample)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return sorted(samples, key=lambda sample: sample.timestamp)


def save_profile_sample(path: Path, sample: ProfileSample) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(asdict(sample), ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TikTok-Kanalstatistiken live verfolgen")
    parser.add_argument("url", help="HTTPS-Adresse eines öffentlichen TikTok-Profils")
    parser.add_argument("--interval", type=float, default=60, metavar="SEKUNDEN",
                        help="Abrufintervall in Sekunden (Standard: 60; Minimum: 10)")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "tiktokanalyse",
                        help="Verzeichnis für die Messhistorie")
    parser.add_argument("--once", action="store_true", help="Einmal abrufen und Ergebnis ausgeben")
    parser.add_argument("--page", type=int, choices=(1, 2, 3, 4), default=1,
                        help="Startansicht: 1 Übersicht, 2 Prognose, 3 Verlauf, 4 Details")
    args = parser.parse_args(argv)
    try:
        url, username = parse_profile_url(args.url)
        if not math.isfinite(args.interval) or args.interval < 10:
            raise ValueError("Das Intervall muss mindestens 10 Sekunden betragen.")
    except ValueError as exc:
        parser.error(str(exc))
    console = Console()
    path = profile_history_path(args.data_dir, username)
    try:
        samples = load_profile_history(path, username)
    except OSError as exc:
        console.print(f"[red]Messhistorie nicht lesbar:[/red] {exc}")
        return 1
    return monitor(samples, lambda: parse_profile_html(fetch_html(url), username),
                   lambda sample: save_profile_sample(path, sample),
                   title=f"@{username} • Kanal", labels=LABELS, primary="followers",
                   channel=True, path=path, interval=args.interval,
                   once=args.once, page=args.page)


if __name__ == "__main__":
    sys.exit(main())
