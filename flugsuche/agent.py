#!/usr/bin/env python3
"""KI-Agent für die Flugsuche: versteht Reisewünsche in normaler Sprache.

  python3 agent.py
  python3 agent.py "Ab Frankfurt oder München nach Bangkok, Anfang Dezember für 2-3 Wochen, Business"

Der Agent (Claude) fragt fehlende Angaben nach, wählt passende Flughäfen,
startet die Suche (dieselbe wie flugsuche.py, bei Bedarf mehrmals, z. B. um
Economy und Business zu vergleichen) und erklärt das Ergebnis.
Braucht einen Anthropic-API-Schlüssel: export ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from typing import Literal

import anthropic
from anthropic import beta_tool
from anthropic.lib.tools import ToolError

from report import WEEKDAYS, StatusLine, fmt_price, result_to_dict
from search import (
    CABINS,
    CITY_AIRPORTS,
    SearchError,
    SearchRequest,
    normalize_place,
    parse_airports,
    run_search,
)
from sources import make_source

MODEL = "claude-opus-5-5"
EXIT_WORDS = {"ende", "exit", "quit", "tschüss", "bye"}


def system_prompt(today: date) -> str:
    return f"""Du bist ein Reiseassistent, der die günstigste Flugverbindung mit möglichst kurzer Reisezeit findet. Heute ist {WEEKDAYS[today.weekday()]}, der {today:%d.%m.%Y}.

Für eine Suche brauchst du Abflugort(e), Ziel(e) und den Zeitraum für den Hinflug, bei Hin- und Rückflug auch den für den Rückflug. Fehlt davon etwas, frag kurz nach. Fehlen Klasse oder Personenzahl, nimm Economy und eine Person an und sag das dazu.

- Rechne Angaben wie „Anfang Dezember“ oder „zwei bis drei Wochen“ in konkrete Daten bzw. naechte_min/naechte_max um.
- Nimm bei Städten mit mehreren Flughäfen alle sinnvollen auf (London: LHR, LGW, STN, LTN, LCY) und suche alle Flughäfen in einem Aufruf. Bei unbekannten Orten hilft flughafen_suchen.
- Eine Suche umfasst höchstens 100 Kombinationen aus Hin- und Rückflugtag. Sind es mehr, grenze mit naechte_min/naechte_max ein oder schlag engere Zeiträume vor.
- Zeig danach die Empfehlung (Platz 1) sowie die günstigste und die schnellste Verbindung mit Preis, Tagen, Uhrzeiten, Dauer, Umstiegen und Link, und sag in einem Satz, was für die Empfehlung spricht (etwa: 80 € teurer, aber 5 Stunden schneller). Biete passende nächste Schritte an, z. B. Business statt Economy oder nur Direktflüge.
- Nenne nur Flüge und Preise aus den Suchergebnissen. Die Preise sind Momentaufnahmen für alle Reisenden zusammen und vor der Buchung auf der verlinkten Seite zu prüfen.

Antworte auf Deutsch, knapp und übersichtlich. Die Antwort erscheint im Terminal: schlichter Text ohne Markdown-Fettdruck und ohne Tabellen."""


def make_tools(source_name: str, status: StatusLine) -> list:
    """Werkzeuge, die Claude aufrufen darf. Sie laufen lokal auf deinem Rechner."""

    @beta_tool
    def flug_suchen(
        von: list[str],
        nach: list[str],
        hinflug_von: str,
        hinflug_bis: str,
        rueckflug_von: str | None = None,
        rueckflug_bis: str | None = None,
        klasse: Literal["economy", "premium", "business", "first"] = "economy",
        erwachsene: int = 1,
        max_umstiege: int | None = None,
        naechte_min: int | None = None,
        naechte_max: int | None = None,
        max_reisezeit_stunden: float | None = None,
        zeitwert: float | None = None,
    ) -> str:
        """Sucht die günstigsten Flugverbindungen mit kurzer Reisezeit (Daten von Google Flights).

        Prüft alle Kombinationen aus Hin- und Rückflugtag in den Zeiträumen und alle
        Flughäfen auf einmal, lädt für die günstigsten Termine die Verbindungen und
        bewertet sie mit Preis + Zeitwert × Reisezeit. Liefert JSON mit den
        günstigsten Terminen und einer Rangliste (Platz 1 = Empfehlung, dazu die
        günstigste und die schnellste Verbindung) samt Link. Eine Suche dauert je
        nach Zahl der Termine etwa 10 Sekunden bis wenige Minuten.

        Args:
            von: Abflughäfen als IATA-Codes, z. B. ["FRA", "MUC"].
            nach: Zielflughäfen als IATA-Codes, z. B. ["BKK", "HKT"].
            hinflug_von: Frühester Hinflugtag, JJJJ-MM-TT.
            hinflug_bis: Spätester Hinflugtag, JJJJ-MM-TT (gleich hinflug_von für einen festen Tag).
            rueckflug_von: Frühester Rückflugtag, JJJJ-MM-TT. Weglassen für einen reinen Hinflug.
            rueckflug_bis: Spätester Rückflugtag, JJJJ-MM-TT.
            klasse: Reiseklasse.
            erwachsene: Anzahl Reisende (Erwachsene).
            max_umstiege: Höchstens so viele Umstiege je Richtung, 0 = nur Direktflüge. Weglassen = egal.
            naechte_min: Mindestaufenthalt am Ziel in Nächten.
            naechte_max: Höchstaufenthalt am Ziel in Nächten.
            max_reisezeit_stunden: Längste akzeptable Reisezeit je Richtung in Stunden.
            zeitwert: Euro, die eine Stunde weniger Reisezeit wert ist (Standard: 25 Economy,
                35 Premium, 50 Business, 75 First).
        """
        try:
            request = SearchRequest(
                origins=parse_airports(von),
                destinations=parse_airports(nach),
                depart_from=date.fromisoformat(hinflug_von),
                depart_to=date.fromisoformat(hinflug_bis),
                return_from=date.fromisoformat(rueckflug_von) if rueckflug_von else None,
                return_to=date.fromisoformat(rueckflug_bis) if rueckflug_bis else None,
                cabin=klasse,
                adults=erwachsene,
                max_stops=max_umstiege,
                min_nights=naechte_min,
                max_nights=naechte_max,
                max_hours=max_reisezeit_stunden,
                time_value=zeitwert,
            )
            route = f"{', '.join(request.origins)} → {', '.join(request.destinations)}"
            print(f"\n  Suche {route} ({CABINS[request.cabin]}) …", file=sys.stderr)
            source = make_source(source_name)
            result = run_search(request, source, details=source.default_details, progress=status)
        except SearchError as error:
            raise ToolError(str(error)) from None
        except ValueError as error:  # Datum nicht im Format JJJJ-MM-TT
            raise ToolError(f"Ungültige Eingabe: {error}") from None
        finally:
            status.clear()
        best = result.best
        summary = f"{len(result.pairs)} Termin(e) geprüft"
        if best:
            summary += f", Empfehlung {fmt_price(best.price, best.currency)}"
        print(f"  {summary}", file=sys.stderr)
        return json.dumps(result_to_dict(result), ensure_ascii=False)

    @beta_tool
    def flughafen_suchen(suchbegriff: str) -> str:
        """Findet IATA-Codes zu einer Stadt, Insel oder einem Flughafennamen.

        Args:
            suchbegriff: z. B. "Mailand", "Teneriffa", "Heathrow" oder "Phuket" (auch englische Namen).
        """
        found = [
            {"code": code, "hinweis": "Flughafen der Stadt"}
            for code in CITY_AIRPORTS.get(normalize_place(suchbegriff), [])
        ]
        try:
            from fli.core import search_airports
        except ImportError:
            search_airports = None
        if search_airports:
            known = {entry["code"] for entry in found}
            for match in search_airports(suchbegriff, limit=8):
                code = match.code.name.removeprefix("_")
                if code not in known:
                    found.append({"code": code, "name": match.name})
        if not found:
            return "Nichts gefunden – einen anderen (auch englischen) Namen versuchen."
        return json.dumps(found, ensure_ascii=False)

    return [flug_suchen, flughafen_suchen]


class RefusedError(Exception):
    """Claude hat die Anfrage abgelehnt."""


def answer(client: anthropic.Anthropic, tools: list, system: str, messages: list) -> None:
    """Lässt Claude auf die letzte Nachricht antworten (inkl. Suchen) und gibt die Antwort aus."""
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        system=system,
        tools=tools,
        messages=messages,
        max_iterations=12,
        output_config={"effort": "medium"},
        # Lehnt das Modell eine Anfrage ab, beantwortet sie automatisch ein Ersatzmodell.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    for message in runner:
        # Den Verlauf selbst mitschreiben – der Runner arbeitet auf einer Kopie.
        messages.append({"role": "assistant", "content": message.content})
        tool_response = runner.generate_tool_call_response()
        if tool_response is not None:
            messages.append(tool_response)
        text = "\n".join(block.text for block in message.content if block.type == "text").strip()
        if text:
            print(f"\nAgent: {text}")
        if message.stop_reason == "refusal":
            raise RefusedError
        if message.stop_reason == "max_tokens":
            print("\n(Die Antwort wurde abgeschnitten.)")


def chat(client: anthropic.Anthropic, tools: list, system: str, first: str | None = None) -> None:
    messages: list = []
    pending = first
    while True:
        if pending is None:
            try:
                pending = input("\nDu: ").strip()
            except EOFError:
                print()
                return
        text, pending = pending, None
        if not text:
            continue
        if text.lower() in EXIT_WORDS:
            return
        start = len(messages)
        messages.append({"role": "user", "content": text})
        try:
            answer(client, tools, system, messages)
            continue
        except RefusedError:
            print("\nAgent: Dabei kann ich nicht helfen. Frag mich gern nach Flügen.")
        except anthropic.AuthenticationError:
            print("Fehler: Der Anthropic-API-Schlüssel wurde abgelehnt – ANTHROPIC_API_KEY prüfen.",
                  file=sys.stderr)
            return
        except anthropic.RateLimitError:
            print("Fehler: Zu viele Anfragen an Claude – kurz warten und nochmal versuchen.", file=sys.stderr)
        except anthropic.APIStatusError as error:
            print(f"Fehler der Claude-API ({error.status_code}): {error.message}", file=sys.stderr)
        except anthropic.APIConnectionError:
            print("Fehler: Keine Verbindung zur Claude-API – Internetverbindung prüfen.", file=sys.stderr)
        except TypeError as error:
            if "authentication" not in str(error):
                raise
            print(
                "Fehler: Kein Anthropic-API-Schlüssel gefunden – 'export ANTHROPIC_API_KEY=sk-ant-…' setzen "
                "(Schlüssel unter console.anthropic.com).",
                file=sys.stderr,
            )
            return
        # Fehlgeschlagene Runde aus dem Verlauf nehmen, damit es danach normal weitergeht.
        del messages[start:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KI-Agent für die Flugsuche (braucht ANTHROPIC_API_KEY).")
    parser.add_argument("nachricht", nargs="*", help='erste Nachricht, z. B. "Von FRA nach BKK im Dezember"')
    parser.add_argument(
        "--quelle",
        choices=["google", "serpapi"],
        default=os.environ.get("FLUGSUCHE_QUELLE", "google"),
        help="Datenquelle (Standard google, kostenlos)",
    )
    args = parser.parse_args(argv)
    try:
        make_source(args.quelle)  # früh prüfen, ob die Quelle nutzbar ist
    except SearchError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    status = StatusLine()
    print("Flugsuche-Agent – beschreib deine Reise. Beenden mit 'ende' oder Strg+C.")
    try:
        tools = make_tools(args.quelle, status)
        chat(anthropic.Anthropic(), tools, system_prompt(date.today()), " ".join(args.nachricht) or None)
    except KeyboardInterrupt:
        status.clear()
        print("\nTschüss!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
