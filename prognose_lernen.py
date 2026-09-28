"""Dauerhaftes, kausales Lernen aus abgeschlossenen Prüfprognosen.

Jeder Zielhorizont hat eigene, zeitlich nicht überlappende Prüffälle. Ein
5-Minuten-Erfolg verändert deshalb niemals die Gewichte für 48 Stunden.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from contextlib import nullcontext
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import sqlite3

from prognose_zeiten import CHANNEL_HORIZONS, VIDEO_HORIZONS
from trendanalyse import Point, Trend, expected_gain, fit, project, value_at


EXPERTS = {
    "adaptive": "Dynamik",
    "fast": "Rasch abklingend",
    "slow": "Ausdauernd",
    "linear": "Konstantes Tempo",
    "flat": "Stillstand",
}
PRIORS = {"adaptive": 0.45, "fast": 0.15, "slow": 0.15, "linear": 0.15, "flat": 0.10}
LEGACY_VIDEO_HORIZONS = (300, 900, 3600, 7200, 21600, 43200, 86400, 172800)
LEGACY_CHANNEL_HORIZONS = (3600, 21600, 86400, 172800, 604800, 2592000)


@dataclass(frozen=True)
class LearnedForecast:
    low: int
    middle: int
    high: int
    weights: dict[str, float]
    candidates: dict[str, float]
    scale: float
    correction: float = 0
    count: int = 0
    live_count: int = 0
    mae: float | None = None
    bias: float | None = None
    coverage: float | None = None
    contextual: bool = False


def candidates(model: Trend, seconds: int) -> dict[str, float]:
    hours = seconds / 3600
    current = model.points[-1].value
    floor = current if model.monotone else 0
    gains = {
        "adaptive": expected_gain(model, hours),
        "fast": expected_gain(model, hours, half_life=model.half_life / 2, boost=0),
        "slow": expected_gain(model, hours, half_life=model.half_life * 2),
        "linear": model.recent_rate * hours,
        "flat": 0.0,
    }
    return {name: max(floor, current + gain) for name, gain in gains.items()}


def phase(model: Trend) -> str:
    if "Plateau" in model.state:
        return "plateau"
    if model.rate < 0:
        return "loss"
    if model.measured_decay:
        return "slowing"
    if model.boost:
        return "accelerating"
    return "steady"


class Learner:
    def __init__(self, path: Path, *, channel: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.channel = path, channel
        self.horizons = CHANNEL_HORIZONS if channel else VIDEO_HORIZONS
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("Die Lernhistorie stammt aus einer neueren Programmversion.")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS forecasts (
                metric TEXT NOT NULL, horizon INTEGER NOT NULL,
                origin REAL NOT NULL, target REAL NOT NULL, source TEXT NOT NULL,
                phase TEXT NOT NULL, baseline REAL NOT NULL, scale REAL NOT NULL,
                cadence REAL NOT NULL, monotone INTEGER NOT NULL,
                candidates TEXT NOT NULL, weights TEXT NOT NULL,
                low REAL NOT NULL, prediction REAL NOT NULL, high REAL NOT NULL,
                actual REAL, evaluated_at REAL, skipped TEXT,
                PRIMARY KEY (metric, horizon, origin)
            );
            CREATE INDEX IF NOT EXISTS forecast_due ON forecasts(target)
                WHERE actual IS NULL AND skipped IS NULL;
            CREATE INDEX IF NOT EXISTS forecast_scores ON forecasts(metric, horizon, evaluated_at);
            PRAGMA user_version=1;
        """)
        kind = "channel" if channel else "video"
        existing = self.db.execute("SELECT value FROM metadata WHERE key='kind'").fetchone()
        if existing and existing[0] != kind:
            self.db.close()
            raise ValueError("Lernhistorie passt nicht zum Analysetyp.")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('kind', ?)", (kind,))

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def predict(self, metric: str, model: Trend, seconds: int) -> LearnedForecast:
        origin = model.points[-1].timestamp
        options = candidates(model, seconds)
        current = model.points[-1].value
        scale = max(model.quantum, abs(model.recent_rate) * seconds / 3600, 1.0)
        rows = self.db.execute("""
            SELECT * FROM forecasts WHERE metric=? AND horizon=? AND actual IS NOT NULL
                AND evaluated_at<=? ORDER BY target DESC LIMIT 40
        """, (metric, seconds, origin)).fetchall()
        matching = [row for row in rows if row["phase"] == phase(model)]
        contextual = len(matching) >= 6
        if contextual:
            rows = matching
        weights = dict(PRIORS)
        correction = 0.0
        mae = bias = coverage = None
        predictions = [json.loads(row["candidates"]) for row in rows]
        recency = [2 ** (-index / 10) for index in range(len(rows))]
        total = sum(recency)
        residuals = []
        if rows:
            # Gedeckelte relative Fehler verhindern, dass ein einziger viraler
            # Sprung ein Modell dauerhaft verdrängt. Neuere Fälle zählen mehr.
            losses = {name: sum(weight * min(10, abs(options_[name]-row["actual"]) / row["scale"])
                                for weight, row, options_ in zip(recency, rows, predictions)) / total
                      for name in EXPERTS}
            best = min(losses.values())
            scores = {name: PRIORS[name] * math.exp(-4 * (loss-best)) for name, loss in losses.items()}
            normalizer = sum(scores.values())
            strength = min(1.0, len(rows) / 6)
            weights = {name: (1-strength) * PRIORS[name] + strength * (
                0.90 * scores[name] / normalizer + 0.10 * PRIORS[name]) for name in EXPERTS}
            residuals = [(row["actual"] - sum(weights[name] * options_[name] for name in EXPERTS)) / row["scale"]
                         for row, options_ in zip(rows, predictions)]
            learned_bias = sum(weight * max(-2, min(2, residual))
                               for weight, residual in zip(recency, residuals)) / total
            correction = max(-0.5, min(0.5, learned_bias)) * scale * len(rows) / (len(rows)+6)
            # Diese Kennzahlen bewerten die damals gespeicherte Prognose, nicht
            # nachträglich mit besseren Gewichten rekonstruierte Vorhersagen.
            mae = sum(abs(row["prediction"]-row["actual"]) for row in rows) / len(rows)
            bias = sum(row["prediction"]-row["actual"] for row in rows) / len(rows)
            coverage = sum(row["low"] <= row["actual"] <= row["high"] for row in rows) / len(rows)

        center = sum(weights[name] * options[name] for name in EXPERTS) + correction
        base_low, base_middle, base_high = project(model, seconds/3600)
        lower_width, upper_width = base_middle-base_low, base_high-base_middle
        disagreement = math.sqrt(sum(weights[name]*(options[name]-center)**2 for name in EXPERTS))
        if residuals:
            ordered = sorted(abs(value) for value in residuals)
            error_width = ordered[min(len(ordered)-1, math.ceil(len(ordered)*0.8)-1)] * scale
            strength = len(rows) / (len(rows)+6)
            lower_width = (1-strength)*lower_width + strength*error_width
            upper_width = (1-strength)*upper_width + strength*error_width
        lower_width = max(model.quantum, lower_width, disagreement)
        upper_width = max(model.quantum, upper_width, disagreement)
        floor = current if model.monotone else 0
        low, middle, high = (round(max(floor, value)) for value in (
            center-lower_width, center, center+upper_width))
        return LearnedForecast(low, middle, high, weights, options, scale, correction,
                               len(rows), sum(row["source"] == "live" for row in rows),
                               mae, bias, coverage, contextual)

    def predict_all(self, metric: str, model: Trend) -> dict[int, LearnedForecast]:
        results = {}
        previous = (round(model.points[-1].value),) * 3
        for seconds in self.horizons:
            prediction = self.predict(metric, model, seconds)
            if model.monotone:
                # Unabhängige Horizonte dürfen bei kumulativen Views keine
                # rückwärts laufende Prognosekurve ergeben.
                low, mid, high = (max(old, new) for old, new in zip(
                    previous, (prediction.low, prediction.middle, prediction.high)))
                prediction = replace(prediction, low=low, middle=mid, high=high)
                previous = (low, mid, high)
            results[seconds] = prediction
        return results

    def _settle(self, series: dict[str, tuple[Point, ...]], now: float):
        rows = self.db.execute("""SELECT * FROM forecasts WHERE target<=?
            AND actual IS NULL AND skipped IS NULL ORDER BY target""", (now,)).fetchall()
        for row in rows:
            points = series.get(row["metric"], ())
            reason = None
            left = bisect_left(points, row["origin"], key=lambda point: point.timestamp)
            right = bisect_left(points, row["target"], key=lambda point: point.timestamp)
            if not points or left == len(points) or right == len(points) or points[right].timestamp > now:
                continue
            covered = points[left:right+1]
            gap_limit = max(3600 if self.channel else 300, row["cadence"]*5)
            if abs(points[left].timestamp-row["origin"]) > max(1, row["cadence"]*2):
                reason = "Ausgangsmessung fehlt"
            elif any(b.timestamp-a.timestamp > gap_limit for a, b in zip(covered, covered[1:])):
                reason = "Messlücke im Prüfzeitraum"
            elif row["monotone"] and any(b.value < a.value for a, b in zip(covered, covered[1:])):
                reason = "Aufrufzähler nach unten korrigiert"
            actual = None if reason else value_at(points, row["target"])
            self.db.execute("""UPDATE forecasts SET actual=?, evaluated_at=?, skipped=?
                WHERE metric=? AND horizon=? AND origin=?""",
                (actual, now, reason, row["metric"], row["horizon"], row["origin"]))

    def observe(self, series: dict[str, tuple[Point, ...]], models: dict[str, Trend], *, source="live",
                commit=True, horizons=None):
        if not series or not all(series.values()):
            return
        now = min(points[-1].timestamp for points in series.values())
        with self.db if commit else nullcontext():
            self._settle(series, now)
            for metric, model in models.items():
                if not model.ready:
                    continue
                forecasts = self.predict_all(metric, model)
                for seconds in self.horizons if horizons is None else horizons:
                    last = self.db.execute("SELECT MAX(origin) FROM forecasts WHERE metric=? AND horizon=?",
                                           (metric, seconds)).fetchone()[0]
                    # Pro Horizont nacheinander lernen, statt überlappende,
                    # nahezu identische Prognosen als neue Beweise zu zählen.
                    if last is not None and now < last+seconds:
                        continue
                    prediction = forecasts[seconds]
                    self.db.execute("""INSERT OR IGNORE INTO forecasts
                        (metric,horizon,origin,target,source,phase,baseline,scale,cadence,monotone,
                         candidates,weights,low,prediction,high) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (metric, seconds, now, now+seconds, source, phase(model), model.points[-1].value,
                         prediction.scale, model.cadence, model.monotone,
                         json.dumps(prediction.candidates), json.dumps(prediction.weights),
                         prediction.low, prediction.middle, prediction.high))

    def bootstrap(self, series: dict[str, tuple[Point, ...]]):
        """Neue Horizonte chronologisch nachspielen; vorhandene Fälle erhalten."""
        if not series or not all(series.values()):
            return
        row = self.db.execute("SELECT value FROM metadata WHERE key='bootstrapped_horizons'").fetchone()
        if row:
            done = set(json.loads(row[0]))
        elif self.db.execute("SELECT value FROM metadata WHERE key='bootstrapped'").fetchone():
            done = set(LEGACY_CHANNEL_HORIZONS if self.channel else LEGACY_VIDEO_HORIZONS)
        else:
            done = set()
        missing = set(self.horizons) - done
        if not missing:
            return
        primary = next(iter(series.values()))
        maximum_days = 90
        start = max(primary[0].timestamp, primary[-1].timestamp-maximum_days*86400)
        span = primary[-1].timestamp-start
        spacing = max(3600 if self.channel else 300, span/240)
        # Ziele ohne beobachtbaren Endpunkt liefern noch keine Lernerfahrung.
        eligible = tuple(seconds for seconds in self.horizons if seconds in missing and seconds < span)
        previous = start-spacing
        with self.db:
            for point in primary[:-1] if eligible else ():
                if point.timestamp < start or point.timestamp < previous+spacing:
                    continue
                previous = point.timestamp
                prefixes = {metric: points[:bisect_right(points, point.timestamp, key=lambda item: item.timestamp)]
                            for metric, points in series.items()}
                models = {metric: fit(points, channel=self.channel, monotone=metric == "views")
                          for metric, points in prefixes.items()}
                self.observe(prefixes, models, source="replay", commit=False, horizons=eligible)
            self._settle(series, min(points[-1].timestamp for points in series.values()))
            # Simulation dient als Starttraining. Unbeobachtete Replay-Ziele
            # dürfen keine künftig tatsächlich ausgegebene Live-Prognose ersetzen.
            self.db.execute("DELETE FROM forecasts WHERE source='replay' AND actual IS NULL AND skipped IS NULL")
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('bootstrapped', '1')")
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('bootstrapped_horizons', ?)",
                            (json.dumps(sorted(done | missing)),))

    def outcomes(self, metric: str, limit=6):
        return self.db.execute("""SELECT * FROM forecasts WHERE metric=? AND actual IS NOT NULL
            ORDER BY target DESC, horizon DESC LIMIT ?""", (metric, limit)).fetchall()

    def summary(self, metric: str) -> dict:
        """Gespeicherte Lernfälle unabhängig vom aktuellen Trendfenster zählen.

        Anders als die phasenabhängigen Modellgewichte umfasst diese Übersicht
        alle Fälle der Kennzahl. Fehler beziehen sich auf die Originalprognosen;
        noch offene und übersprungene Fälle tragen keinen Fehler bei.
        """
        rows = self.db.execute("""
            SELECT horizon, COUNT(*) AS total, COUNT(actual) AS count,
                SUM(CASE WHEN actual IS NOT NULL AND source='live' THEN 1 ELSE 0 END) AS live_count,
                AVG(CASE WHEN actual IS NOT NULL THEN ABS(prediction-actual) END) AS mae,
                AVG(CASE WHEN actual IS NOT NULL THEN prediction-actual END) AS bias,
                SUM(CASE WHEN actual IS NULL AND skipped IS NULL THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN skipped IS NOT NULL THEN 1 ELSE 0 END) AS skipped
            FROM forecasts WHERE metric=? GROUP BY horizon ORDER BY horizon
        """, (metric,)).fetchall()
        horizons = {row["horizon"]: {key: row[key] for key in row.keys() if key != "horizon"}
                    for row in rows}
        return {"total": sum(row["total"] for row in rows),
                "evaluated": sum(row["count"] for row in rows),
                "pending": sum(row["pending"] for row in rows),
                "skipped": sum(row["skipped"] for row in rows),
                "horizons": horizons}

    def skipped_count(self, metric: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM forecasts WHERE metric=? AND skipped IS NOT NULL",
                               (metric,)).fetchone()[0]
