"""Live-Analyse öffentlicher TikTok-Video- und Fotobeiträge."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from rich.console import Console

from analyse_tui import monitor
from prognose_zeiten import add_horizon_argument

METRICS = ("views", "likes", "comments", "shares", "favorites")
LABELS = {"views": "Aufrufe", "likes": "Likes", "comments": "Kommentare",
          "shares": "Geteilt", "favorites": "Gespeichert"}
STAT_KEYS = {"views": "playCount", "likes": "diggCount",
             "comments": "commentCount", "shares": "shareCount",
             "favorites": "collectCount"}
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class ParseError(ValueError):
    """Die Antwort enthält keine gültigen Statistikdaten."""


class ScriptExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.target: str | None = None
        self.parts: list[str] = []
        self.scripts: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            script_id = dict(attrs).get("id")
            self.target = script_id if script_id in {
                "__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE"
            } else None
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.target:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.target:
            self.scripts[self.target] = "".join(self.parts)
            self.target = None


@dataclass(frozen=True)
class Sample:
    timestamp: float
    views: int
    likes: int
    comments: int
    shares: int
    favorites: int


def parse_url(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        raise ValueError("Bitte eine HTTPS-Beitragsadresse von tiktok.com angeben.")
    match = re.fullmatch(r"/@([A-Za-z0-9._]+)/(video|photo)/(\d+)", parsed.path.rstrip("/"))
    if not match:
        raise ValueError("Erwartet: https://www.tiktok.com/@name/video/123456789 "
                         "oder https://www.tiktok.com/@name/photo/123456789")
    creator, post_type, video_id = match.groups()
    return f"https://www.tiktok.com/@{creator}/{post_type}/{video_id}", creator, video_id


def _number(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ParseError(f"{key} fehlt oder ist keine Zahl.")
    if isinstance(value, str) and not re.fullmatch(r"\d+", value):
        raise ParseError(f"{key} ist keine ganze Zahl.")
    number = int(value)
    if number < 0:
        raise ParseError(f"{key} ist negativ.")
    return number


def parse_html(html: str, video_id: str, timestamp: float | None = None) -> Sample:
    extractor = ScriptExtractor()
    extractor.feed(html)
    item = None
    if "__UNIVERSAL_DATA_FOR_REHYDRATION__" in extractor.scripts:
        try:
            data = json.loads(extractor.scripts["__UNIVERSAL_DATA_FOR_REHYDRATION__"])
            item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ParseError("Beitragsdaten fehlen im eingebetteten JSON (Sperre oder Layoutänderung?).") from exc
    elif "SIGI_STATE" in extractor.scripts:
        try:
            data = json.loads(extractor.scripts["SIGI_STATE"])
            item = data["ItemModule"][video_id]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ParseError("Beitragsdaten fehlen im eingebetteten JSON (Sperre oder Layoutänderung?).") from exc
    if not isinstance(item, dict) or str(item.get("id")) != video_id:
        raise ParseError("Die HTML-Seite enthält keine Daten für die angefragte Beitrags-ID.")
    stats = item.get("statsV2") or item.get("stats")
    if not isinstance(stats, dict):
        raise ParseError("Statistikdaten fehlen im HTML.")
    values = {}
    for name, key in STAT_KEYS.items():
        # Ältere Seiten liefern nicht immer die Anzahl der gespeicherten Beiträge.
        raw = stats.get(key, 0 if name == "favorites" else None)
        values[name] = _number(raw, key)
    return Sample(timestamp if timestamp is not None else time.time(), **values)


def fetch_sample(url: str, video_id: str) -> Sample:
    html = fetch_html(url)
    try:
        return parse_html(html, video_id)
    except ParseError:
        if "/photo/" not in urlparse(url).path:
            raise
    # Fotoseiten liefern teilweise nur die App-Hülle. Die reguläre Video-Route
    # derselben Beitrags-ID enthält dann auch für Fotos die Statistikdaten.
    # parse_html prüft weiterhin die angefragte ID, bevor Werte übernommen werden.
    return parse_html(fetch_html(url.replace("/photo/", "/video/", 1)), video_id)


def fetch_html(url: str) -> str:
    request = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    try:
        with urlopen(request, timeout=20) as response:
            if "html" not in response.headers.get("Content-Type", "").lower():
                raise ParseError("Server hat keine HTML-Seite geliefert.")
            html = response.read(5_000_001)
            if len(html) > 5_000_000:
                raise ParseError("HTML-Seite ist unerwartet groß.")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: TikTok hat die Anfrage abgewiesen.") from exc
    except URLError as exc:
        raise RuntimeError(f"Netzwerkfehler: {exc.reason}") from exc
    return html.decode("utf-8", errors="replace")


def history_path(data_dir: Path, video_id: str) -> Path:
    return data_dir / f"{video_id}.jsonl"


def load_history(path: Path, video_id: str) -> list[Sample]:
    if not path.exists():
        return []
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if record.get("video_id") != video_id:
                continue
            sample = Sample(float(record["timestamp"]), **{
                name: _number(record[name], name) for name in METRICS
            })
            if math.isfinite(sample.timestamp) and 0 < sample.timestamp <= time.time() + 300:
                samples.append(sample)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return sorted(samples, key=lambda sample: sample.timestamp)


def save_sample(path: Path, video_id: str, sample: Sample) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"video_id": video_id, **asdict(sample)}, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TikTok-Video- und Fotostatistiken live verfolgen")
    parser.add_argument("url", help="HTTPS-Adresse eines öffentlichen TikTok-Video- oder Fotobeitrags")
    parser.add_argument("--interval", type=float, default=30, metavar="SEKUNDEN",
                        help="Abrufintervall in Sekunden (Standard: 30; Minimum: 10)")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "tiktokanalyse",
                        help="Verzeichnis für die Messhistorie")
    parser.add_argument("--once", action="store_true", help="Einmal abrufen und Ergebnis ausgeben")
    parser.add_argument("--page", type=int, choices=(1, 2, 3, 4, 5, 6), default=1,
                        help="Startansicht: 1 Übersicht, 2 Prognose, 3 Verlauf, 4 Details, 5 Lernen, 6 Fehler")
    parser.add_argument("--metric", choices=("views", "likes", "comments"), default="views",
                        help="Ausgewählte Kennzahl (in der TUI mit m wechseln)")
    add_horizon_argument(parser)
    args = parser.parse_args(argv)
    try:
        url, creator, video_id = parse_url(args.url)
        if not math.isfinite(args.interval) or args.interval < 10:
            raise ValueError("Das Intervall muss mindestens 10 Sekunden betragen.")
    except ValueError as exc:
        parser.error(str(exc))
    console = Console()
    path = history_path(args.data_dir, video_id)
    try:
        samples = load_history(path, video_id)
    except OSError as exc:
        console.print(f"[red]Messhistorie nicht lesbar:[/red] {exc}")
        return 1
    post_label = "Fotobeitrag" if "/photo/" in url else "Video"
    return monitor(samples, lambda: fetch_sample(url, video_id),
                   lambda sample: save_sample(path, video_id, sample),
                   title=f"@{creator} • {post_label} {video_id}", labels=LABELS,
                   primary="views", channel=False, path=path, interval=args.interval,
                   once=args.once, page=args.page, metric=args.metric, horizon=args.horizon)


if __name__ == "__main__":
    sys.exit(main())
