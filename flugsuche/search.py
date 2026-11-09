"""Kern der Flugsuche: Suchauftrag, Datumskombinationen, Bewertung und Ablauf.

Hier steckt keine Netzwerk-Logik. Die Datenquellen (sources.py) liefern Preise
und Verbindungen, dieses Modul entscheidet, was abgefragt und wie bewertet wird.
Dadurch lässt sich alles ohne Internet testen.

Ablauf einer Suche:
  1. Alle Paare aus Hin- und Rückflugtag bilden (Zeiträume, ggf. Nächte am Ziel).
  2. Preiskalender: günstigster Preis je Paar, alle Flughäfen in einer Abfrage.
  3. Für die günstigsten Paare die konkreten Verbindungen mit Flugzeiten laden.
  4. Bewerten: Preis + Zeitwert × Reisezeit. Die niedrigste Bewertung gewinnt.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol

CABINS = {
    "economy": "Economy",
    "premium": "Premium Economy",
    "business": "Business",
    "first": "First",
}
_CABIN_ALIASES = {
    "eco": "economy",
    "premiumeconomy": "premium",
    "businessclass": "business",
    "firstclass": "first",
    "erste": "first",
    "ersteklasse": "first",
}

# Was eine Stunde weniger Reisezeit wert ist (in der Suchwährung). Damit werden
# Preis und Flugdauer zu einer Zahl verrechnet. In teureren Klassen liegt der
# Wert höher, sonst würde dort fast nur der Preis entscheiden.
DEFAULT_TIME_VALUE = {"economy": 25.0, "premium": 35.0, "business": 50.0, "first": 75.0}

# Städte mit mehreren Flughäfen, deutsche Städtenamen und Sammelcodes -> IATA-Codes.
# Alle anderen Flughäfen direkt als Code angeben (FRA, MUC, ...).
CITY_AIRPORTS = {
    "berlin": ["BER"],
    "frankfurt": ["FRA"],
    "muenchen": ["MUC"],
    "hamburg": ["HAM"],
    "duesseldorf": ["DUS"],
    "koeln": ["CGN"],
    "koeln bonn": ["CGN"],
    "stuttgart": ["STR"],
    "hannover": ["HAJ"],
    "nuernberg": ["NUE"],
    "leipzig": ["LEJ"],
    "dresden": ["DRS"],
    "bremen": ["BRE"],
    "wien": ["VIE"],
    "zuerich": ["ZRH"],
    "genf": ["GVA"],
    "basel": ["BSL"],
    "london": ["LHR", "LGW", "STN", "LTN", "LCY"],
    "lon": ["LHR", "LGW", "STN", "LTN", "LCY"],
    "paris": ["CDG", "ORY"],
    "par": ["CDG", "ORY"],
    "mailand": ["MXP", "LIN", "BGY"],
    "mil": ["MXP", "LIN", "BGY"],
    "rom": ["FCO", "CIA"],
    "lissabon": ["LIS"],
    "mallorca": ["PMI"],
    "palma": ["PMI"],
    "teneriffa": ["TFS", "TFN"],
    "kreta": ["HER", "CHQ"],
    "athen": ["ATH"],
    "istanbul": ["IST", "SAW"],
    "dubai": ["DXB", "DWC"],
    "bangkok": ["BKK", "DMK"],
    "tokio": ["HND", "NRT"],
    "tyo": ["HND", "NRT"],
    "seoul": ["ICN", "GMP"],
    "peking": ["PEK", "PKX"],
    "shanghai": ["PVG", "SHA"],
    "singapur": ["SIN"],
    "hongkong": ["HKG"],
    "new york": ["JFK", "EWR", "LGA"],
    "nyc": ["JFK", "EWR", "LGA"],
    "washington": ["IAD", "DCA", "BWI"],
    "chicago": ["ORD", "MDW"],
    "miami": ["MIA", "FLL"],
    "kapstadt": ["CPT"],
}

DatePair = tuple[date, date | None]  # (Hinflugtag, Rückflugtag oder None)
Progress = Callable[[str], None]


class SearchError(Exception):
    """Fehler mit verständlicher Meldung für den Nutzer (ohne Traceback)."""


@dataclass
class Leg:
    """Ein einzelner Flug."""

    airline: str  # Name, z. B. "Lufthansa"
    flight_number: str  # z. B. "LH 400"
    origin: str  # IATA-Code
    destination: str
    departure: datetime  # Ortszeit am Abflughafen
    arrival: datetime  # Ortszeit am Zielflughafen
    duration: int  # Minuten


@dataclass
class Journey:
    """Eine Richtung (Hin- oder Rückflug), ggf. mit Umstiegen."""

    legs: list[Leg]
    duration: int  # Minuten inkl. Umstiegszeit

    @property
    def stops(self) -> int:
        return len(self.legs) - 1

    @property
    def origin(self) -> str:
        return self.legs[0].origin

    @property
    def destination(self) -> str:
        return self.legs[-1].destination

    @property
    def departure(self) -> datetime:
        return self.legs[0].departure

    @property
    def arrival(self) -> datetime:
        return self.legs[-1].arrival

    @property
    def via(self) -> list[str]:
        return [leg.destination for leg in self.legs[:-1]]

    @property
    def airlines(self) -> list[str]:
        return list(dict.fromkeys(leg.airline for leg in self.legs if leg.airline))


@dataclass
class Offer:
    """Eine Reise: Hinflug, ggf. Rückflug und Gesamtpreis."""

    price: float  # für alle Reisenden zusammen, 0 = Quelle nennt keinen Preis
    currency: str
    outbound: Journey
    inbound: Journey | None = None
    link: str = ""
    score: float = 0.0  # Preis + Zeitwert × Reisezeit, gesetzt von rank_offers()

    @property
    def duration(self) -> int:
        """Reisezeit beider Richtungen in Minuten."""
        return self.outbound.duration + (self.inbound.duration if self.inbound else 0)

    @property
    def journeys(self) -> list[Journey]:
        return [self.outbound] + ([self.inbound] if self.inbound else [])

    def flights(self) -> tuple[str, ...]:
        """Alle Flugnummern – gleich bei derselben Verbindung an anderen Tagen."""
        return tuple(leg.flight_number for journey in self.journeys for leg in journey.legs)


@dataclass
class SearchRequest:
    """Was gesucht wird. Zeiträume schließen Anfangs- und Endtag ein."""

    origins: list[str]
    destinations: list[str]
    depart_from: date
    depart_to: date
    return_from: date | None = None  # None = nur Hinflug
    return_to: date | None = None
    cabin: str = "economy"
    adults: int = 1
    max_stops: int | None = None  # je Richtung, 0 = nur Direktflüge
    min_nights: int | None = None  # Nächte am Ziel
    max_nights: int | None = None
    max_hours: float | None = None  # längste Reisezeit je Richtung
    time_value: float | None = None  # None = Standard der Klasse
    currency: str = "EUR"

    @property
    def round_trip(self) -> bool:
        return self.return_from is not None

    @property
    def value_per_hour(self) -> float:
        if self.time_value is not None:
            return self.time_value
        return DEFAULT_TIME_VALUE[self.cabin]

    def validate(self, today: date) -> list[str]:
        """Prüft die Angaben, wirft SearchError bei Fehlern und gibt Hinweise zurück.

        Ein Hinflug-Zeitraum, der schon begonnen hat, wird auf heute gekürzt.
        """
        notes = []
        if not self.origins or not self.destinations:
            raise SearchError("Bitte mindestens einen Abflug- und einen Zielflughafen angeben.")
        both = sorted(set(self.origins) & set(self.destinations))
        if both:
            raise SearchError(f"{', '.join(both)} steht bei Abflug und Ziel.")
        if self.cabin not in CABINS:
            raise SearchError(f"Unbekannte Klasse '{self.cabin}' (economy, premium, business, first).")
        if not 1 <= self.adults <= 9:
            raise SearchError("Es können 1 bis 9 Erwachsene gesucht werden.")
        if self.max_stops is not None and self.max_stops < 0:
            raise SearchError("Die Zahl der Umstiege kann nicht negativ sein.")
        if self.time_value is not None and self.time_value < 0:
            raise SearchError("Der Zeitwert kann nicht negativ sein.")
        if self.depart_to < self.depart_from:
            raise SearchError("Der Hinflug-Zeitraum endet vor seinem Beginn.")
        if self.depart_to < today:
            raise SearchError("Der Hinflug-Zeitraum liegt in der Vergangenheit.")
        if self.depart_from < today:
            self.depart_from = today
            notes.append(f"Hinflug frühestens ab heute ({today:%d.%m.%Y}) gesucht.")
        if self.round_trip:
            if self.return_to is None:
                self.return_to = self.return_from
            if self.return_to < self.return_from:
                raise SearchError("Der Rückflug-Zeitraum endet vor seinem Beginn.")
            if self.return_to < self.depart_from:
                raise SearchError("Der Rückflug-Zeitraum liegt vor dem Hinflug.")
        if (
            self.min_nights is not None
            and self.max_nights is not None
            and self.min_nights > self.max_nights
        ):
            raise SearchError("Die Mindestzahl der Nächte ist größer als die Höchstzahl.")
        return notes


class Source(Protocol):
    """Datenquelle, siehe sources.py."""

    name: str
    notes: list[str]  # Hinweise für den Nutzer, z. B. zu fehlgeschlagenen Abfragen

    def price_calendar(
        self, request: SearchRequest, pairs: list[DatePair], progress: Progress
    ) -> dict[DatePair, float]:
        """Günstigster Gesamtpreis je Datumspaar. Paare ohne Flüge fehlen."""
        ...

    def offers(self, request: SearchRequest, pair: DatePair) -> list[Offer]:
        """Konkrete Verbindungen mit Flugzeiten für ein Datumspaar."""
        ...


@dataclass
class SearchResult:
    request: SearchRequest
    source: str
    pairs: list[DatePair]  # alle geprüften Datumspaare
    calendar: dict[DatePair, float]  # günstigster Preis je Paar
    detailed: list[DatePair]  # Paare, deren Verbindungen geladen wurden
    offers: list[Offer]  # nach Bewertung sortiert, beste zuerst
    notes: list[str] = field(default_factory=list)
    requests: int | None = None  # verbrauchte API-Abfragen (nur SerpApi)

    @property
    def best(self) -> Offer | None:
        return self.offers[0] if self.offers else None

    @property
    def cheapest(self) -> Offer | None:
        return min(self.offers, key=lambda o: (o.price, o.duration), default=None)

    @property
    def fastest(self) -> Offer | None:
        return min(self.offers, key=lambda o: (o.duration, o.price), default=None)


# --- Eingaben lesen ----------------------------------------------------------

def normalize_place(text: str) -> str:
    text = text.strip().lower()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, plain)
    return re.sub(r"[\s-]+", " ", text)


def parse_airports(text: str | list[str]) -> list[str]:
    """'FRA, München; London' -> ['FRA', 'MUC', 'LHR', 'LGW', 'STN', 'LTN', 'LCY']"""
    parts = re.split(r"[,;/+]", text) if isinstance(text, str) else text
    codes: list[str] = []
    for part in parts:
        name = normalize_place(part)
        if not name:
            continue
        if name in CITY_AIRPORTS:
            codes += CITY_AIRPORTS[name]
        elif re.fullmatch(r"[a-z]{3}", name):
            codes.append(name.upper())
        else:
            raise SearchError(
                f"Unbekannter Ort '{part.strip()}' – bitte den Flughafen-Code angeben (z. B. FRA)."
            )
    return list(dict.fromkeys(codes))


_DATE_RE = re.compile(
    r"(?P<iso>(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2}))"
    r"|(?P<de>(?P<dd>\d{1,2})\.(?P<mm>\d{1,2})(?:\.(?P<yy>\d{4}|\d{2})(?!\d))?\.?)"
)


def parse_date_range(text: str, today: date) -> tuple[date, date]:
    """Tag oder Zeitraum -> (erster Tag, letzter Tag).

    '1.12.2026', '1.12.-5.12.', '20.12. bis 5.1.', '2026-12-01..2026-12-05'.
    Ohne Jahr gilt der nächste passende Tag ab heute; ein Ende ohne Jahr, das vor
    dem Anfang läge, gehört ins Folgejahr.
    """
    days: list[date] = []
    for m in _DATE_RE.finditer(text):
        try:
            if m["iso"]:
                day = date(int(m["y"]), int(m["m"]), int(m["d"]))
            elif m["yy"]:
                year = int(m["yy"])
                day = date(year + 2000 if year < 100 else year, int(m["mm"]), int(m["dd"]))
            else:
                ref = days[-1] if days else today
                day = date(ref.year, int(m["mm"]), int(m["dd"]))
                if day < ref:
                    day = date(ref.year + 1, int(m["mm"]), int(m["dd"]))
        except ValueError:
            raise SearchError(f"Ungültiges Datum: {m.group(0).rstrip('.')}") from None
        days.append(day)
    if not days or len(days) > 2:
        raise SearchError(
            f"Datum nicht verstanden: '{text}'. Beispiele: 1.12.2026 oder 1.12.-5.12. oder 2026-12-01..2026-12-05"
        )
    if days[-1] < days[0]:
        raise SearchError(f"Der Zeitraum '{text}' endet vor seinem Beginn.")
    return days[0], days[-1]


def parse_nights(text: str) -> tuple[int | None, int | None]:
    """'7-14' -> (7, 14), '10' -> (10, 10)"""
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if not 1 <= len(numbers) <= 2:
        raise SearchError(f"Nächte nicht verstanden: '{text}' (Beispiel: 7-14).")
    return numbers[0], numbers[-1]


def parse_cabin(text: str) -> str:
    name = re.sub(r"[\s_-]+", "", text.strip().lower())
    name = _CABIN_ALIASES.get(name, name)
    if name not in CABINS:
        raise SearchError(f"Unbekannte Klasse '{text}' (economy, premium, business, first).")
    return name


# --- Suche -------------------------------------------------------------------

def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def date_pairs(request: SearchRequest) -> list[DatePair]:
    """Alle Kombinationen aus Hin- und Rückflugtag, die zu den Angaben passen."""
    outbound_days = _days(request.depart_from, request.depart_to)
    if not request.round_trip:
        return [(day, None) for day in outbound_days]
    pairs = []
    for out in outbound_days:
        for back in _days(request.return_from, request.return_to):
            nights = (back - out).days
            if nights < 0:
                continue
            if request.min_nights is not None and nights < request.min_nights:
                continue
            if request.max_nights is not None and nights > request.max_nights:
                continue
            pairs.append((out, back))
    return pairs


def rank_offers(offers: list[Offer], request: SearchRequest) -> list[Offer]:
    """Filtert, bewertet und sortiert (beste zuerst).

    Dieselben Flüge an anderen Tagen erscheinen nur einmal – mit dem besten
    Termin. Wie der Preis über die Tage schwankt, zeigt der Preiskalender.
    """
    value = request.value_per_hour
    limit = request.max_hours * 60 if request.max_hours else None
    best: dict[tuple[str, ...], Offer] = {}
    for offer in offers:
        if offer.price <= 0:
            continue
        journeys = offer.journeys
        if request.max_stops is not None and any(j.stops > request.max_stops for j in journeys):
            continue
        if limit and any(j.duration > limit for j in journeys):
            continue
        offer.score = offer.price + value * offer.duration / 60
        known = best.get(offer.flights())
        if known is None or offer.score < known.score:
            best[offer.flights()] = offer
    return sorted(best.values(), key=lambda o: (o.score, o.price, o.duration))


def run_search(
    request: SearchRequest,
    source: Source,
    *,
    details: int = 5,
    max_pairs: int = 100,
    progress: Progress | None = None,
    today: date | None = None,
) -> SearchResult:
    """Führt die Suche aus (Ablauf siehe Modulbeschreibung)."""
    say = progress or (lambda message: None)
    notes = request.validate(today or date.today())
    pairs = date_pairs(request)
    if not pairs:
        raise SearchError(
            "Keine passende Kombination aus Hin- und Rückflugtag – Zeiträume und Nächte prüfen."
        )
    if len(pairs) > max_pairs:
        raise SearchError(
            f"{len(pairs)} Kombinationen aus Hin- und Rückflugtag sind zu viele (Grenze {max_pairs}). "
            "Zeiträume verkürzen, die Nächte am Ziel eingrenzen oder die Grenze erhöhen (--max-kombinationen)."
        )

    say(f"Prüfe {len(pairs)} Datumskombination(en) bei {source.name} …")
    calendar = source.price_calendar(request, pairs, say)

    # Nur für die günstigsten Paare die Verbindungen laden: Die Flugzeiten sind
    # an allen Tagen ähnlich, der Preis schwankt dagegen stark.
    detailed = sorted(calendar, key=lambda p: (calendar[p], p[0], p[1] or p[0]))[: max(details, 1)]
    offers: list[Offer] = []
    errors: list[SearchError] = []
    for number, pair in enumerate(detailed, 1):
        say(f"Lade Verbindungen {number}/{len(detailed)} …")
        try:
            offers += source.offers(request, pair)
        except SearchError as error:
            errors.append(error)
    if errors and not offers:
        raise errors[0]
    if errors:
        notes.append(f"{len(errors)} von {len(detailed)} Detailabfragen fehlgeschlagen ({errors[0]}).")

    ranked = rank_offers(offers, request)
    priceless = sum(1 for offer in offers if offer.price <= 0)
    if priceless:
        notes.append(f"{priceless} Verbindung(en) ohne Preisangabe der Quelle ausgeblendet.")
    if offers and not ranked and not priceless:
        notes.append("Keine Verbindung erfüllt die Vorgaben zu Umstiegen bzw. Reisezeit.")
    return SearchResult(
        request=request,
        source=source.name,
        pairs=pairs,
        calendar=calendar,
        detailed=detailed,
        offers=ranked,
        notes=notes + list(getattr(source, "notes", [])),
        requests=getattr(source, "requests_made", None),
    )
