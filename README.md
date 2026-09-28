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
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416 --metric likes --page 2
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416 --metric comments --page 5
analysierekanal https://www.tiktok.com/@zeitkante
analysierekanal https://www.tiktok.com/@zeitkante --interval 120
```

`--once` ruft einmal ab und druckt die Prognosen und Lernstände aller verfügbaren Kennzahlen ohne Vollbild. `--page 1` bis `6` wählt die Startansicht. Videos bieten `--metric views`, `likes` und `comments`, Kanäle `followers` und `likes`. Das Profil-HTML enthält keinen Kommentar-Gesamtzähler für den Kanal.

| Taste | Ansicht / Aktion |
| --- | --- |
| `1` | Übersicht: Kennzahlen, geglättetes Tempo, Beschleunigung/Abbremsung, Zeitdiagramm |
| `2` | Prognose: vorsichtiges, mittleres und optimistisches Szenario |
| `3` | Messverlauf mit Änderungen und Zeitabständen |
| `4` | Datengrundlage, Fehler vergangener Prognosen und gemessene Zeitfenster |
| `5` | Lernstand pro Zielzeitraum: Live-/Replay-Fälle, Fehler, Bias, Korrektur und stärkstes Modell |
| `6` | Tatsächlicher Soll-/Ist-Vergleich abgeschlossener Prüfprognosen |
| `m` oder Tab | Kennzahl wechseln: Aufrufe → Likes → Kommentare bzw. Follower → Profil-Likes |
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

Aufrufe, Likes und Kommentare erhalten jeweils eigene Raten, Trends und dieselben sechs Prognosehorizonte. Likes und Kommentare können durch entfernte Likes oder gelöschte Kommentare sinken; diese Nettorückgänge werden berücksichtigt. Beim Kanal gilt dasselbe für Follower und Profil-Likes.

Die Szenarien variieren Tempo, Abklingzeit, Modellstreuung und später beobachtete Prognosefehler. Sie sind **keine kalibrierten Wahrscheinlichkeitsintervalle**. „Basis: kurz/mittel/gut“ beschreibt heuristisch die verfügbare Datenmenge und Schwankung, nicht eine garantierte Treffergenauigkeit. Der bedingte Restzuwachs des Dynamikmodells bei gleicher Abbremsung ist keine harte Reichweitenobergrenze. Neue Ausspielwellen, neue Beiträge, Tagesrhythmen und Änderungen am TikTok-Algorithmus werden nicht vorhergesagt.

## Automatisches Lernen aus Fehlern

Die Lernschleife läuft lokal und dauerhaft für jedes Video bzw. jeden Kanal:

1. Fünf Ansätze erstellen eine Prognose: beobachtete Dynamik, rasches Abklingen, längeres Wachstum, konstantes Tempo und Stillstand. Ausgangszahl, Zielzeit, einzelne Modellwerte, Gewichte und endgültige Prognose werden gespeichert.
2. Sobald eine tatsächliche Messung den Zielzeitpunkt erreicht, folgt der Soll-/Ist-Vergleich. Bei zwei eng benachbarten Messungen wird der Zielwert interpoliert. Prüffälle mit Messlücken oder korrigierten Aufrufzählern werden ausgeschlossen.
3. Ansätze mit kleineren bisherigen Fehlern erhalten mehr Gewicht. Das Lernen erfolgt getrennt nach **Zielobjekt, Kennzahl und Horizont**. Erfolge über fünf Minuten werden nicht auf 48 Stunden übertragen. Gibt es mindestens sechs passende Fälle, zählen Erfahrungen aus einer ähnlichen Wachstumsphase.
4. Wiederholte Über- oder Unterschätzung führt zusätzlich zu einer begrenzten Korrektur des Mittelwerts. Die gemessenen Fehler verändern auch die Breite der Szenarien. Eine Änderung wird bei der nächsten Prognose wirksam.

Pro Horizont wird jeweils ein zeitlich abgeschlossener Prüffall nach dem anderen aufgezeichnet. Damit zählen viele fast gleiche, überlappende Vorhersagen nicht als unabhängige Erfolge. Zusätzlich zu den angezeigten Horizonten gibt es kurze Lernprüfungen über 5/15 Minuten beim Video und 1/6 Stunden beim Kanal. Die Gewichte verwenden höchstens die jüngsten 40 passenden Fälle, mit stärkerem Einfluss neuerer Erfahrungen und einem kleinen Restgewicht für jedes Modell. So kann ein zeitweise schlechter Ansatz später wieder berücksichtigt werden.

Beim ersten Start der neuen Version wird vorhandene Historie einmal chronologisch nachgespielt, an höchstens rund 240 Startpunkten aus den letzten 14 Tagen (Video) bzw. 90 Tagen (Kanal). Jeder Startpunkt erhält ausschließlich die damals bereits gemessenen Werte. Diese Fälle heißen **Replay** und sind von tatsächlich während des laufenden Programms gespeicherten **Live**-Prognosen getrennt gekennzeichnet. Ohne ausreichende abgeschlossene Fälle bleiben die Ausgangsgewichte aktiv; langfristige Ziele brauchen entsprechend lange Beobachtung.

Die Lernhistorie liegt neben den Messdaten in `<video-id>.lernen.sqlite3` bzw. `kanal_<name>.lernen.sqlite3`. Sie übersteht Neustarts; bereits bewertete Fälle werden nicht erneut gezählt. Messhistorien bleiben im bisherigen JSONL-Format.

In Ansicht `5` bedeutet **Bias +**, dass die damalige Prognose im Mittel zu hoch lag; **Bias −** bedeutet zu niedrig. **Korr.** zeigt die Anpassung nach der neuen Modellgewichtung. Weil diese Gewichtung bereits verändert sein kann und neuere Fehler stärker zählen, muss die Korrektur nicht immer das Gegenzeichen des historischen Bias haben. Fehler und Bias beziehen sich auf die unverändert gespeicherten Vorhersagen, nicht auf nachträglich verbesserte Werte. `*` beim stärksten Modell kennzeichnet Gewichte aus einer ähnlichen Wachstumsphase. Ansicht `6` zeigt dazu einzelne Zielzeiten, Sollwerte, Istwerte und Abweichungen.

Diese Anpassung kann systematische Fehler verringern; sie garantiert keine stetige Verbesserung bei neuen oder unerwarteten Ausspielwellen. Die Idee mehrerer konkurrierender Ansätze orientiert sich an [Prognosekombinationen](https://otexts.com/fpp3/combinations.html); die lokale Gewichtung und Fehlerkorrektur sind eine eigene Heuristik.

## Nachprüfbare Qualität

Zusätzlich zur dauerhaften Lernhistorie prüft Ansicht `4` das Dynamikmodell anhand mindestens dreier vergangener Zeitfenster. Für jeden Startpunkt werden nur die davor bekannten Daten verwendet. Sie zeigt den mittleren absoluten Fehler der ausgewählten Kennzahl und vergleicht ihn mit einer Fortschreibung bei konstantem Tempo. Diese Prüfung verwendet abgeschlossene Horizonte von 5/15/60 Minuten beim Video oder 1/6/24 Stunden beim Kanal; sie belegt keine Genauigkeit über 48 Stunden oder 30 Tage.

Die Modellidee nutzt [gedämpfte Trends](https://otexts.com/fpp3/holt.html); die Prüfung folgt dem Prinzip [zeitlich rollender Rücktests](https://otexts.com/fpp3/tscv.html). Die konkrete Kombination aus robusten Raten, beobachteter Abbremsung und Szenarien ist eine eigene Heuristik für diese Messdaten.

## Entwicklung

```bash
python3 -m unittest discover -s tests -v
```

Tests decken HTML-Parser, getrennte Historien, Abbremsung bis zur Sättigung, Beschleunigung, Stillstand, Nettorückgänge, Rundung, Messpausen, Zählerkorrekturen, Lernen ohne Zukunftsdaten, dauerhafte Soll-/Ist-Auswertung, getrennte Kennzahlen/Horizonte, Rücktests und kleine Terminals ab.
