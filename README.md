# TikTok-Analyse

Öffentliche Video- und Kanalzahlen direkt im Linux-Terminal verfolgen. Beide Befehle lesen das eingebettete JSON aus dem HTML der TikTok-Seite, speichern Messpunkte lokal und zeigen Tempo, Trendwechsel und Szenarien.

## Installation und Update

Python 3.10 oder neuer und `pipx` werden benötigt:

```bash
pipx install -e .
```

Bestehende Installation nach einem Update aktualisieren:

```bash
pipx runpip tiktokanalyse install -e .
```

Falls `~/.local/bin` noch nicht im `PATH` liegt: `pipx ensurepath` ausführen und ein neues Terminal öffnen. Bereits laufende Analysen nach einem Softwareupdate neu starten; ihre Historien bleiben erhalten.

## Verwendung

```bash
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416 --interval 60 --page 2
analysierekanal https://www.tiktok.com/@zeitkante
analysierekanal https://www.tiktok.com/@zeitkante --interval 120
```

`--once` ruft einmal ab und druckt alle Details ohne Vollbild. `--page 1`, `2`, `3` oder `4` wählt die Startansicht.

| Taste | Ansicht / Aktion |
| --- | --- |
| `1` | Übersicht: Kennzahlen, geglättetes Tempo, Beschleunigung/Abbremsung, Zeitdiagramm |
| `2` | Prognose: vorsichtiges, mittleres und optimistisches Szenario |
| `3` | Messverlauf mit Änderungen und Zeitabständen |
| `4` | Datengrundlage, Fehler vergangener Prognosen und gemessene Zeitfenster |
| `r` | Sofort erneut abrufen |
| `q` oder `Strg+C` | Beenden |

Die Oberfläche passt sich der Terminalgröße an; empfohlen sind mindestens 80 × 24 Zeichen. Breite, hohe Terminals zeigen mehrere Bereiche gleichzeitig. Die Ansicht bleibt auch während eines Netzwerkabrufs bedienbar.

Videoabrufe erfolgen standardmäßig alle 30 Sekunden, Kanalabrufe alle 60 Sekunden; Minimum jeweils 10 Sekunden. Historien liegen unter `~/.local/share/tiktokanalyse/<video-id>.jsonl` beziehungsweise `kanal_<name>.jsonl`. `XDG_DATA_HOME` und `--data-dir PFAD` werden berücksichtigt. Verschiedene Videos und Kanäle können gleichzeitig in getrennten Konsolen laufen. Dasselbe Ziel nur einmal gleichzeitig überwachen, um doppelte Messpunkte zu vermeiden.

## Was die Prognose erkennt

Die Gesamtzahl der Aufrufe kann weiter steigen, während die **neuen Aufrufe pro Stunde** bereits deutlich sinken. Diesen Unterschied berücksichtigt das Modell:

- Steigungen aus gleichen Zeitfenstern messen aktuelles und vorheriges Tempo. Ein gleichmäßiges Zeitraster und Median-Steigungen verringern den Einfluss unregelmäßiger Abrufe und gerundeter Zähler.
- Bei messbarer Abbremsung wird die Halbierungszeit aus dem Verhältnis beider Raten geschätzt. Aktuelles Tempo und weiterer Zuwachs sinken entsprechend. Eine auslaufende Ausspielwelle kann damit in eine nahezu flache Kurve übergehen.
- Bei Beschleunigung gibt es einen begrenzten zusätzlichen Schub, der wieder abklingt. Wachstum wird nicht unbegrenzt exponentiell fortgeschrieben.
- Ohne erkennbares Abbremsen ist eine Halbierungszeit von 12 Stunden für Videos beziehungsweise 7 Tagen für Kanäle eine ausdrücklich angezeigte **Modellannahme**. Die echten zukünftigen Ausspielentscheidungen sind unbekannt.
- Ein unveränderter Zähler führt zu einer flachen mittleren Prognose. Das kann Stillstand, Rundung oder Caching bedeuten. Es wird kein Wachstum erzwungen, nur damit die Prognosetabelle steigt.
- Followerverluste werden als negatives Nettowachstum verarbeitet. Kumulative Videoaufrufe, die nach unten korrigiert werden, starten dagegen eine neue Modellgrundlage; ein einzelner unmittelbar zurückgenommener Rücksprung wird übersprungen.
- Nach einer längeren Messpause wird frischer Verlauf gesammelt. Die gesamte gespeicherte Historie bleibt bestehen. Für die Modellbildung zählen maximal die jüngsten 6 Stunden beim Video beziehungsweise 7 Tage beim Kanal.

Die erste Videoprognose benötigt 5 Minuten, die Kanalprognose 1 Stunde und jeweils mindestens 6 Messpunkte ohne größere Unterbrechung. Vorläufige Kanalraten sind schon nach 5 Minuten sichtbar. Videos zeigen 1/2/6/12/24/48 Stunden, Kanäle 1/2/7/30 Tage. Mit `*` markierte Ziele liegen mehr als dreimal so weit in der Zukunft wie die beobachtete Zeitspanne.

Die Szenarien variieren Tempo, Abklingzeit und gemessene Streuung. Sie sind **keine kalibrierten Wahrscheinlichkeitsintervalle**. „Basis: kurz/mittel/gut“ beschreibt heuristisch die verfügbare Datenmenge und Schwankung, nicht eine garantierte Treffergenauigkeit. Der bedingte Restzuwachs bei gleicher Abbremsung ist keine harte Reichweitenobergrenze. Neue Ausspielwellen, neue Beiträge, Tagesrhythmen und Änderungen am TikTok-Algorithmus werden nicht vorhergesagt.

## Nachprüfbare Qualität

Sobald genug Verlauf vorhanden ist, spielt das Programm mindestens drei vergangene Prognosen nach. Für jeden Startpunkt werden nur die davor bekannten Daten verwendet. Die Detailansicht zeigt den mittleren absoluten Fehler in Aufrufen/Followern und vergleicht ihn mit einer Fortschreibung bei konstantem Tempo. Die Prüfung verwendet abgeschlossene Horizonte von 5/15/60 Minuten beim Video oder 1/6/24 Stunden beim Kanal; sie belegt keine Genauigkeit über 48 Stunden oder 30 Tage.

Die Modellidee nutzt [gedämpfte Trends](https://otexts.com/fpp3/holt.html); die Prüfung folgt dem Prinzip [zeitlich rollender Rücktests](https://otexts.com/fpp3/tscv.html). Die konkrete Kombination aus robusten Raten, beobachteter Abbremsung und Szenarien ist eine eigene Heuristik für diese Messdaten.

## Entwicklung

```bash
python3 -m unittest discover -s tests -v
```

Tests decken HTML-Parser, getrennte Historien, Abbremsung bis zur Sättigung, Beschleunigung, Stillstand, Followerverluste, Rundung, Messpausen, Zählerkorrekturen, Rücktests und kleine Terminals ab.
