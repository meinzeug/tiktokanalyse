"""Gemeinsame, größenabhängige Terminalansicht und Abrufschleife."""

from __future__ import annotations

from datetime import datetime
import math
import os
from pathlib import Path
import queue
import select
import sys
import termios
import threading
import time
import tty

from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from prognose_lernen import Learner
from prognose_zeiten import HORIZON_RANGES, YEAR, horizon_label
from trendanalyse import analyse, backtest, expected_gain, points_from, project, window_change


def number(value: float) -> str:
    return f"{round(value):,}".replace(",", ".")


def compact_number(value: float) -> str:
    for scale, unit in ((1e12, "Bio."), (1e9, "Mrd."), (1e6, "Mio.")):
        if abs(value) >= scale:
            return f"{value / scale:.1f} {unit}".replace(".", ",", 1)
    return number(value)


def delta(value: float) -> str:
    return ("+" if value >= 0 else "−") + number(abs(value))


def duration(seconds: float) -> str:
    if seconds >= YEAR:
        return f"{seconds / YEAR:.1f} Jahre".replace(".", ",")
    if seconds >= 86400:
        return f"{seconds / 86400:.1f} Tage".replace(".", ",")
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h".replace(".", ",")
    if seconds >= 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds:.0f} s"


def spark(values: list[float]) -> str:
    if len(values) < 2:
        return "·"
    low, high = min(values), max(values)
    if low == high:
        return "▁" * len(values)
    return "".join("▁▂▃▄▅▆▇█"[min(7, round((value-low)/(high-low)*7))] for value in values)


class Report:
    def __init__(self, samples, title: str, labels: dict[str, str], primary: str,
                 channel: bool, path: Path, interval: float, learner: Learner | None = None,
                 horizon: str = "4w"):
        self.samples, self.title, self.labels = samples, title, labels
        self.primary, self.channel, self.path, self.interval = primary, channel, path, interval
        self.available_metrics = ("followers", "likes") if channel else ("views", "likes", "comments")
        self.learner = learner
        if horizon not in HORIZON_RANGES:
            raise ValueError("Unbekannter Prognosezeitraum.")
        self.horizon = horizon
        self.revision = None
        self.refresh()

    def refresh(self):
        revision = (len(self.samples), self.samples[-1].timestamp if self.samples else None)
        if revision == self.revision:
            return
        self.revision = revision
        self.series = {metric: points_from(self.samples, metric) for metric in self.available_metrics}
        self.models = {metric: analyse(self.samples, metric, channel=self.channel) for metric in self.available_metrics}
        self.checks = {metric: backtest(model.points, channel=self.channel, monotone=model.monotone)
                       for metric, model in self.models.items()}
        self.update_predictions()

    @property
    def model(self):
        return self.models[self.primary]

    @property
    def check(self):
        return self.checks[self.primary]

    def cycle_metric(self):
        self.primary = self.available_metrics[(self.available_metrics.index(self.primary)+1) % len(self.available_metrics)]

    def change_horizon(self, direction: int):
        ranges = tuple(HORIZON_RANGES)
        index = max(0, min(len(ranges)-1, ranges.index(self.horizon)+direction))
        self.horizon = ranges[index]

    def update_predictions(self):
        self.predictions = {metric: self.learner.predict_all(metric, model)
                            if self.learner and model.ready else {}
                            for metric, model in self.models.items()}

    def learn(self):
        if self.learner:
            self.learner.observe(self.series, self.models)
            self.update_predictions()

    def header(self, width: int):
        mode = "KANAL" if self.channel else "VIDEO"
        return Panel(Text(f"{self.title} • {self.labels[self.primary]}", style="bold cyan", overflow="ellipsis", no_wrap=True),
                     title=f"TIKTOK • {mode} LIVE", border_style="cyan")

    def metrics(self, detailed=False):
        latest = self.samples[-1] if self.samples else None
        prior = self.samples[-2] if len(self.samples) > 1 else None
        if detailed:
            table = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False)
            for column in ("Kennzahl", "Aktuell", "Δ Abruf", "Δ beobachtet", "Tempo / h"):
                table.add_column(column, justify="left" if column == "Kennzahl" else "right")
            for key, label in self.labels.items():
                points = points_from(self.samples, key)
                change = window_change(points, min(3600, self.model.span)) if self.model.span else None
                rate = change * 3600 / min(3600, self.model.span) if change is not None else None
                table.add_row(label, number(getattr(latest, key)) if latest else "—",
                              delta(getattr(latest, key)-getattr(prior, key)) if prior else "—",
                              delta(getattr(latest, key)-getattr(self.samples[0], key)) if latest else "—",
                              delta(rate) if rate is not None else "—")
            return Panel(table, title="Kennzahlen", border_style="blue")
        table = Table.grid(expand=True, padding=(0, 1))
        for _ in self.labels:
            table.add_column(ratio=1)
        cells = []
        for key, label in self.labels.items():
            text = Text(label + "\n", style="dim", no_wrap=True, overflow="ellipsis")
            text.append(number(getattr(latest, key)) if latest else "—", style="bold white")
            if prior:
                change = getattr(latest, key)-getattr(prior, key)
                text.append(" " + delta(change), style="green" if change >= 0 else "yellow")
            cells.append(text)
        table.add_row(*cells)
        return Panel(table, title="Kennzahlen • Δ seit letztem Abruf", border_style="blue")

    def trend(self, compact=False):
        model = self.model
        style = "yellow" if "Bremst" in model.state or "Plateau" in model.state else "cyan"
        if model.rate < 0:
            style = "red"
        lines = Text()
        lines.append(model.state + "\n", style=f"bold {style}")
        if not model.ready:
            minimum = 3600 if self.channel else 300
            lines.append(f"Noch {duration(max(0, minimum-model.span))} Verlauf / mindestens 6 Messpunkte.\n")
            lines.append(f"Bisher {len(model.points)} Punkte über {duration(model.span)}.\n", style="dim")
            if model.rate_series:
                lines.append(f"Vorläufiges Tempo: {delta(model.rate)}/h • Prognose sammelt Daten.\n", style="cyan")
                lines.append("Tempo  " + spark([point.value for point in model.rate_series]), style="cyan")
        else:
            lines.append(f"Aktuelles Tempo: {delta(model.rate)}/h", style="bold white")
            lines.append(f"  ({delta(model.rate*24)}/Tag)\n" if self.channel else f"  ({delta(model.rate/60)}/min)\n")
            lines.append(f"Fenster {duration(model.window)}: {delta(model.recent_rate)}/h")
            if model.previous_rate is not None:
                lines.append(f" • davor {delta(model.previous_rate)}/h")
            lines.append("\n")
            if model.momentum is not None:
                lines.append(f"Tempoänderung: {model.momentum:+.0f} % zum vorigen Fenster\n", style=style)
            else:
                lines.append("Tempoänderung: noch kein belastbarer Vergleich\n", style="dim")
            origin = "aus Tempoverlauf" if model.measured_decay else "Modellannahme"
            if model.rate:
                lines.append(f"Tempo-Halbierung: {duration(model.half_life*3600)} ({origin})\n")
            else:
                lines.append("Trend bleibt bei unverändertem Zähler zunächst flach.\n")
            if model.measured_decay and not compact:
                remaining = expected_gain(model, model.half_life*20)
                lines.append(f"Dynamikmodell: Rest bei gleicher Abbremsung {delta(remaining)}\n")
            lines.append(f"Zähler seit {duration(model.unchanged)} unverändert • Basis: {model.quality}\n")
            if not compact:
                lines.append("Tempo  " + spark([point.value for point in model.rate_series]) + "\n", style="bright_cyan")
                if not model.monotone:
                    lines.append("Rückgänge fließen als negatives Nettowachstum ein.", style="dim")
                else:
                    lines.append("Plateaus können auch durch Rundung oder Cache entstehen.", style="dim")
        if lines.plain.endswith("\n"):
            lines = lines[:-1]
        return Panel(lines, title="Dynamik", border_style=style)

    def chart(self, width=50, height=6):
        points = self.model.points
        if len(points) < 2:
            return Panel(Text("Warte auf weitere Messpunkte …", style="dim"), title="Tempo im Verlauf")
        rates = self.model.rate_series
        if not rates:
            return Panel(Text("Noch zu wenig Verlauf für geglättete Raten.", style="dim"), title="Tempo im Verlauf")
        values = [point.value for point in rates]
        low, high = min(0, min(values)), max(0, max(values))
        if high == low:
            high = low + 1
        scale = high-low
        plot_width = max(8, width-16)
        sampled = [values[min(len(values)-1, int(i*len(values)/plot_width))] for i in range(plot_width)]
        graph = Text()
        for row in range(height):
            threshold = high - scale * (row + 0.5) / height
            graph.append(f"{number(threshold):>9} │", style="dim")
            for value in sampled:
                filled = (value > 0 and 0 <= threshold <= value) or (value < 0 and value <= threshold <= 0)
                graph.append("█" if filled else " ", style="cyan" if value >= 0 else "red")
            graph.append("\n")
        graph.append(f"          └{'─'*plot_width}\n", style="dim")
        start = datetime.fromtimestamp(points[0].timestamp).strftime("%H:%M")
        end = datetime.fromtimestamp(points[-1].timestamp).strftime("%H:%M")
        graph.append(f"{start} → {end} • gleich breite Zeitabschnitte", style="dim")
        return Panel(graph, title="Tempo / h • zeitlicher Verlauf", border_style="blue")

    def forecast(self, width: int, details=False):
        format_count = compact_number if width < 95 else number
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style="bold magenta")
        table.add_column("In")
        for label in ("Vorsichtig", "Trend", "Optimistisch"):
            table.add_column(label, justify="right", style="bold white" if label == "Trend" else "")
        if width >= 95:
            table.add_column("Δ Trend", justify="right")
        range_title, horizons = HORIZON_RANGES[self.horizon]
        for seconds in horizons:
            label = horizon_label(seconds, hours_only=self.horizon == "48h")
            far = seconds > self.model.span * 3
            label += " *" if far else ""
            row = [label]
            if self.model.ready:
                learned = self.predictions[self.primary].get(seconds)
                low, mid, high = ((learned.low, learned.middle, learned.high) if learned
                                 else project(self.model, seconds/3600))
                row += [format_count(low), format_count(mid), format_count(high)]
                if width >= 95:
                    row.append(delta(mid-self.model.points[-1].value))
            else:
                row += ["—", "—", "—"] + (["—"] if width >= 95 else [])
            table.add_row(*row)
        checked = sum(bool(self.predictions[self.primary].get(seconds) and
                           self.predictions[self.primary][seconds].count) for seconds in horizons)
        note = Text("* Fernes Ziel; keine Wahrscheinlichkeitsintervalle.", style="dim")
        if self.horizon in ("1y", "10y"):
            note = Text("* Extrapolation; langfristig stark annahmenabhängig.", style="yellow")
        note.append(f"\nBasis: {duration(self.model.span)} • geprüft: {checked}/{len(horizons)} Ziele")
        if details:
            note.append("\nGeprüft = mindestens ein abgeschlossener Fall; Details unter 5.")
            note.append("\nNeue Ausspielwellen und Beiträge bleiben unbekannt.")
        return Panel(Group(table, note), title=f"Prognose • {self.labels[self.primary]} • {range_title}",
                     border_style="magenta")

    def learning(self, width, compact=False):
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style="cyan")
        columns = ["Ziel", "Live/Replay" if width >= 75 else "L/R", "Fehler Ø", "Bias"]
        if width >= 75:
            columns.append("Korr.")
        columns.append("Modell")
        for label in columns:
            table.add_column(label, justify="left" if label in ("Ziel", "Modell") else "right")
        predictions = self.predictions[self.primary]
        range_title, horizons = HORIZON_RANGES[self.horizon]
        if self.horizon == "48h" and not compact and not self.channel:
            horizons = (300, 900, *horizons)
        if predictions:
            for seconds in horizons:
                learned = predictions[seconds]
                winner = max(learned.weights, key=learned.weights.get)
                short_names = {"adaptive": "Dynamik", "fast": "Kurz", "slow": "Lang", "linear": "Linear", "flat": "Flach"}
                row = [horizon_label(seconds, hours_only=self.horizon == "48h"),
                       f"{learned.live_count}/{learned.count-learned.live_count}",
                       number(learned.mae) if learned.mae is not None else "—",
                       delta(learned.bias) if learned.bias is not None else "—"]
                if width >= 75:
                    row.append(delta(learned.correction))
                row.append(f"{short_names[winner]} {learned.weights[winner]:.0%}" + (" *" if learned.contextual else ""))
                table.add_row(*row)
        else:
            table.add_row(*(["—", "0/0", "—", "—"] + (["—"] if width >= 75 else []) + ["sammelt Daten"]))
        note = Text("L/R = Live/Replay; 0/0 = noch ungeprüft.", style="dim")
        note.append("\nBias +: zu hoch, −: zu niedrig.")
        if not compact:
            note.append("\nKorr. = nächste Anpassung; * = Lernen aus ähnlicher Phase.")
        return Panel(Group(table, note), title=f"Lernstand • {self.labels[self.primary]} • {range_title}", border_style="green")

    def errors(self, rows=8):
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style="cyan")
        for label in ("Zielzeit", "Horizont", "Prognose", "Ist", "Fehler", "Art"):
            table.add_column(label, justify="left" if label in ("Zielzeit", "Art") else "right")
        cases = self.learner.outcomes(self.primary, rows) if self.learner else []
        for case in cases:
            table.add_row(datetime.fromtimestamp(case["target"]).strftime("%d.%m. %H:%M"),
                          horizon_label(case["horizon"]), number(case["prediction"]), number(case["actual"]),
                          delta(case["prediction"]-case["actual"]), "Live" if case["source"] == "live" else "Replay")
        if not cases:
            table.add_row("Noch keine abgeschlossenen Prüffälle", "", "", "", "", "")
        return Panel(table, title=f"Soll / Ist • {self.labels[self.primary]}", border_style="green")

    def windows(self):
        table = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False, header_style="cyan")
        for label in ("Zeitraum", "Δ " + self.labels[self.primary], "Tempo / h", "Δ Likes"):
            table.add_column(label, justify="left" if label == "Zeitraum" else "right")
        likes = points_from(self.samples, "likes")
        if self.model.points:
            likes = tuple(point for point in likes if point.timestamp >= self.model.points[0].timestamp)
        for seconds in ((3600, 21600, 86400, 604800) if self.channel else (300, 900, 3600, 21600)):
            change = window_change(self.model.points, seconds)
            like_change = window_change(likes, seconds)
            table.add_row(duration(seconds), delta(change) if change is not None else "—",
                          delta(change*3600/seconds) if change is not None else "—",
                          delta(like_change) if like_change is not None else "—")
        return Panel(table, title="Gemessene Zeitfenster", border_style="blue")

    def quality(self, compact=False):
        model = self.model
        text = Text(f"Basis: {model.quality} • {len(model.points)} nutzbare Punkte • {duration(model.span)} Verlauf\n")
        text.append(f"Typischer Abstand: {duration(model.cadence)} • kleinste gemeinsame Zählerstufe: {number(model.quantum)}\n")
        if model.gap:
            text.append("Messpause erkannt; Prognose nutzt nur den anschließenden Verlauf.\n", style="yellow")
        if model.corrections:
            text.append(f"{model.corrections} Zählerrücksprünge: getrennt vom Wachstum behandelt.\n", style="yellow")
        if self.check:
            text.append(f"Rücktest: mittlerer Fehler {number(self.check.mae)} bei {duration(self.check.horizon)}\n")
            text.append(f"{self.check.count} vergangene Prognosen • konstantes Tempo: Fehler {number(self.check.naive_mae)}\n")
        else:
            text.append("Für einen Rücktest fehlen noch abgeschlossene Zeitabschnitte.\n", style="dim")
        if self.samples and not self.channel:
            sample = self.samples[-1]
            if sample.views:
                engagement = sum(getattr(sample, key) for key in ("likes", "comments", "shares", "favorites"))
                text.append(f"Interaktionen / Aufrufe: {engagement/sample.views:.2%} • Likes: {sample.likes/sample.views:.2%}\n")
                if not compact:
                    text.append("Interaktionen können von derselben Person stammen.\n", style="dim")
        if not compact:
            text.append("Eine tatsächliche Ausspielgrenze ist aus diesen Zählern unbekannt.", style="dim")
        if text.plain.endswith("\n"):
            text = text[:-1]
        return Panel(text, title="Datengrundlage & Prüfung", border_style="blue")

    def history(self, rows=10):
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style="cyan")
        table.add_column("Zeit")
        table.add_column(self.labels[self.primary], justify="right")
        table.add_column("Δ", justify="right")
        table.add_column("Abstand", justify="right")
        table.add_column("Likes", justify="right")
        start = max(0, len(self.samples)-rows)
        for index in range(start, len(self.samples)):
            sample = self.samples[index]
            prior = self.samples[index-1] if index else None
            change = getattr(sample, self.primary)-getattr(prior, self.primary) if prior else None
            table.add_row(datetime.fromtimestamp(sample.timestamp).strftime("%d.%m. %H:%M:%S"),
                          number(getattr(sample, self.primary)), delta(change) if change is not None else "—",
                          duration(sample.timestamp-prior.timestamp) if prior else "—", number(sample.likes))
        return Panel(table, title="Letzte Messungen", border_style="blue")

    def footer(self, status, next_poll, busy, page, compact=False):
        remaining = max(0, math.ceil(next_poll-time.monotonic()))
        text = Text(status, style="green" if status == "Aktuell" else "yellow", no_wrap=True, overflow="ellipsis")
        if self.samples:
            age = max(0, time.time()-self.samples[-1].timestamp)
            text.append(f" • Daten {duration(age)} alt")
        text.append(" • Abruf läuft …" if busy else f" • Nächster Abruf {remaining}s")
        text.append("\n")
        for key, label in ((1, "Start"), (2, "Progn."), (3, "Verlauf"), (4, "Details"), (5, "Lernen"), (6, "Fehler")):
            text.append(f"{key} {label} ", style="bold cyan" if key == page else "dim")
        text.append("\nm Kennzahl  -/+ Zeitraum  r Abruf  q Ende", style="dim")
        return text if compact else Panel(text, border_style="dim")

    def screen(self, console, page, status, next_poll, busy):
        self.refresh()
        width, height = console.size
        if width < 60 or height < 20:
            return Group(self.header(width), Text("Für alle Details Terminal auf mindestens 60 × 20 vergrößern."),
                         Text(f"{self.model.state} • Tempo {delta(self.model.rate)}/h"),
                         self.footer(status, next_poll, busy, page))
        root = Layout()
        compact = height < 28
        head = Text(f"{self.title} • {self.labels[self.primary]}", style="bold cyan", no_wrap=True, overflow="ellipsis") if compact else self.header(width)
        root.split_column(Layout(head, size=1 if compact else 3), Layout(self.metrics(), size=4),
                          Layout(name="body"),
                          Layout(self.footer(status, next_poll, busy, page, compact), size=3 if compact else 5))
        body_height = height - (8 if compact else 12)
        if page == 2:
            body = self.forecast(width, details=body_height >= 15)
            if body_height >= 26:
                body = Group(body, self.quality())
        elif page == 3:
            body = self.history(max(1, body_height-4))
        elif page == 4:
            body = (Group(self.quality(compact=True), self.windows())
                    if body_height >= 17 and width >= 75 else self.quality())
        elif page == 5:
            body = self.learning(width, compact=body_height < 16)
        elif page == 6:
            body = self.errors(max(1, body_height-5))
        elif width >= 110:
            grid = Table.grid(expand=True)
            grid.add_column(ratio=1)
            grid.add_column(ratio=1)
            grid.add_row(self.trend(compact=body_height < 11), self.chart(width//2, max(2, min(6, body_height-6))))
            if body_height >= 28:
                grid.add_row(self.forecast(width//2), self.quality())
            body = grid
        else:
            if body_height >= 15:
                body = Group(self.trend(compact=True), self.chart(width, min(6, body_height-12)))
            else:
                body = self.trend(compact=body_height < 11)
        root["body"].update(body)
        return root

    def full(self, width):
        selected = self.primary
        forecasts = []
        try:
            for metric in self.available_metrics:
                self.primary = metric
                forecasts.extend((self.forecast(width, details=True), self.learning(width)))
        finally:
            self.primary = selected
        return Group(self.header(width), self.metrics(detailed=True), self.trend(), self.chart(width),
                     *forecasts, self.windows(), self.quality(), self.errors(), self.history(),
                     Text(f"Historie: {self.path}", style="dim"))


class TerminalKeys:
    def __enter__(self):
        self.fd, self.old = None, None
        if sys.stdin.isatty():
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def read(self):
        if self.fd is not None and select.select([self.fd], [], [], 0)[0]:
            return os.read(self.fd, 32).decode("utf-8", errors="ignore")
        return ""

    def __exit__(self, *args):
        if self.old is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)


def monitor(samples, fetch, save, **options):
    with Learner(options["path"].with_suffix(".lernen.sqlite3"), channel=options["channel"]) as learner:
        return _monitor(samples, fetch, save, learner=learner, **options)


def _monitor(samples, fetch, save, *, title, labels, primary, channel, path,
             interval, once=False, page=1, metric=None, horizon="4w", learner):
    console = Console()
    report = Report(samples, title, labels, metric or primary, channel, path, interval, learner, horizon)
    with console.status("Werte die bisherige Messhistorie chronologisch aus …"):
        learner.bootstrap(report.series)
        report.update_predictions()
    status = "Starte Abruf …"

    def accept(sample):
        if channel and samples and samples[-1].user_id != sample.user_id:
            raise ValueError("Kanal-ID hat sich geändert; bestehende Historie passt nicht mehr.")
        save(sample)
        samples.append(sample)
        report.refresh()
        report.learn()

    if once:
        try:
            accept(fetch())
            status = "Aktuell"
        except (ValueError, RuntimeError, OSError) as exc:
            status = f"Abruf fehlgeschlagen: {exc}"
        console.print(report.full(console.width))
        console.print(Text(status, style="green" if status == "Aktuell" else "yellow"))
        return 0 if status == "Aktuell" else 1

    results = queue.Queue()
    busy = False
    next_poll = time.monotonic()

    def worker():
        try:
            results.put((fetch(), None))
        except Exception as exc:
            results.put((None, exc))

    try:
        with TerminalKeys() as keys, Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                pressed = keys.read().lower()
                if "q" in pressed:
                    break
                for key in pressed:
                    if key in "123456":
                        page = int(key)
                    if key == "m" or key == "\t":
                        report.cycle_metric()
                    if key in "+=-":
                        report.change_horizon(-1 if key == "-" else 1)
                        if page != 5:
                            page = 2
                    if key == "r" and not busy:
                        next_poll = time.monotonic()
                if busy:
                    try:
                        sample, error = results.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        try:
                            if error:
                                raise error
                            accept(sample)
                            status = "Aktuell"
                        except Exception as exc:
                            status = f"Abruf fehlgeschlagen: {exc}"
                        busy = False
                        next_poll = time.monotonic() + interval
                if not busy and time.monotonic() >= next_poll:
                    busy = True
                    threading.Thread(target=worker, daemon=True).start()
                live.update(report.screen(console, page, status, next_poll, busy), refresh=True)
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    console.print("Analyse beendet. Messhistorie:", str(path))
    return 0
