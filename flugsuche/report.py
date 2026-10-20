"""Ausgabe der Suchergebnisse: als Text fürs Terminal und als JSON (für den Agenten)."""

from __future__ import annotations

import sys
from datetime import date

from search import CABINS, Journey, Offer, SearchResult

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def fmt_number(value: float) -> str:
    """2190.4 -> '2.190'"""
    return f"{value:,.0f}".replace(",", ".")


def fmt_price(value: float, currency: str = "EUR") -> str:
    """2190.0 -> '2.190 €'"""
    return f"{fmt_number(value)} €" if currency == "EUR" else f"{fmt_number(value)} {currency}"


def fmt_duration(minutes: int) -> str:
    """650 -> '10 Std. 50 Min.'"""
    hours, mins = divmod(int(minutes), 60)
    if not hours:
        return f"{mins} Min."
    return f"{hours} Std. {mins} Min." if mins else f"{hours} Std."


def fmt_day(day: date, year: bool = False) -> str:
    """date(2026, 12, 2) -> 'Mi 02.12.'"""
    text = f"{WEEKDAYS[day.weekday()]} {day:%d.%m.}"
    return f"{text}{day:%Y}" if year else text


def fmt_range(start: date, end: date) -> str:
    if start == end:
        return fmt_day(start, year=True)
    return f"{fmt_day(start)}–{fmt_day(end, year=True)}"


def fmt_stops(journey: Journey) -> str:
    if not journey.stops:
        return "direkt"
    word = "Umstieg" if journey.stops == 1 else "Umstiege"
    return f"{journey.stops} {word} ({', '.join(journey.via)})"


def _day_shift(journey: Journey) -> int:
    """Ankunft so viele Tage nach dem Abflugtag (Ortszeit)."""
    return (journey.arrival.date() - journey.departure.date()).days


def journey_line(label: str, journey: Journey) -> str:
    shift = _day_shift(journey)
    arrival = f"{journey.arrival:%H:%M}" + (f" ({shift:+d})" if shift else "")
    flights = ", ".join(leg.flight_number for leg in journey.legs)
    return (
        f"{label:<5} {fmt_day(journey.departure.date())}  "
        f"{journey.departure:%H:%M} {journey.origin} → {arrival} {journey.destination}   "
        f"{fmt_duration(journey.duration)}, {fmt_stops(journey)}   {' / '.join(journey.airlines)} {flights}"
    )


def _labels(result: SearchResult, offer: Offer) -> list[str]:
    labels = []
    if offer is result.best:
        labels.append("Empfehlung")
    if offer is result.cheapest:
        labels.append("günstigste")
    if offer is result.fastest:
        labels.append("schnellste")
    return labels


def _highlighted(result: SearchResult, top: int) -> list[Offer]:
    """Die besten `top` Verbindungen, dazu günstigste und schnellste, falls nicht dabei."""
    shown = result.offers[:top]
    for offer in (result.cheapest, result.fastest):
        if offer is not None and all(offer is not other for other in shown):
            shown.append(offer)
    return shown


# --- Text --------------------------------------------------------------------

class StatusLine:
    """Fortschritt in einer einzigen, sich überschreibenden Zeile (nur im Terminal)."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stderr
        self.width = 0

    def __call__(self, message: str) -> None:
        if not self.stream.isatty():
            return
        self.stream.write("\r" + message.ljust(self.width))
        self.stream.flush()
        self.width = len(message)

    def clear(self) -> None:
        if self.width:
            self.stream.write("\r" + " " * self.width + "\r")
            self.stream.flush()
            self.width = 0


def render_calendar(result: SearchResult) -> list[str]:
    r = result.request
    calendar = result.calendar
    best = min(calendar.values())
    currency = result.best.currency if result.best else r.currency  # so, wie die Quelle die Preise liefert

    def cell(pair, width: int) -> str:
        if pair not in calendar:
            return f"{'–':>{width}}"
        price = calendar[pair]
        star = "★" if price == best else " "
        return f"{fmt_number(price) + star:>{width}}"

    if not r.round_trip:
        lines = [f"Preiskalender – günstigster Preis je Tag ({currency}, ★ = am günstigsten)"]
        lines += [f"  {fmt_day(pair[0])}  {cell(pair, 9)}" for pair in sorted(result.pairs)]
        return lines

    searched = set(result.pairs)
    outbound_days = sorted({pair[0] for pair in result.pairs})
    return_days = sorted({pair[1] for pair in result.pairs})
    lines = [
        f"Preiskalender – günstigster Gesamtpreis je Kombination ({currency}, ★ = am günstigsten)",
        "Hin \\ Rück" + "".join(f"{fmt_day(day):>11}" for day in return_days),
    ]
    for out in outbound_days:
        cells = [cell((out, back), 11) if (out, back) in searched else " " * 11 for back in return_days]
        lines.append(f"{fmt_day(out):<10}" + "".join(cells))
    return lines


def render_offer(result: SearchResult, offer: Offer) -> list[str]:
    rank = result.offers.index(offer) + 1
    labels = _labels(result, offer)
    head = (
        f"{rank:>2}. {fmt_price(offer.price, offer.currency)} · Reisezeit {fmt_duration(offer.duration)}"
        f" · Bewertung {fmt_number(offer.score)}"
    )
    if labels and labels[0] == "Empfehlung":
        labels[0] = "★ Empfehlung"
    lines = [head + (f"   {' · '.join(labels)}" if labels else "")]
    lines.append("    " + journey_line("Hin", offer.outbound))
    if offer.inbound:
        lines.append("    " + journey_line("Rück", offer.inbound))
    if offer.link:
        lines.append(f"    {offer.link}")
    return lines


def render_text(result: SearchResult, top: int = 10) -> str:
    r = result.request
    persons = "1 Erwachsener" if r.adults == 1 else f"{r.adults} Erwachsene"
    period = f"Hinflug {fmt_range(r.depart_from, r.depart_to)}"
    if r.round_trip:
        period += f" · Rückflug {fmt_range(r.return_from, r.return_to)}"
    lines = [
        f"✈  {', '.join(r.origins)} → {', '.join(r.destinations)} · {CABINS[r.cabin]} · {persons}"
        f" · {result.source}",
        f"   {period}",
        f"   {len(result.pairs)} Datumskombination(en) geprüft, {len(result.calendar)} mit Preis;"
        f" Verbindungen geladen für die {len(result.detailed)} günstigsten",
        f"   Bewertung = Preis + {fmt_price(r.value_per_hour, r.currency)} je Stunde Reisezeit"
        " (kleiner ist besser), Preise für alle Reisenden zusammen",
    ]
    if len(result.pairs) > 1 and result.calendar:
        lines += ["", *render_calendar(result)]
    lines.append("")
    if result.offers:
        shown = _highlighted(result, top)
        lines.append(f"Beste Verbindungen ({min(top, len(result.offers))} von {len(result.offers)}):")
        for offer in shown:
            lines += ["", *render_offer(result, offer)]
    else:
        lines.append("Keine passenden Verbindungen gefunden.")
    if result.requests is not None:
        lines.append(f"\nSerpApi-Abfragen bei dieser Suche: {result.requests}")
    for note in result.notes:
        lines.append(f"Hinweis: {note}")
    return "\n".join(lines)


# --- JSON --------------------------------------------------------------------

def journey_to_dict(journey: Journey | None) -> dict | None:
    if journey is None:
        return None
    shift = _day_shift(journey)
    return {
        "datum": journey.departure.date().isoformat(),
        "abflug": f"{journey.origin} {journey.departure:%H:%M}",
        "ankunft": f"{journey.destination} {journey.arrival:%H:%M}" + (f" ({shift:+d} Tag)" if shift else ""),
        "dauer": fmt_duration(journey.duration),
        "umstiege": journey.stops,
        "via": journey.via,
        "fluege": [f"{leg.flight_number} ({leg.airline})" for leg in journey.legs],
    }


def offer_to_dict(result: SearchResult, offer: Offer) -> dict:
    return {
        "platz": result.offers.index(offer) + 1,
        "markierung": _labels(result, offer),
        "preis": round(offer.price, 2),
        "waehrung": offer.currency,
        "reisezeit_gesamt": fmt_duration(offer.duration),
        "bewertung": round(offer.score),
        "hinflug": journey_to_dict(offer.outbound),
        "rueckflug": journey_to_dict(offer.inbound),
        "link": offer.link,
    }


def result_to_dict(result: SearchResult, top: int = 8) -> dict:
    r = result.request
    cheapest_dates = sorted(result.calendar.items(), key=lambda item: item[1])[:10]
    return {
        "suche": {
            "von": r.origins,
            "nach": r.destinations,
            "hinflug_zeitraum": [r.depart_from.isoformat(), r.depart_to.isoformat()],
            "rueckflug_zeitraum": [r.return_from.isoformat(), r.return_to.isoformat()] if r.round_trip else None,
            "klasse": CABINS[r.cabin],
            "erwachsene": r.adults,
            "max_umstiege": r.max_stops,
            "zeitwert_pro_stunde": r.value_per_hour,
            "quelle": result.source,
        },
        "datumskombinationen": {
            "geprueft": len(result.pairs),
            "mit_preis": len(result.calendar),
            "verbindungen_geladen_fuer": len(result.detailed),
        },
        "guenstigste_termine": [
            {"hin": out.isoformat(), "rueck": back.isoformat() if back else None, "preis": price}
            for (out, back), price in cheapest_dates
        ],
        "verbindungen_gesamt": len(result.offers),
        "rangliste": [offer_to_dict(result, offer) for offer in _highlighted(result, top)],
        "preise_gelten_fuer": "alle Reisenden zusammen",
        "hinweise": result.notes,
    }
