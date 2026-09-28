"""Zeitbasierte Trendmodelle für kumulative Zähler und Followerbestände.

Alle Raten sind pro Stunde. Szenarien sind Sensitivitätsrechnungen, keine
statistisch kalibrierten Konfidenzintervalle. Siehe README für Annahmen.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
from statistics import median
from typing import Iterable


@dataclass(frozen=True)
class Point:
    timestamp: float
    value: float


@dataclass(frozen=True)
class Trend:
    points: tuple[Point, ...]
    rate: float = 0.0
    recent_rate: float = 0.0
    previous_rate: float | None = None
    momentum: float | None = None
    window: float = 0.0
    half_life: float = 12.0
    measured_decay: bool = False
    boost: float = 0.0
    error_rate: float = 0.0
    state: str = "Sammelt Daten"
    quality: str = "kurz"
    ready: bool = False
    monotone: bool = True
    cadence: float = 30.0
    quantum: float = 1.0
    unchanged: float = 0.0
    corrections: int = 0
    gap: bool = False
    rate_series: tuple[Point, ...] = ()

    @property
    def span(self) -> float:
        return self.points[-1].timestamp - self.points[0].timestamp if len(self.points) > 1 else 0.0


@dataclass(frozen=True)
class Backtest:
    horizon: float
    count: int
    mae: float
    naive_mae: float


def points_from(samples: Iterable, metric: str) -> tuple[Point, ...]:
    # Derselbe Zeitstempel zählt nur einmal, auch bei importierten Historien.
    values = {sample.timestamp: float(getattr(sample, metric)) for sample in samples
              if math.isfinite(sample.timestamp) and math.isfinite(getattr(sample, metric))}
    return tuple(Point(timestamp, values[timestamp]) for timestamp in sorted(values))


def value_at(points: tuple[Point, ...], timestamp: float) -> float:
    """Lineare Interpolation nur innerhalb der gemessenen Zeitspanne."""
    index = bisect_right(points, timestamp, key=lambda point: point.timestamp)
    if index == 0:
        return points[0].value
    if index == len(points):
        return points[-1].value
    left, right = points[index - 1], points[index]
    weight = (timestamp - left.timestamp) / (right.timestamp - left.timestamp)
    return left.value + weight * (right.value - left.value)


def window_change(points: tuple[Point, ...], seconds: float) -> float | None:
    if len(points) < 2 or points[-1].timestamp - points[0].timestamp < seconds:
        return None
    return points[-1].value - value_at(points, points[-1].timestamp - seconds)


def robust_rate(points: tuple[Point, ...], start: float, end: float) -> float:
    """Median von Steigungen auf gleichem Zeitraster, robust gegen Sprünge."""
    if end <= start:
        return 0.0
    values = [value_at(points, start + (end - start) * i / 12) for i in range(13)]
    slopes = [(values[j] - values[i]) * 3600 / ((end - start) * (j - i) / 12)
              for i in range(13) for j in range(i + 4, 13)]
    return median(slopes)


def analyse(samples: Iterable, metric: str, *, channel: bool = False) -> Trend:
    return fit(points_from(samples, metric), channel=channel,
               monotone=not channel and metric == "views")


def fit(raw: tuple[Point, ...], *, channel: bool = False, monotone: bool | None = None) -> Trend:
    if monotone is None:
        monotone = not channel
    minimum = 3600 if channel else 300
    max_window = 21600 if channel else 1800
    lookback = 7 * 86400 if channel else 6 * 3600
    default_half_life = 168.0 if channel else 12.0
    if not raw:
        return Trend((), half_life=default_half_life, monotone=monotone)
    raw = tuple(point for point in raw if point.timestamp >= raw[-1].timestamp - lookback)
    differences = [right.timestamp - left.timestamp for left, right in zip(raw, raw[1:])]
    cadence = median(differences[-200:]) if differences else 60.0
    gap_limit = max(minimum, cadence * 5)
    gaps = [i for i in range(1, len(raw)) if raw[i].timestamp - raw[i-1].timestamp > gap_limit]
    points = list(raw[gaps[-1]:] if gaps else raw)
    corrections = 0
    if monotone:
        cleaned = []
        for index, point in enumerate(points):
            if cleaned and point.value < cleaned[-1].value:
                corrections += 1
                # Einzelner Cache-Rücksprung, der sofort wieder korrigiert wird.
                if index + 1 < len(points) and points[index+1].value >= cleaned[-1].value:
                    continue
                # Dauerhafte Korrektur: neues Niveau, keine negativen Views und
                # kein künstlicher Wachstumsschub durch das alte Niveau.
                cleaned = []
            cleaned.append(point)
        points = cleaned
    points = tuple(points)
    span = points[-1].timestamp - points[0].timestamp if len(points) > 1 else 0
    changes = [i for i in range(1, len(points)) if points[i].value != points[i-1].value]
    unchanged = points[-1].timestamp - (points[changes[-1]].timestamp if changes else points[0].timestamp)
    quantum = 1.0
    if len(changes) >= 3:
        steps = [abs(round(points[i].value - points[i-1].value)) for i in changes[-30:]]
        quantum = float(max(1, math.gcd(*steps)))
    base = dict(points=points, cadence=cadence, half_life=default_half_life,
                monotone=monotone, quantum=quantum, unchanged=unchanged,
                corrections=corrections, gap=bool(gaps))
    if len(points) < 6 or span < 300:
        return Trend(**base)

    # Vergleich zweier gleich langer Fenster; nicht 5-Minuten- gegen 6-Stunden-
    # Mittel vergleichen. Mehrere Zähleränderungen glätten gerundete Views.
    change_intervals = [points[b].timestamp - points[a].timestamp for a, b in zip(changes, changes[1:])]
    update_window = 3 * median(change_intervals[-20:]) if change_intervals else minimum / 2
    window = min(span, max(minimum / 2, min(max_window, max(span / 4, update_window))))
    end = points[-1].timestamp
    recent = robust_rate(points, end - window, end)
    previous = robust_rate(points, end - 2 * window, end - window) if span >= 2 * window else None
    if monotone:
        recent = max(0.0, recent)
        previous = max(0.0, previous) if previous is not None else None
    resolution = quantum * 3600 / window
    momentum = ((recent - previous) / abs(previous) * 100
                if previous is not None and abs(previous) > resolution / 2 else None)
    state = "Gleichmäßig"
    rate = recent
    decay = False
    boost = 0.0
    half_life = default_half_life
    if abs(recent) < 1e-9:
        state = "Plateau / Zähler unverändert"
    elif previous is not None:
        if previous * recent < 0:
            state = "Richtungswechsel"
        elif abs(recent - previous) >= max(abs(previous) * 0.08, resolution):
            if abs(recent) < abs(previous):
                state = "Bremst ab" if recent > 0 else "Verluste lassen nach"
                # Durchschnittsraten repräsentieren Fenstermitten. Aus ihrem
                # Verhältnis wird die Abklingzeit geschätzt und bis jetzt korrigiert.
                k = -math.log(max(0.05, abs(recent / previous))) / (window / 3600)
                half_life = max(window / 7200, min(default_half_life * 4, math.log(2) / k))
                rate *= math.exp(-math.log(2) * window / 7200 / half_life)
                decay = True
            else:
                state = "Beschleunigt" if recent > 0 else "Verluste nehmen zu"
                boost = math.copysign(min(abs(recent), abs(recent - previous)), recent)
    if recent < 0 and state == "Gleichmäßig":
        state = "Bestand sinkt"

    # Gleich breite Zeitbalken für Geschwindigkeitsgrafik und Streuung.
    count = min(24, max(2, int(span / max(cadence * 3, window / 3))))
    bucket = span / count
    rate_series = tuple(Point(points[0].timestamp + (i + 1) * bucket,
                             (value_at(points, points[0].timestamp + (i+1) * bucket)
                              - value_at(points, points[0].timestamp + i * bucket)) * 3600 / bucket)
                        for i in range(count))
    recent_rates = [point.value for point in rate_series if point.timestamp > end - 2 * window]
    center = median(recent_rates)
    scatter = median(abs(value - center) for value in recent_rates) * 1.4826
    # Mindeststreuung wegen Zählerauflösung und kurzer Beobachtung.
    error_rate = max(resolution / 2, scatter, abs(rate) * min(1.0, math.sqrt(minimum / span) * 0.5))
    quality = "kurz" if span < minimum * 4 or len(changes) < 6 else "mittel"
    if span >= minimum * 12 and len(changes) >= 20 and scatter < max(abs(rate), resolution) * 0.5:
        quality = "gut"
    if corrections or gaps:
        quality = "eingeschränkt"
    base.update(half_life=half_life)
    return Trend(**base, rate=rate, recent_rate=recent, previous_rate=previous,
                 momentum=momentum, window=window, measured_decay=decay,
                 boost=boost, error_rate=error_rate, state=state, quality=quality,
                 ready=span >= minimum, rate_series=rate_series)


def _integral(hours: float, decay: float) -> float:
    return -math.expm1(-decay * hours) / decay


def expected_gain(model: Trend, hours: float, *, rate: float | None = None,
                  half_life: float | None = None, boost: float | None = None) -> float:
    decay = math.log(2) / (half_life if half_life is not None else model.half_life)
    speed = model.rate if rate is None else rate
    acceleration = model.boost if boost is None else boost
    # Ein Schub kann höchstens eine weitere aktuelle Rate hinzufügen und läuft
    # aus. Keine unbegrenzte Extrapolation exponentiellen Wachstums.
    tau = max(model.window / 3600, 1 / 60)
    return speed * _integral(hours, decay) + acceleration * (
        _integral(hours, decay) - _integral(hours, decay + 1 / tau))


def project(model: Trend, hours: float) -> tuple[int, int, int]:
    if hours < 0 or not math.isfinite(hours):
        raise ValueError("Prognosezeit muss endlich und nicht negativ sein.")
    current = model.points[-1].value
    if not model.ready:
        return (round(current),) * 3
    # Wenig Verlauf: weitere Szenarien. Die Grenzen sind ausdrücklich keine
    # Wahrscheinlichkeiten. Auch langsam wachsende Videos dürfen stagnieren.
    relative = min(3.0, model.error_rate / max(abs(model.rate), 1.0))
    if model.monotone:
        slow_rate = model.rate / (1 + relative)
        fast_rate = model.rate + model.error_rate
    else:
        slow_rate, fast_rate = model.rate - model.error_rate, model.rate + model.error_rate
    candidates = [expected_gain(model, hours)]
    for rate in (slow_rate, fast_rate):
        for half_life in (model.half_life / 2, model.half_life * 2):
            candidates.append(expected_gain(model, hours, rate=rate, half_life=half_life,
                                            boost=model.boost / 2 if rate < model.rate else model.boost))
    mid = max(0, round(current + candidates[0]))
    low, high = max(0, round(current + min(candidates))), max(0, round(current + max(candidates)))
    return low, mid, high


def backtest(raw: tuple[Point, ...], *, channel: bool = False,
             monotone: bool | None = None) -> Backtest | None:
    """Vergangene Prognosen nachspielen, ausschließlich mit damaligen Daten."""
    if len(raw) < 12:
        return None
    minimum = 3600 if channel else 300
    span = raw[-1].timestamp - raw[0].timestamp
    horizons = (86400, 21600, 3600) if channel else (3600, 900, 300)
    horizon = next((value for value in horizons if span >= minimum + value * 4), None)
    if horizon is None:
        return None
    errors, naive_errors = [], []
    # Nicht überlappende Testziele, höchstens acht vergangene Zeitabschnitte.
    end = raw[-1].timestamp
    for step in range(8, 0, -1):
        cutoff = end - horizon * step
        index = bisect_right(raw, cutoff, key=lambda point: point.timestamp)
        prefix = raw[:index]
        if len(prefix) < 6:
            continue
        model = fit(prefix, channel=channel, monotone=monotone)
        if not model.ready:
            continue
        target = prefix[-1].timestamp + horizon
        future = tuple(point for point in raw[index:] if point.timestamp <= target)
        future_with_anchor = prefix[-1:] + future
        gaps = [b.timestamp-a.timestamp for a, b in zip(future_with_anchor, future_with_anchor[1:])]
        if (not future or target > raw[-1].timestamp or
                target - future[-1].timestamp > model.cadence * 2 or
                any(gap > max(minimum, model.cadence * 5) for gap in gaps)):
            continue
        if model.monotone and any(b.value < a.value for a, b in zip(future_with_anchor, future_with_anchor[1:])):
            continue
        actual = value_at(raw, target)
        prediction = prefix[-1].value + expected_gain(model, horizon / 3600)
        naive = prefix[-1].value + model.recent_rate * horizon / 3600
        errors.append(abs(prediction - actual))
        naive_errors.append(abs(naive - actual))
    if len(errors) < 3:
        return None
    return Backtest(horizon, len(errors), sum(errors) / len(errors), sum(naive_errors) / len(errors))
