"""Live-Analyse öffentlicher TikTok-Profile."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse

from rich.align import Align
from rich.box import SIMPLE
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tiktokanalyse import (ParseError, ScriptExtractor, _number, fetch_html,
                           format_delta, format_int, sparkline)


METRICS = ("followers", "following", "likes", "videos", "friends")
LABELS = {"followers": "Follower", "following": "Gefolgt", "likes": "Profil-Likes",
          "videos": "Videos", "friends": "Freunde"}
STAT_KEYS = {"followers": "followerCount", "following": "followingCount",
             "likes": "heartCount", "videos": "videoCount", "friends": "friendCount"}
HORIZONS = (1, 2, 7, 30)


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


def period_change(samples: list[ProfileSample], metric: str, seconds: int) -> tuple[int, float] | None:
    if len(samples) < 2:
        return None
    current = samples[-1]
    target = current.timestamp - seconds
    past = next((sample for sample in reversed(samples[:-1]) if sample.timestamp <= target), samples[0])
    elapsed = current.timestamp - past.timestamp
    if elapsed < seconds * 0.8:
        return None
    return getattr(current, metric) - getattr(past, metric), elapsed


def follower_forecast(samples: list[ProfileSample]) -> tuple[float, float] | None:
    if len(samples) < 3 or samples[-1].timestamp - samples[0].timestamp < 3600:
        return None
    rates = []
    for seconds, weight in ((3600, 0.25), (86400, 0.5), (604800, 0.25)):
        change = period_change(samples, "followers", seconds)
        if change:
            rates.append((change[0] * 86400 / change[1], weight))
    if not rates:
        return None
    daily_rate = sum(rate * weight for rate, weight in rates) / sum(weight for _, weight in rates)
    spread = max((abs(rate - daily_rate) for rate, _ in rates), default=0)
    age_days = (samples[-1].timestamp - samples[0].timestamp) / 86400
    uncertainty = max(0.35, spread / max(abs(daily_rate), 1), 0.5 / math.sqrt(max(age_days, 0.04)))
    return daily_rate, uncertainty


def follower_range(current: int, daily_rate: float, uncertainty: float, days: int) -> tuple[int, int, int]:
    effective_days = 7 / math.log(2) * (1 - 2 ** (-days / 7))
    change = daily_rate * effective_days
    width = min(2.5, uncertainty * math.sqrt(days))
    low = max(0, round(current + change - abs(change) * width))
    middle = max(0, round(current + change))
    high = max(0, round(current + change + abs(change) * width))
    return low, middle, high


def dashboard(samples: list[ProfileSample], username: str, next_poll: float, status: str) -> Group:
    latest = samples[-1] if samples else None
    previous = samples[-2] if len(samples) > 1 else None
    title = Text(f"@{username}" + (f"  ·  {latest.nickname}" if latest and latest.nickname != username else ""),
                 style="bold cyan")
    metrics = Table.grid(expand=True, padding=(0, 2))
    for _ in METRICS:
        metrics.add_column(ratio=1)
    cells = []
    for name in METRICS:
        value = getattr(latest, name) if latest else None
        delta = value - getattr(previous, name) if value is not None and previous else None
        cell = Text()
        cell.append(LABELS[name] + "\n", style="dim")
        cell.append(format_int(value) if value is not None else "—", style="bold white")
        if delta is not None:
            cell.append("\n" + format_delta(delta), style="green" if delta >= 0 else "yellow")
        cells.append(cell)
    metrics.add_row(*cells)

    growth = Table(box=SIMPLE, expand=True, header_style="bold cyan")
    growth.add_column("Zeitraum")
    growth.add_column("Follower", justify="right")
    growth.add_column("Profil-Likes", justify="right")
    growth.add_column("Videos", justify="right")
    for label, seconds in (("1 Stunde", 3600), ("24 Stunden", 86400), ("7 Tage", 604800)):
        values = []
        for name in ("followers", "likes", "videos"):
            change = period_change(samples, name, seconds)
            values.append(format_delta(change[0]) if change else "sammelt Daten")
        growth.add_row(label, *values)

    predictions = Table(box=SIMPLE, expand=True, header_style="bold magenta")
    predictions.add_column("In")
    predictions.add_column("Vorsichtig", justify="right")
    predictions.add_column("Trend", justify="right", style="bold")
    predictions.add_column("Optimistisch", justify="right")
    model = follower_forecast(samples)
    if latest and model:
        for days in HORIZONS:
            low, middle, high = follower_range(latest.followers, *model, days)
            predictions.add_row(f"{days} Tag" if days == 1 else f"{days} Tage",
                                format_int(low), format_int(middle), format_int(high))
    else:
        predictions.add_row("—", "—", "Ab 1 Stunde Messverlauf", "—")

    updated = (datetime.fromtimestamp(latest.timestamp, timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S")
               if latest else "noch keine Messung")
    countdown = max(0, math.ceil(next_poll - time.monotonic()))
    footer = Text(f"Letzter Abruf: {updated}  ·  Messpunkte: {len(samples)}  ·  Nächster Abruf: {countdown}s\n")
    footer.append(status, style="green" if status == "Aktuell" else "yellow")
    note = Text("Follower-Prognose aus bisherigem Wachstum mit abnehmender Dynamik; Szenarien, keine Garantie.", style="dim")
    return Group(
        Panel(Align.center(title), title="TIKTOK · KANAL LIVE", border_style="cyan"),
        Panel(metrics, title="Kanalzahlen  ·  Änderung seit letztem Abruf", border_style="blue"),
        Panel(Group(Text(sparkline(samples, "followers"), style="bright_cyan"), growth),
              title="Wachstum", border_style="blue"),
        Panel(Group(predictions, note), title="Follower-Prognose", border_style="magenta"),
        Panel(footer, border_style="dim"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TikTok-Kanalstatistiken live verfolgen")
    parser.add_argument("url", help="HTTPS-Adresse eines öffentlichen TikTok-Profils")
    parser.add_argument("--interval", type=float, default=60, metavar="SEKUNDEN",
                        help="Abrufintervall in Sekunden (Standard: 60; Minimum: 10)")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "tiktokanalyse",
                        help="Verzeichnis für die Messhistorie")
    parser.add_argument("--once", action="store_true", help="Einmal abrufen und Ergebnis ausgeben")
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
    status = "Starte Abruf ..."
    next_poll = time.monotonic()

    def poll() -> None:
        nonlocal status
        try:
            sample = parse_profile_html(fetch_html(url), username)
            if samples and samples[-1].user_id != sample.user_id:
                raise ParseError("Kanal-ID hat sich geändert; bestehende Historie passt nicht mehr.")
            save_profile_sample(path, sample)
            samples.append(sample)
            status = "Aktuell"
        except (ParseError, RuntimeError, OSError) as exc:
            status = f"Abruf fehlgeschlagen: {exc} · erneuter Versuch folgt"

    if args.once:
        poll()
        console.print(dashboard(samples, username, time.monotonic(), status))
        return 0 if status == "Aktuell" else 1
    try:
        with Live(dashboard(samples, username, next_poll, status), console=console,
                  refresh_per_second=2, screen=True) as live:
            while True:
                if time.monotonic() >= next_poll:
                    poll()
                    next_poll = time.monotonic() + args.interval
                live.update(dashboard(samples, username, next_poll, status))
                time.sleep(min(0.5, max(0.05, next_poll - time.monotonic())))
    except KeyboardInterrupt:
        console.print("Analyse beendet. Messhistorie gespeichert:", str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
