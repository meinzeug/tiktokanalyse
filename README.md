# TikTok-Analyse

Verfolge öffentliche Video- und Kanalzahlen direkt im Linux-Terminal. Beide Befehle lesen das eingebettete JSON aus dem HTML der TikTok-Seite und speichern Messpunkte lokal.

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
analysierekanal https://www.tiktok.com/@zeitkante
analysierekanal https://www.tiktok.com/@zeitkante --interval 120
analysierekanal https://www.tiktok.com/@zeitkante --once
```

Mit `Strg+C` beenden. `analysiere` ruft standardmäßig alle 30 Sekunden ab, `analysierekanal` alle 60 Sekunden; das Minimum beträgt jeweils 10 Sekunden. Die Historien liegen unter `~/.local/share/tiktokanalyse/<video-id>.jsonl` beziehungsweise `~/.local/share/tiktokanalyse/kanal_<name>.jsonl`. Mit `--data-dir PFAD` kann der Speicherort geändert werden. Verschiedene Videos und Kanäle können parallel in getrennten Konsolen laufen.

Die Videoansicht zeigt Aufrufe, Likes, Kommentare, Teilen und Speichern sowie Aufrufprognosen für 1, 2, 6, 12, 24 und 48 Stunden. Die erste Prognose erscheint nach mindestens fünf Minuten und drei Messpunkten.

Die Kanalansicht zeigt Follower, Gefolgte, Profil-Likes, Videos und Freunde. Sie berechnet Veränderungen über 1 Stunde, 24 Stunden und 7 Tage sowie Follower-Szenarien für 1, 2, 7 und 30 Tage. Die Follower-Prognose erscheint nach mindestens einer Stunde und drei Messpunkten.

Beide Prognosen schreiben die gemessene Entwicklung mit langsam abnehmender Dynamik fort. Die Bänder zeigen Szenarien, keine statistische Sicherheit. TikToks Rundung, Caching, Sperren oder Änderungen am HTML können Zahlen verzögern beziehungsweise unzugänglich machen. Bei Abruffehlern versucht das Programm beim nächsten Intervall erneut.

Tests: `python3 -m unittest discover -s tests`
