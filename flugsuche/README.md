# Flugsuche-Agent ✈

Findet die günstigste Flugverbindung mit möglichst kurzer Reisezeit: mehrere Start- und
Zielflughäfen, je ein Zeitraum für Hin- und Rückflug, Economy, Premium Economy, Business oder First.

| Teil | Was es ist |
|---|---|
| `flugsuche.py` | Suche über die Kommandozeile, ohne KI und ohne Kosten |
| `agent.py` | KI-Agent (Claude): Reisewunsch in normaler Sprache eingeben, er sucht und erklärt das Ergebnis |
| `.claude/skills/flugsuche/` | dasselbe direkt in Claude Code, auch in der Claude-App, ohne eigenen API-Schlüssel |

## So sucht er

1. **Alle Termine:** Er bildet jede Kombination aus Hin- und Rückflugtag. Hinflug 1.–5.12. und
   Rückflug 20.–27.12. ergeben 40 Kombinationen. Mit `--naechte 14-21` bleiben nur passende Aufenthalte.
2. **Preiskalender:** Er holt für jede Kombination den günstigsten Preis, alle Flughäfen in einer Abfrage.
3. **Verbindungen:** Für die 5 günstigsten Termine lädt er die konkreten Flüge mit Uhrzeiten und Dauer.
4. **Bewertung:** **Preis + Zeitwert × Reisezeit (beide Richtungen)**. Der Zeitwert sagt, was eine Stunde
   weniger im Flugzeug oder beim Umsteigen wert ist: standardmäßig 25 € (Economy), 35 € (Premium),
   50 € (Business) und 75 € (First), änderbar mit `--zeitwert`.

Am Ende stehen die **Empfehlung** (beste Bewertung), die **günstigste** und die **schnellste** Verbindung,
jeweils mit Link zu Google Flights zum Prüfen und Buchen.

## Einrichten (einmalig)

Benötigt **Python 3.10 oder neuer** (macOS: `brew install python`, Windows: [python.org](https://www.python.org/downloads/)).

```bash
cd flugsuche
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Suchen über die Kommandozeile

```bash
python3 flugsuche.py --von FRA,MUC --nach Bangkok --hin 1.12.-5.12. --rueck 20.12.-27.12. --klasse business
python3 flugsuche.py --von Berlin --nach London --hin 14.11.2026 --direkt
python3 flugsuche.py            # fragt die Angaben nacheinander ab
```

So sieht die Ausgabe aus (Beispiel mit erfundenen Werten, gekürzt):

```
✈  FRA, MUC → BKK, DMK · Business · 1 Erwachsener · Google Flights
   Hinflug Di 01.12.–Sa 05.12.2026 · Rückflug So 20.12.–So 27.12.2026
   40 Datumskombination(en) geprüft, 38 mit Preis; Verbindungen geladen für die 5 günstigsten
   Bewertung = Preis + 50 € je Stunde Reisezeit (kleiner ist besser), Preise für alle Reisenden zusammen

Preiskalender – günstigster Gesamtpreis je Kombination (EUR, ★ = am günstigsten)
Hin \ Rück  So 20.12.  Mo 21.12.  Di 22.12.  …
Di 01.12.      2.480      2.395      2.410   …
Mi 02.12.      2.350★     2.420      2.515   …

Beste Verbindungen (10 von 23):

 1. 2.350 € · Reisezeit 23 Std. 35 Min. · Bewertung 3.529   ★ Empfehlung · schnellste
    Hin   Mi 02.12.  21:35 FRA → 13:25 (+1) BKK   10 Std. 50 Min., direkt   Thai Airways TG 921
    Rück  So 20.12.  00:05 BKK → 06:50 FRA   12 Std. 45 Min., direkt   Thai Airways TG 920
    https://www.google.com/travel/flights/booking?tfs=…
```

| Option | Bedeutung |
|---|---|
| `--von`, `--nach` | Flughafen-Codes oder Städte, mit Komma getrennt. Städte wie London, Paris, Mailand oder New York stehen für alle ihre Flughäfen |
| `--hin`, `--rueck` | Tag oder Zeitraum: `1.12.2026`, `1.12.-5.12.`, `20.12. bis 5.1.`, `2026-12-01..2026-12-05`. Ohne Jahr gilt das nächste passende Datum. Ohne `--rueck` wird nur der Hinflug gesucht |
| `--klasse` | `economy` (Standard), `premium`, `business`, `first` |
| `--personen` | Anzahl Erwachsene (Standard 1). Die Preise gelten für alle zusammen |
| `--direkt`, `--max-umstiege N` | nur Direktflüge bzw. höchstens N Umstiege je Richtung |
| `--naechte 7-14` | nur Termine mit 7 bis 14 Nächten am Ziel |
| `--max-reisezeit 15` | höchstens 15 Stunden je Richtung |
| `--zeitwert 40` | was dir eine Stunde weniger Reisezeit wert ist, in Euro |
| `--details N` | Verbindungen für die N günstigsten Termine laden (Standard 5, bei SerpApi 3) |
| `--quelle serpapi` | Datenquelle wechseln, siehe unten |
| `--json` | Ergebnis als JSON (für eigene Auswertungen) |

## KI-Agent: suchen in normaler Sprache

```bash
export ANTHROPIC_API_KEY=sk-ant-…        # Schlüssel unter console.anthropic.com → API Keys
python3 agent.py
```

```
Du: Anfang Dezember von Frankfurt oder München nach Bangkok, zurück kurz vor Weihnachten, Business für zwei
```

Der Agent fragt fehlende Angaben nach, wählt die Flughäfen und rechnet „Anfang Dezember“ in Daten um.
Danach startet er die Suche (bei Bedarf mehrmals, z. B. um Economy und Business zu vergleichen) und
begründet seine Empfehlung. Er läuft mit Claude Opus 5.5 und wird über dein Anthropic-Konto abgerechnet,
meist wenige Cent pro Suche. Lehnt das Modell eine Anfrage aus Sicherheitsgründen ab, antwortet
automatisch ein Ersatzmodell (Server-Fallback, eingeschaltet in `agent.py`).

## In Claude Code (auch in der Claude-App)

Im Repo liegt der Skill `.claude/skills/flugsuche`. In einer Claude-Code-Sitzung mit diesem Repo reicht
z. B. *„Finde mir den günstigsten Flug von Hamburg nach Lissabon, hin 3.–6.3., zurück 10.–14.3.“*.
Claude richtet die Suche selbst ein, führt `flugsuche.py` aus und fasst das Ergebnis zusammen.
Dafür ist kein Anthropic-API-Schlüssel nötig.

Für **Cloud-Sitzungen** (Claude Code im Web oder in der App) muss die Netzwerk-Einstellung der Umgebung
`www.google.com` erlauben, bei SerpApi `serpapi.com`: Umgebung → *Bearbeiten* → *Netzwerkzugriff*
([Anleitung](https://code.claude.com/docs/en/cloud-environments#network-access)).

## Datenquellen

| Quelle | Kosten | Hinweise |
|---|---|---|
| `google` (Standard) | kostenlos, ohne Anmeldung | Liest über die Bibliothek [fli](https://github.com/punitarani/fli) die öffentliche Google-Flights-Seite. Das ist inoffiziell: Ändert Google die Seite, kann die Suche ausfallen, bis fli angepasst ist. Dann in `requirements.txt` einen neueren Commit eintragen oder SerpApi nutzen. Bei sehr vielen Abfragen bremst Google zeitweise (HTTP 429) |
| `serpapi` | kleines Gratis-Kontingent pro Monat, danach kostenpflichtig | Offizielle API mit denselben Google-Flights-Daten. Kostenlos auf [serpapi.com](https://serpapi.com) registrieren, dann `export SERPAPI_KEY=…` und `--quelle serpapi`. Verbrauch: 1 Abfrage je Termin plus 2 je Termin mit Verbindungen; die Ausgabe zeigt die Zahl |

Andere Quellen fehlen bewusst: Die Self-Service-API von Amadeus wurde im Juli 2026 eingestellt,
Skyscanner und Kiwi.com geben ihre Schnittstellen nur an Geschäftspartner heraus.

## Gut zu wissen

- **Preise** sind Momentaufnahmen inkl. Steuern und Gebühren, aber ohne aufgegebenes Gepäck. Vor dem Buchen auf der verlinkten Seite prüfen.
- **Mehrere Flughäfen:** Der Rückflug kann an einem anderen Flughafen der Liste landen (hin ab FRA, zurück nach MUC). Die Ausgabe zeigt die Flughäfen.
- **Dauer:** Jede Kombination kostet einen Seitenabruf (parallel), jeder Termin mit Verbindungen einige weitere. Höchstens 100 Kombinationen pro Suche (`--max-kombinationen`).
- **Gleiche Flüge an anderen Tagen** erscheinen in der Liste nur einmal mit ihrem besten Termin. Wie der Preis über die Tage schwankt, zeigt der Preiskalender.
- **Kinder und Babys** werden nicht unterstützt: Für sie liefert Google auf der öffentlichen Seite kaum Preise.

## Tests

```bash
cd flugsuche && pip install pytest && python3 -m pytest -q
```

Die Tests brauchen kein Internet: Google Flights, SerpApi und Claude werden durch nachgebaute Antworten ersetzt.
