# TikTok-Analyse

Verfolge die öffentlichen Zähler eines TikTok-Videos direkt im Linux-Terminal. Das Programm liest das eingebettete JSON aus dem HTML der Videoseite, speichert Messpunkte lokal und zeigt eine Live-Ansicht mit Trend und Aufrufprognosen für 1, 2, 6, 12, 24 und 48 Stunden.

## Installation

Benötigt Python 3.10 oder neuer und `pipx`:

```bash
pipx install -e .
```

Falls `~/.local/bin` noch nicht im `PATH` liegt: `pipx ensurepath` ausführen und danach ein neues Terminal öffnen.

## Verwendung

```bash
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416 --interval 60
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416 --once
```

Mit `Strg+C` beenden. Standardmäßig erfolgt alle 30 Sekunden ein Abruf; das Minimum beträgt 10 Sekunden. Die Historie liegt unter `~/.local/share/tiktokanalyse/<video-id>.jsonl`. Mit `--data-dir PFAD` kann der Speicherort geändert werden.

Die erste Prognose erscheint nach mindestens fünf Minuten und drei Messpunkten. Die Schätzung nutzt Aufrufraten über mehrere Zeitfenster und nimmt für die Zukunft eine langsam abnehmende Dynamik an. Das Band zeigt plausible Szenarien, keine statistische Sicherheit. Die Zahlen können durch TikToks Rundung, Caching, Sperren oder Änderungen am HTML verzögert beziehungsweise nicht verfügbar sein. Bei Abruffehlern versucht das Programm beim nächsten Intervall erneut.

Tests: `python3 -m unittest discover -s tests`
