"""Live-Analyse öffentlicher TikTok-Videoseiten."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from rich.align import Align
from rich.box import ROUNDED, SIMPLE
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


METRICS = ("views", "likes", "comments", "shares", "favorites")
LABELS = {"views": "Aufrufe", "likes": "Likes", "comments": "Kommentare",
          "shares": "Geteilt", "favorites": "Gespeichert"}
STAT_KEYS = {"views": "playCount", "likes": "diggCount",
             "comments": "commentCount", "shares": "shareCount",
             "favorites": "collectCount"}
HORIZONS = (1, 2, 6, 12, 24, 48)
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class ParseError(ValueError):
    """Die Antwort enthält keine gültigen Zahlen für dieses Video."""


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
        raise ValueError("Bitte eine HTTPS-Videoadresse von tiktok.com angeben.")
    match = re.fullmatch(r"/@([A-Za-z0-9._]+)/video/(\d+)", parsed.path.rstrip("/"))
    if not match:
        raise ValueError("Erwartet: https://www.tiktok.com/@name/video/123456789")
    creator, video_id = match.groups()
    return f"https://www.tiktok.com/@{creator}/video/{video_id}", creator, video_id


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
            raise ParseError("Videodaten fehlen im eingebetteten JSON (Sperre oder Layoutänderung?).") from exc
    elif "SIGI_STATE" in extractor.scripts:
        try:
            data = json.loads(extractor.scripts["SIGI_STATE"])
            item = data["ItemModule"][video_id]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ParseError("Videodaten fehlen im eingebetteten JSON (Sperre oder Layoutänderung?).") from exc
    if not isinstance(item, dict) or str(item.get("id")) != video_id:
        raise ParseError("Die HTML-Seite enthält keine Daten für die angefragte Video-ID.")
    stats = item.get("statsV2") or item.get("stats")
    if not isinstance(stats, dict):
        raise ParseError("Statistikdaten fehlen im HTML.")
    values = {}
    for name, key in STAT_KEYS.items():
        # Ältere Seiten liefern nicht immer die Anzahl der gespeicherten Videos.
        raw = stats.get(key, 0 if name == "favorites" else None)
        values[name] = _number(raw, key)
    return Sample(timestamp if timestamp is not None else time.time(), **values)


def fetch_sample(url: str, video_id: str) -> Sample:
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
    return parse_html(html.decode("utf-8", errors="replace"), video_id)


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


def format_int(value: int | float) -> str:
    return f"{round(value):,}".replace(",", ".")


def format_delta(value: int) -> str:
    return f"+{format_int(value)}" if value >= 0 else f"−{format_int(-value)}"


def _past_sample(samples: list[Sample], seconds: float) -> Sample | None:
    if len(samples) < 2:
        return None
    target = samples[-1].timestamp - seconds
    candidates = [sample for sample in samples[:-1] if sample.timestamp <= target]
    return candidates[-1] if candidates else samples[0]


def hourly_rate(samples: list[Sample], metric: str, seconds: float) -> float | None:
    past = _past_sample(samples, seconds)
    if past is None:
        return None
    elapsed = samples[-1].timestamp - past.timestamp
    if elapsed < max(60, seconds * 0.8):
        return None
    return max(0, getattr(samples[-1], metric) - getattr(past, metric)) * 3600 / elapsed


def forecast(samples: list[Sample]) -> tuple[float, float] | None:
    if len(samples) < 3 or samples[-1].timestamp - samples[0].timestamp < 300:
        return None
    rates = [(hourly_rate(samples, "views", seconds), weight)
             for seconds, weight in ((300, 0.20), (900, 0.35),
                                     (3600, 0.30), (21600, 0.15))]
    available = [(rate, weight) for rate, weight in rates if rate is not None]
    if not available:
        return None
    baseline = sum(rate * weight for rate, weight in available) / sum(weight for _, weight in available)
    # Unterschiede zwischen kurzen und langen Zeitfenstern geben eine einfache
    # Unsicherheitsschätzung; bei wenig Daten ist das Band bewusst breiter.
    spread = statistics.pstdev([rate for rate, _ in available]) if len(available) > 1 else baseline * 0.5
    age_hours = (samples[-1].timestamp - samples[0].timestamp) / 3600
    uncertainty = max(0.25, spread / max(baseline, 1), 0.7 / math.sqrt(max(age_hours, 0.1)))
    return baseline, uncertainty


def forecast_range(current: int, rate: float, uncertainty: float, hours: int) -> tuple[int, int, int]:
    # Fallende Dynamik: Halbwertszeit 12 Stunden. Szenarioband wächst mit Horizont.
    effective_hours = 12 / math.log(2) * (1 - 2 ** (-hours / 12))
    middle = current + rate * effective_hours
    width = min(2.0, uncertainty * math.sqrt(hours))
    low = current + max(0, rate * (1 - width)) * effective_hours
    high = current + rate * (1 + width) * effective_hours
    return round(low), round(middle), round(high)


def sparkline(samples: list[Sample], metric: str = "views", width: int = 36) -> str:
    points = samples[-max(2, width):]
    if len(points) < 2:
        return "·" * width
    values = [getattr(point, metric) for point in points]
    lo, hi = min(values), max(values)
    blocks = "▁▂▃▄▅▆▇█"
    if hi == lo:
        return "▁" * len(values)
    return "".join(blocks[min(7, round((value - lo) / (hi - lo) * 7))] for value in values)


def dashboard(samples: list[Sample], creator: str, video_id: str,
              interval: float, next_poll: float, status: str) -> Group:
    latest = samples[-1] if samples else None
    title = Text(f"@{creator}  ·  Video {video_id}", style="bold cyan")
    metrics = Table.grid(expand=True, padding=(0, 2))
    for _ in range(5):
        metrics.add_column(ratio=1)
    previous = samples[-2] if len(samples) > 1 else None
    cells = []
    for name in METRICS:
        value = getattr(latest, name) if latest else None
        delta = getattr(latest, name) - getattr(previous, name) if latest and previous else None
        cell = Text()
        cell.append(LABELS[name] + "\n", style="dim")
        cell.append(format_int(value) if value is not None else "—", style="bold white")
        if delta is not None:
            cell.append("\n" + format_delta(delta), style="green" if delta >= 0 else "yellow")
        cells.append(cell)
    metrics.add_row(*cells)

    rates = Table.grid(expand=True, padding=(0, 3))
    for _ in range(3):
        rates.add_column(ratio=1)
    labels = []
    for label, seconds in (("15 Min.", 900), ("1 Std.", 3600), ("6 Std.", 21600)):
        rate = hourly_rate(samples, "views", seconds)
        labels.append(f"{label}: {format_int(rate)}/h" if rate is not None else f"{label}: sammelt Daten")
    rates.add_row(*labels)

    predictions = Table(box=SIMPLE, expand=True, header_style="bold cyan")
    predictions.add_column("In", width=8)
    predictions.add_column("Vorsichtig", justify="right")
    predictions.add_column("Trend", justify="right", style="bold")
    predictions.add_column("Optimistisch", justify="right")
    model = forecast(samples)
    if latest and model:
        for hours in HORIZONS:
            low, mid, high = forecast_range(latest.views, *model, hours)
            predictions.add_row(f"{hours} h", format_int(low), format_int(mid), format_int(high))
    else:
        predictions.add_row("—", "—", "Erste Prognose nach 5 Minuten Messdaten", "—")

    updated = (datetime.fromtimestamp(latest.timestamp, timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S")
               if latest else "noch keine Messung")
    countdown = max(0, math.ceil(next_poll - time.monotonic()))
    footer = Text(f"Letzter Abruf: {updated}  ·  Messpunkte: {len(samples)}  ·  Nächster Abruf: {countdown}s\n")
    footer.append(status, style="green" if status == "Aktuell" else "yellow")
    note = Text("Trendfortschreibung mit abnehmender Dynamik; das Band ist ein Szenario, keine statistische Garantie.", style="dim")
    return Group(
        Panel(Align.center(title), title="TIKTOK · LIVE", border_style="cyan"),
        Panel(metrics, title="Kennzahlen  ·  Änderung seit letztem Abruf", border_style="blue"),
        Panel(Group(Text(sparkline(samples), style="bright_cyan"), rates), title="Aufruftrend", border_style="blue"),
        Panel(Group(predictions, note), title="Aufrufprognose", border_style="magenta"),
        Panel(footer, border_style="dim"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TikTok-Videostatistiken live verfolgen")
    parser.add_argument("url", help="HTTPS-Adresse eines öffentlichen TikTok-Videos")
    parser.add_argument("--interval", type=float, default=30, metavar="SEKUNDEN",
                        help="Abrufintervall in Sekunden (Standard: 30; Minimum: 10)")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "tiktokanalyse",
                        help="Verzeichnis für die Messhistorie")
    parser.add_argument("--once", action="store_true", help="Einmal abrufen und Ergebnis ausgeben")
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
    status = "Starte Abruf ..."
    next_poll = time.monotonic()

    def poll() -> None:
        nonlocal status
        try:
            sample = fetch_sample(url, video_id)
            save_sample(path, video_id, sample)
            samples.append(sample)
            status = "Aktuell"
        except (ParseError, RuntimeError, OSError) as exc:
            status = f"Abruf fehlgeschlagen: {exc} · erneuter Versuch folgt"

    if args.once:
        poll()
        console.print(dashboard(samples, creator, video_id, args.interval, time.monotonic(), status))
        return 0 if status == "Aktuell" else 1

    try:
        with Live(dashboard(samples, creator, video_id, args.interval, next_poll, status),
                  console=console, refresh_per_second=2, screen=True) as live:
            while True:
                if time.monotonic() >= next_poll:
                    poll()
                    next_poll = time.monotonic() + args.interval
                live.update(dashboard(samples, creator, video_id, args.interval, next_poll, status))
                time.sleep(min(0.5, max(0.05, next_poll - time.monotonic())))
    except KeyboardInterrupt:
        console.print("Analyse beendet. Messhistorie gespeichert:", str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
