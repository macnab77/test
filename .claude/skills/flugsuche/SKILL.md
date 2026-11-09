---
name: flugsuche
description: Sucht die günstigste Flugverbindung mit kurzer Reisezeit über mehrere Start- und Zielflughäfen und flexible Zeiträume für Hin- und Rückflug (Economy, Premium, Business, First). Verwenden, wenn nach Flügen, Flugpreisen oder der günstigsten Flugverbindung gefragt wird.
---

# Flugsuche

Das Werkzeug liegt in `flugsuche/` im Repo (Anleitung: `flugsuche/README.md`).

1. **Angaben klären:** Nötig sind Abflugort(e), Ziel(e) und der Zeitraum für den Hinflug, bei Hin und Rück
   auch für den Rückflug. Fehlt etwas davon, kurz nachfragen. Ohne Angabe gelten Economy und 1 Erwachsener;
   das in der Antwort erwähnen. Relative Angaben („Anfang Dezember“, „2–3 Wochen“) in Daten bzw. `--naechte` umrechnen.
2. **Einrichten**, falls `flugsuche/.venv` fehlt:
   `cd flugsuche && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. **Suchen** (Flughäfen oder Städte mit Komma; Städte wie London stehen für alle ihre Flughäfen):
   `cd flugsuche && .venv/bin/python flugsuche.py --von FRA,MUC --nach BKK --hin 2026-12-01..2026-12-05 --rueck 2026-12-20..2026-12-27 --klasse business --json`
   Weitere Optionen: `--direkt`, `--max-umstiege N`, `--naechte 7-14`, `--max-reisezeit STUNDEN`, `--personen N`,
   `--zeitwert EURO`, `--quelle serpapi` (braucht `SERPAPI_KEY`). Höchstens 100 Kombinationen aus Hin- und
   Rückflugtag je Suche; sonst mit `--naechte` eingrenzen oder engere Zeiträume vorschlagen.
4. **Ergebnis zusammenfassen:** Empfehlung (Platz 1 der `rangliste`), günstigste und schnellste Verbindung mit
   Preis, Tagen, Uhrzeiten, Dauer, Umstiegen und Link, dazu ein Satz, was für die Empfehlung spricht.
   Nur Flüge und Preise aus der Ausgabe nennen; die Preise gelten für alle Reisenden zusammen und sind vor
   dem Buchen auf der verlinkten Seite zu prüfen.
5. **Preisvergleich empfehlen:** Immer darauf hinweisen, dass Google meist nur den Preis der Fluglinie zeigt und
   Online-Reisebüros (Booking.com, Trip.com, Gotogate, Flightnetwork …) denselben Flug oft günstiger anbieten.
   Der Nutzer soll die empfohlene Verbindung (Flugnummern und Tage) auf Skyscanner oder Kayak vergleichen und
   bei Reisebüros auf Endpreis an der Kasse, Tarifbedingungen und Umbuchung über das Reisebüro achten. Diese
   Seiten nicht selbst abrufen: Sie sperren automatische Abrufe per Bot-Schutz.

Meldet die Suche in einer Cloud-Sitzung, Google Flights oder SerpApi sei nicht erreichbar, blockiert
vermutlich die Netzwerk-Einstellung der Umgebung `www.google.com` bzw. `serpapi.com`. Das dem Nutzer so
sagen (Freigabe unter Umgebung → Bearbeiten → Netzwerkzugriff), statt Ergebnisse zu schätzen.
