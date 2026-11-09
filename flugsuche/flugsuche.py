#!/usr/bin/env python3
"""Findet die günstigste Flugverbindung mit kurzer Reisezeit.

Beispiele:
  python3 flugsuche.py --von FRA,MUC --nach Bangkok --hin 1.12.-5.12. --rueck 20.12.-27.12.
  python3 flugsuche.py --von Berlin --nach London --hin 14.11.2026 --klasse business
  python3 flugsuche.py --von HAM --nach "New York" --hin 2026-12-01..2026-12-03 \\
      --rueck 2026-12-10..2026-12-14 --direkt
  python3 flugsuche.py        (fragt die Angaben nacheinander ab)

Mehrere Flughäfen mit Komma trennen; Städte wie London oder Mailand stehen für
alle ihre Flughäfen. Ohne --rueck wird nur der Hinflug gesucht.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

from report import StatusLine, render_text, result_to_dict
from search import (
    SearchError,
    SearchRequest,
    parse_airports,
    parse_cabin,
    parse_date_range,
    parse_nights,
    run_search,
)
from sources import make_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Findet die günstigste Flugverbindung mit kurzer Reisezeit.",
        epilog=__doc__.split("\n", 2)[2],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    trip = parser.add_argument_group("Reise")
    trip.add_argument("--von", metavar="ORTE", help="Abflughäfen oder Städte, z. B. FRA,MUC oder Frankfurt")
    trip.add_argument("--nach", metavar="ORTE", help="Zielflughäfen oder Städte, z. B. BKK,HKT oder London")
    trip.add_argument("--hin", metavar="DATUM", help="Hinflug: Tag oder Zeitraum, z. B. 1.12.-5.12.")
    trip.add_argument(
        "--rueck", "--rück", dest="rueck", metavar="DATUM",
        help="Rückflug: Tag oder Zeitraum (weglassen = nur Hinflug)",
    )
    trip.add_argument(
        "--klasse", default="economy", metavar="KLASSE", help="economy (Standard), premium, business oder first"
    )
    trip.add_argument("--personen", type=int, default=1, metavar="N", help="Anzahl Erwachsene (Standard 1)")

    rules = parser.add_argument_group("Vorgaben")
    rules.add_argument("--direkt", action="store_true", help="nur Direktflüge")
    rules.add_argument(
        "--max-umstiege", type=int, choices=[0, 1, 2], metavar="N",
        help="höchstens N Umstiege je Richtung (0, 1 oder 2)",
    )
    rules.add_argument(
        "--naechte", "--nächte", dest="naechte", metavar="VON-BIS", help="Aufenthalt am Ziel in Nächten, z. B. 7-14"
    )
    rules.add_argument("--max-reisezeit", type=float, metavar="STUNDEN", help="längste Reisezeit je Richtung")
    rules.add_argument(
        "--zeitwert", type=float, metavar="EURO",
        help="was eine Stunde weniger Reisezeit wert ist (Standard 25 Economy, 35 Premium, 50 Business, 75 First)",
    )

    options = parser.add_argument_group("Suche")
    options.add_argument(
        "--quelle", choices=["google", "serpapi"], default=os.environ.get("FLUGSUCHE_QUELLE", "google"),
        help="Datenquelle (Standard google, kostenlos)",
    )
    options.add_argument(
        "--details", type=int, metavar="N",
        help="Verbindungen für die N günstigsten Termine laden (Standard 5, SerpApi 3)",
    )
    options.add_argument(
        "--max-kombinationen", type=int, default=100, metavar="N",
        help="höchstens N Kombinationen aus Hin- und Rückflugtag (Standard 100)",
    )
    options.add_argument("--top", type=int, default=10, metavar="N", help="N Verbindungen anzeigen (Standard 10)")
    options.add_argument(
        "--waehrung", "--währung", dest="waehrung", default="EUR", metavar="CODE", help="Währung (Standard EUR)"
    )
    options.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")
    return parser


def ask(question: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    return input(f"{question}{hint}: ").strip() or default


def ask_missing(args: argparse.Namespace) -> None:
    """Fragt fehlende Angaben im Terminal ab."""
    print("Flugsuche – Mehrere Flughäfen mit Komma trennen, [Wert] = Vorgabe.\n")
    args.von = args.von or ask("Abflug (Flughäfen oder Städte)")
    args.nach = args.nach or ask("Ziel (Flughäfen oder Städte)")
    args.hin = args.hin or ask("Hinflug (Tag oder Zeitraum, z. B. 1.12.-5.12.)")
    if args.rueck is None:
        args.rueck = ask("Rückflug (Tag oder Zeitraum, leer = nur Hinflug)") or None
    args.klasse = ask("Klasse (economy, premium, business, first)", args.klasse)
    persons = ask("Erwachsene", str(args.personen))
    if not persons.isdigit():
        raise SearchError(f"'{persons}' ist keine Anzahl.")
    args.personen = int(persons)
    print()


def request_from_args(args: argparse.Namespace, today: date) -> SearchRequest:
    depart_from, depart_to = parse_date_range(args.hin, today)
    return_from = return_to = None
    if args.rueck:
        # Ein Rückflugtag ohne Jahr liegt nach dem Hinflug (20.12. -> 5.1. = Januar).
        return_from, return_to = parse_date_range(args.rueck, depart_from)
    min_nights = max_nights = None
    if args.naechte:
        min_nights, max_nights = parse_nights(args.naechte)
    return SearchRequest(
        origins=parse_airports(args.von),
        destinations=parse_airports(args.nach),
        depart_from=depart_from,
        depart_to=depart_to,
        return_from=return_from,
        return_to=return_to,
        cabin=parse_cabin(args.klasse),
        adults=args.personen,
        max_stops=0 if args.direkt else args.max_umstiege,
        min_nights=min_nights,
        max_nights=max_nights,
        max_hours=args.max_reisezeit,
        time_value=args.zeitwert,
        currency=args.waehrung.upper(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    today = date.today()
    status = StatusLine()
    try:
        if not (args.von and args.nach and args.hin):
            if not sys.stdin.isatty():
                parser.error("--von, --nach und --hin werden benötigt")
            ask_missing(args)
        request = request_from_args(args, today)
        source = make_source(args.quelle)
        result = run_search(
            request,
            source,
            details=args.details or source.default_details,
            max_pairs=args.max_kombinationen,
            progress=status,
            today=today,
        )
    except SearchError as error:
        status.clear()
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        status.clear()
        print("\nAbgebrochen.", file=sys.stderr)
        return 130
    status.clear()
    if args.json:
        print(json.dumps(result_to_dict(result, top=args.top), ensure_ascii=False, indent=2))
    else:
        print(render_text(result, top=args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
