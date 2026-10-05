# TikTok-Analyse

Öffentliche Video-, Foto- und Kanalzahlen direkt im Linux-Terminal verfolgen. Beide Befehle lesen das eingebettete JSON aus dem HTML der TikTok-Seite, speichern Messpunkte lokal und zeigen Tempo, Trendwechsel und Szenarien.

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
analysiere https://www.tiktok.com/@zeitkante/photo/7693220884814777632
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416 --interval 60 --page 2
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416 --metric likes --page 2
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416 --metric comments --page 5
analysiere https://www.tiktok.com/@zeitkante/video/7689442016555568416 --horizon 10y --page 2
analysierekanal https://www.tiktok.com/@zeitkante
analysierekanal https://www.tiktok.com/@zeitkante --interval 120
analysierekanal https://www.tiktok.com/@zeitkante --horizon 1y --page 2
```

`--once` ruft einmal ab und druckt die Prognosen und Lernstände aller verfügbaren Kennzahlen für den ausgewählten Zeitraum ohne Vollbild. `--page 1` bis `6` wählt die Startansicht. Video- und Fotobeiträge bieten `--metric views`, `likes` und `comments`, Kanäle `followers` und `likes`. Das Profil-HTML enthält keinen Kommentar-Gesamtzähler für den Kanal.

`analysiere` unterstützt sowohl `/video/…` als auch `/photo/…`. Enthält die Fotoseite keine auswertbaren Statistikdaten, wird zusätzlich die reguläre `/video/…`-Adresse **derselben Beitrags-ID** abgerufen. Jeder Abruf muss Daten für genau diese ID liefern; fehlende Aufruf-, Like-, Kommentar- oder Teilen-Zähler werden nicht durch Nullwerte ersetzt. Fotos nutzen dieselben Ansichten, Prognosen und die eigene Mess- und Lernhistorie unter `<beitrags-id>.jsonl` und `<beitrags-id>.lernen.sqlite3`. Verschiedene URL-Varianten derselben ID verwenden dieselben Dateien.

| Taste | Ansicht / Aktion |
| --- | --- |
| `1` | Übersicht: Kennzahlen, geglättetes Tempo, Beschleunigung/Abbremsung, Zeitdiagramm |
| `2` | Prognose: vorsichtiges, mittleres und optimistisches Szenario |
| `3` | Messverlauf mit Änderungen und Zeitabständen |
| `4` | Datengrundlage, Fehler vergangener Prognosen und gemessene Zeitfenster |
| `5` | Lernstand pro Zielzeitraum: Live-/Replay-Fälle, Fehler, Bias, Korrektur und stärkstes Modell |
| `6` | Tatsächlicher Soll-/Ist-Vergleich abgeschlossener Prüfprognosen |
| `m` oder Tab | Kennzahl wechseln: Aufrufe → Likes → Kommentare bzw. Follower → Profil-Likes |
| `+` oder `=` | Längeren Zeitraum wählen: 48 Stunden → 4 Wochen → 1 Jahr → 10 Jahre |
| `-` | Kürzeren Zeitraum wählen |
| `r` | Sofort erneut abrufen |
| `q` oder `Strg+C` | Beenden |

Die Oberfläche passt sich der Terminalgröße an; empfohlen sind mindestens 80 × 24 Zeichen. Breite, hohe Terminals zeigen mehrere Bereiche gleichzeitig. Die Ansicht bleibt auch während eines Netzwerkabrufs bedienbar.

Beide Befehle starten mit **4 Wochen** als Prognosezeitraum. `+` und `-` öffnen direkt die Prognose; in Ansicht `5` ändern sie den Zeitraum des Lernstands. Die Auswahl bleibt beim Kennzahlwechsel erhalten. Mit `--horizon` lässt sich der Zeitraum beim Start setzen:

| Option | Angezeigte Ziele ab jetzt |
| --- | --- |
| `--horizon 48h` | 1, 2, 6, 12, 24 und 48 Stunden |
| `--horizon 4w` (Standard) | 1, 2, 7, 14, 21 und 28 Tage |
| `--horizon 1y` | 30, 60, 90, 180, 270 Tage und 1 Jahr |
| `--horizon 10y` | 1, 2, 3, 5, 7 und 10 Jahre |

Ein Modelljahr entspricht 365,25 Tagen. Alle sechs Ziele einer Ansicht passen ab 60 × 20 Zeichen ins Terminal. Große Prognosewerte werden bei schmalen Terminals als Mio., Mrd. oder Bio. abgekürzt; ab 95 Zeichen Breite erscheinen die vollständigen Zahlen. Jahresprognosen sind ausdrücklich **stark von Annahmen abhängige Extrapolationen**. Das Modell kennt weder zukünftige Beiträge noch neue Ausspielwellen. Der Ansatz „Konstantes Tempo“ schreibt die aktuelle Rate über den gesamten Zielzeitraum fort; bei Jahren kann er deshalb sehr große Szenarien und breite Spannen erzeugen.

Videoabrufe erfolgen standardmäßig alle 30 Sekunden, Kanalabrufe alle 60 Sekunden; Minimum jeweils 10 Sekunden. Historien liegen unter `~/.local/share/tiktokanalyse/<video-id>.jsonl` beziehungsweise `kanal_<name>.jsonl`. `XDG_DATA_HOME` und `--data-dir PFAD` werden berücksichtigt. Verschiedene Videos und Kanäle können gleichzeitig in getrennten Konsolen laufen. Dasselbe Ziel nur einmal gleichzeitig überwachen, um doppelte Messpunkte zu vermeiden.

**Nach einem Neustart sind die alten Daten weiterhin vorhanden.** Der Kennzahlenbereich zeigt auf jeder Seite die Zahl gespeicherter Messpunkte und die gesamte Messdauer. Nach einer längeren Unterbrechung beginnt nur das aktuelle Trendfenster neu: Beim Video braucht es wieder mindestens 5 Minuten und 6 frische Messpunkte, beim Kanal 1 Stunde und mindestens 6 Punkte. Die Anzeige nennt das ausdrücklich „Neuer Trend nach Messpause“. Ansicht `3` zeigt die gespeicherten Messungen; Ansicht `4` unterscheidet Gesamthistorie, aktuelles Trendfenster und Lernhistorie.

Die Lernerfahrung bleibt während dieser Wartezeit in Ansicht `5` sichtbar. Dort erscheinen die gespeicherten Fallzahlen und historischen Fehler, während eine neue Modellkorrektur noch aussteht. Sobald der aktuelle Trend wieder bereit ist, zeigt die Tabelle die für das Modell ausgewählten Lernfälle. Die Zeile „Lernhistorie“ zählt stets alle gespeicherten Fälle der gewählten Kennzahl über sämtliche Horizonte, einschließlich noch offener und ausgeschlossener Prüfungen. `0/0` in einer Tabellenzeile bedeutet ausschließlich, dass **für dieses Ziel** noch kein abgeschlossener Fall vorliegt. Mit `-` lassen sich die kürzeren, früher auswertbaren Horizonte anzeigen.

## Was die Prognose erkennt

Die Gesamtzahl der Aufrufe kann weiter steigen, während die **neuen Aufrufe pro Stunde** bereits deutlich sinken. Diesen Unterschied berücksichtigt das Modell:

- Steigungen aus gleichen Zeitfenstern messen aktuelles und vorheriges Tempo. Ein gleichmäßiges Zeitraster und Median-Steigungen verringern den Einfluss unregelmäßiger Abrufe und gerundeter Zähler.
- Bei messbarer Abbremsung wird die Halbierungszeit aus dem Verhältnis beider Raten geschätzt. Aktuelles Tempo und weiterer Zuwachs sinken entsprechend. Eine auslaufende Ausspielwelle kann damit in eine nahezu flache Kurve übergehen.
- Bei Beschleunigung gibt es einen begrenzten zusätzlichen Schub, der wieder abklingt. Wachstum wird nicht unbegrenzt exponentiell fortgeschrieben.
- Ohne erkennbares Abbremsen ist eine Halbierungszeit von 12 Stunden für Videos beziehungsweise 7 Tagen für Kanäle eine ausdrücklich angezeigte **Modellannahme**. Die echten zukünftigen Ausspielentscheidungen sind unbekannt.
- Ein unveränderter Zähler führt zu einer flachen mittleren Prognose. Das kann Stillstand, Rundung oder Caching bedeuten. Es wird kein Wachstum erzwungen, nur damit die Prognosetabelle steigt.
- Followerverluste werden als negatives Nettowachstum verarbeitet. Kumulative Videoaufrufe, die nach unten korrigiert werden, starten dagegen eine neue Modellgrundlage; ein einzelner unmittelbar zurückgenommener Rücksprung wird übersprungen.
- Nach einer längeren Messpause wird frischer Verlauf gesammelt. Die gesamte gespeicherte Historie bleibt bestehen. Für die Modellbildung zählen maximal die jüngsten 6 Stunden beim Video beziehungsweise 7 Tage beim Kanal.

Die erste Videoprognose benötigt 5 Minuten, die Kanalprognose 1 Stunde und jeweils mindestens 6 Messpunkte ohne größere Unterbrechung. Vorläufige Kanalraten sind schon nach 5 Minuten sichtbar. Die Ziele beider Befehle reichen bis 10 Jahre. Mit `*` markierte Ziele liegen mehr als dreimal so weit in der Zukunft wie die für das aktuelle Trendmodell genutzte Zeitspanne.

Aufrufe, Likes und Kommentare erhalten jeweils eigene Raten, Trends und dieselben Prognosehorizonte in allen vier Ansichten. Likes und Kommentare können durch entfernte Likes oder gelöschte Kommentare sinken; diese Nettorückgänge werden berücksichtigt. Beim Kanal gilt dasselbe für Follower und Profil-Likes.

Unter der Prognose stehen die Zeitspanne der aktuellen Modellbasis und die Anzahl **geprüfter Ziele**. Ein Ziel zählt hier ab dem ersten abgeschlossenen Live- oder Replay-Fall. Das ist keine Qualitätsnote: Fallzahlen und tatsächliche Fehler zeigt Ansicht `5`. Insbesondere bedeutet `0/6`, dass für keines der sechs angezeigten Ziele ein abgeschlossener Vergleich vorliegt.

Die Szenarien variieren Tempo, Abklingzeit, Modellstreuung und später beobachtete Prognosefehler. Sie sind **keine kalibrierten Wahrscheinlichkeitsintervalle**. „Basis: kurz/mittel/gut“ beschreibt heuristisch die verfügbare Datenmenge und Schwankung, nicht eine garantierte Treffergenauigkeit. Der bedingte Restzuwachs des Dynamikmodells bei gleicher Abbremsung ist keine harte Reichweitenobergrenze. Neue Ausspielwellen, neue Beiträge, Tagesrhythmen und Änderungen am TikTok-Algorithmus werden nicht vorhergesagt.

## Automatisches Lernen aus Fehlern

Die Lernschleife läuft lokal und dauerhaft für jedes Video bzw. jeden Kanal:

1. Fünf Ansätze erstellen eine Prognose: beobachtete Dynamik, rasches Abklingen, längeres Wachstum, konstantes Tempo und Stillstand. Ausgangszahl, Zielzeit, einzelne Modellwerte, Gewichte und endgültige Prognose werden gespeichert.
2. Sobald eine tatsächliche Messung den Zielzeitpunkt erreicht, folgt der Soll-/Ist-Vergleich. Bei zwei eng benachbarten Messungen wird der Zielwert interpoliert. Prüffälle mit Messlücken oder korrigierten Aufrufzählern werden ausgeschlossen.
3. Ansätze mit kleineren bisherigen Fehlern erhalten mehr Gewicht. Das Lernen erfolgt getrennt nach **Zielobjekt, Kennzahl und Horizont**. Erfolge über fünf Minuten werden nicht auf 48 Stunden, 28 Tage oder Jahre übertragen. Gibt es mindestens sechs passende Fälle, zählen Erfahrungen aus einer ähnlichen Wachstumsphase.
4. Wiederholte Über- oder Unterschätzung führt zusätzlich zu einer begrenzten Korrektur des Mittelwerts. Die gemessenen Fehler verändern auch die Breite der Szenarien. Eine Änderung wird bei der nächsten Prognose wirksam.

Pro Horizont wird jeweils ein zeitlich abgeschlossener Prüffall nach dem anderen aufgezeichnet. Damit zählen viele fast gleiche, überlappende Vorhersagen nicht als unabhängige Erfolge. **Alle Horizonte bis 10 Jahre werden gespeichert, unabhängig von der gerade ausgewählten Ansicht.** Zusätzlich gibt es kurze Lernprüfungen über 5/15 Minuten beim Video. Beim Kanal dienen unter anderem die 1/6-Stunden-Ziele der kurzen Prüfung. Die Gewichte verwenden höchstens die jüngsten 40 passenden Fälle, mit stärkerem Einfluss neuerer Erfahrungen und einem kleinen Restgewicht für jedes Modell. So kann ein zeitweise schlechter Ansatz später wieder berücksichtigt werden.

Beim ersten Start wird vorhandene Historie einmal chronologisch nachgespielt, an höchstens rund 240 Startpunkten aus den letzten 90 Tagen. Bei einem Update werden neu hinzugekommene Horizonte auf dieselbe Weise nachgetragen; bestehende Prognosen und ihre ursprünglichen Gewichte bleiben erhalten. Jeder Startpunkt erhält ausschließlich die damals bereits gemessenen Werte. Diese Fälle heißen **Replay** und sind von tatsächlich während des laufenden Programms gespeicherten **Live**-Prognosen getrennt gekennzeichnet. Ohne ausreichende abgeschlossene Fälle bleiben die Ausgangsgewichte aktiv. Eine Live-Prognose über 28 Tage wird frühestens nach 28 Tagen bewertet, eine 10-Jahres-Prognose erst nach 10 Jahren mit entsprechendem Messverlauf. Ein Neustart zieht diese Zielzeiten nicht vor.

Die Lernhistorie liegt neben den Messdaten in `<video-id>.lernen.sqlite3` bzw. `kanal_<name>.lernen.sqlite3`. Sie übersteht Neustarts; bereits bewertete Fälle werden nicht erneut gezählt. Messhistorien bleiben im bisherigen JSONL-Format.

In Ansicht `5` bedeutet **Bias +**, dass die damalige Prognose im Mittel zu hoch lag; **Bias −** bedeutet zu niedrig. **Korr.** zeigt die Anpassung nach der neuen Modellgewichtung. Weil diese Gewichtung bereits verändert sein kann und neuere Fehler stärker zählen, muss die Korrektur nicht immer das Gegenzeichen des historischen Bias haben. Fehler und Bias beziehen sich auf die unverändert gespeicherten Vorhersagen, nicht auf nachträglich verbesserte Werte. `*` beim stärksten Modell kennzeichnet Gewichte aus einer ähnlichen Wachstumsphase. Ansicht `6` zeigt dazu einzelne Zielzeiten, Sollwerte, Istwerte und Abweichungen.

Diese Anpassung kann systematische Fehler verringern; sie garantiert keine stetige Verbesserung bei neuen oder unerwarteten Ausspielwellen. Die Idee mehrerer konkurrierender Ansätze orientiert sich an [Prognosekombinationen](https://otexts.com/fpp3/combinations.html); die lokale Gewichtung und Fehlerkorrektur sind eine eigene Heuristik.

## Nachprüfbare Qualität

Zusätzlich zur dauerhaften Lernhistorie prüft Ansicht `4` das Dynamikmodell anhand mindestens dreier vergangener Zeitfenster. Für jeden Startpunkt werden nur die davor bekannten Daten verwendet. Sie zeigt den mittleren absoluten Fehler der ausgewählten Kennzahl und vergleicht ihn mit einer Fortschreibung bei konstantem Tempo. Diese Prüfung verwendet abgeschlossene Horizonte von 5/15/60 Minuten beim Video oder 1/6/24 Stunden beim Kanal; sie belegt keine Genauigkeit über Wochen oder Jahre.

Die Modellidee nutzt [gedämpfte Trends](https://otexts.com/fpp3/holt.html); die Prüfung folgt dem Prinzip [zeitlich rollender Rücktests](https://otexts.com/fpp3/tscv.html). Die konkrete Kombination aus robusten Raten, beobachteter Abbremsung und Szenarien ist eine eigene Heuristik für diese Messdaten.

## Entwicklung

```bash
python3 -m unittest discover -s tests -v
```

Tests decken HTML-Parser, getrennte Historien, Abbremsung bis zur Sättigung, Beschleunigung, Stillstand, Nettorückgänge, Rundung, Messpausen, Zählerkorrekturen, Lernen ohne Zukunftsdaten, dauerhafte Soll-/Ist-Auswertung, getrennte Kennzahlen/Horizonte, das Nachrüsten bestehender Lernhistorien, 28-Tage-Prüffälle nach Neustart, Szenarien bis 10 Jahre, Rücktests und kleine Terminals ab.
