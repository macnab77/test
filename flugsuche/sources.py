"""Datenquellen: liefern Preise und Verbindungen für search.py.

GoogleFlightsSource  kostenlos, ohne Anmeldung. Liest über die Bibliothek fli
                     (github.com/punitarani/fli) die öffentliche Google-Flights-
                     Seite. Inoffiziell: Ändert Google die Seite, kann das
                     zeitweise ausfallen – dann fli aktualisieren oder SerpApi
                     nutzen.
SerpApiSource        offizielle API mit denselben Google-Flights-Daten
                     (serpapi.com). Braucht einen API-Schlüssel; jede Abfrage
                     zählt gegen das Kontingent (kleines Gratis-Kontingent).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from search import DatePair, Journey, Leg, Offer, Progress, SearchError, SearchRequest

# Klassen und Umstiege sind bei Google Flights und SerpApi gleich nummeriert.
CABIN_CODES = {"economy": 1, "premium": 2, "business": 3, "first": 4}
LANGUAGE, COUNTRY = "de", "DE"

# fli-Airline-Namen, die für Passagiere irreführend sind
AIRLINE_NAMES = {"LH": "Lufthansa"}


def stops_code(max_stops: int | None) -> int:
    """0 = egal, 1 = nur direkt, 2 = höchstens 1 Umstieg, 3 = höchstens 2 Umstiege"""
    if max_stops is None or max_stops > 2:
        return 0
    return max_stops + 1


def _parallel(
    jobs: list[list[DatePair]],
    work: Callable[[list[DatePair]], dict[DatePair, float]],
    progress: Progress,
    total: int,
    workers: int,
) -> tuple[dict[DatePair, float], list[Exception]]:
    """Führt work(job) parallel aus und meldet den Fortschritt je Datumspaar."""
    merged: dict[DatePair, float] = {}
    errors: list[Exception] = []
    done = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(work, job): job for job in jobs}
        for future in as_completed(futures):
            try:
                merged.update(future.result())
            except Exception as error:  # einzelne Tage dürfen scheitern
                errors.append(error)
            done += len(futures[future])
            progress(f"Preise: {done}/{total} Datumskombinationen")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # bei Strg+C nicht weiter warten
    return merged, errors


def _runs_by_nights(pairs: list[DatePair]) -> list[list[DatePair]]:
    """Gruppiert Paare mit gleicher Nächtezahl und aufeinanderfolgenden Hinflugtagen.

    Eine solche Gruppe ist für fli eine einzige Datumssuche.
    """
    by_nights: dict[int, list[DatePair]] = {}
    for out, back in sorted(pairs):
        by_nights.setdefault((back - out).days, []).append((out, back))
    runs = []
    for group in by_nights.values():
        run = [group[0]]
        for pair in group[1:]:
            if (pair[0] - run[-1][0]).days == 1 and len(run) < 60:
                run.append(pair)
            else:
                runs.append(run)
                run = [pair]
        runs.append(run)
    return runs


def _failure_note(errors: list[Exception], total: int, explain: Callable[[Exception], SearchError]) -> str:
    return f"{len(errors)} von {total} Preisabfragen fehlgeschlagen ({explain(errors[0])})."


# --- Google Flights (fli) ----------------------------------------------------

class GoogleFlightsSource:
    """Google Flights über fli. Kostenlos, braucht keinen Schlüssel."""

    name = "Google Flights"
    default_details = 5

    def __init__(self, expand: int = 5, workers: int = 3):
        try:
            import fli  # noqa: F401
        except ImportError:
            raise SearchError(
                "Die Bibliothek fli fehlt – im Ordner flugsuche 'pip install -r requirements.txt' ausführen."
            ) from None
        self.expand = expand  # so viele Hinflüge je Datumspaar mit Rückflügen kombinieren
        self.workers = workers
        self.notes: list[str] = []
        self._one_way: dict[DatePair, list[Offer]] = {}

    def price_calendar(
        self, request: SearchRequest, pairs: list[DatePair], progress: Progress
    ) -> dict[DatePair, float]:
        self._airports(request.origins + request.destinations)  # unbekannte Codes sofort melden
        if request.round_trip:
            jobs = _runs_by_nights(pairs)
            calendar, errors = _parallel(
                jobs, lambda run: self._sweep(request, run), progress, len(pairs), self.workers
            )
        else:
            # Ein Tag kostet so oder so einen Seitenabruf – dann gleich mit allen Details.
            jobs = [[pair] for pair in pairs]
            calendar, errors = _parallel(
                jobs, lambda run: self._one_way_day(request, run[0]), progress, len(pairs), self.workers
            )
        if errors and not calendar:
            raise _explain_fli(errors[0])
        if errors:
            self.notes.append(_failure_note(errors, len(jobs), _explain_fli))
        return calendar

    def offers(self, request: SearchRequest, pair: DatePair) -> list[Offer]:
        if pair in self._one_way:
            return self._one_way[pair]
        try:
            return self._search(request, pair)
        except Exception as error:
            raise _explain_fli(error) from error

    def _sweep(self, request: SearchRequest, run: list[DatePair]) -> dict[DatePair, float]:
        """Preise für Paare mit gleicher Nächtezahl und aufeinanderfolgenden Hinflugtagen."""
        from fli.models import DateSearchFilters, PassengerInfo, TripType
        from fli.search import SearchDates

        first_out, first_back = run[0]
        # Ohne duration nimmt fli den Abstand der beiden Flugtage – so geht auch
        # der Rückflug am selben Tag (0 Nächte).
        filters = DateSearchFilters(
            trip_type=TripType.ROUND_TRIP,
            passenger_info=PassengerInfo(adults=request.adults),
            flight_segments=self._segments(request, first_out, first_back),
            from_date=first_out.isoformat(),
            to_date=run[-1][0].isoformat(),
            **self._filters(request),
        )
        nights = (first_back - first_out).days
        wanted = set(run)
        prices = {}
        found = SearchDates().search(filters, currency=request.currency, language=LANGUAGE, country=COUNTRY)
        for item in found or []:
            out = item.date[0].date()
            pair = (out, out + timedelta(days=nights))
            if pair in wanted:
                prices[pair] = float(item.price)
        return prices

    def _one_way_day(self, request: SearchRequest, pair: DatePair) -> dict[DatePair, float]:
        offers = self._search(request, pair)
        self._one_way[pair] = offers
        prices = [offer.price for offer in offers if offer.price > 0]
        return {pair: min(prices)} if prices else {}

    def _search(self, request: SearchRequest, pair: DatePair) -> list[Offer]:
        """Verbindungen für ein Datumspaar (bei Hin und Rück: 1 + expand Seitenabrufe)."""
        from fli.models import FlightSearchFilters, PassengerInfo, SortBy, TripType
        from fli.search import SearchFlights

        out, back = pair
        passengers = PassengerInfo(adults=request.adults)
        filters = FlightSearchFilters(
            trip_type=TripType.ROUND_TRIP if back else TripType.ONE_WAY,
            passenger_info=passengers,
            flight_segments=self._segments(request, out, back),
            # Googles Mischung aus Preis und Dauer entscheidet, welche Hinflüge
            # mit Rückflügen kombiniert werden.
            sort_by=SortBy.BEST,
            **self._filters(request),
        )
        client = SearchFlights()  # nicht threadsicher, daher je Abfrage neu
        found = client.search(
            filters, top_n=self.expand, currency=request.currency, language=LANGUAGE, country=COUNTRY
        )
        offers = []
        for item in found or []:
            parts = item if isinstance(item, tuple) else (item,)
            # Bei Hin und Rück nennt Google an beiden Teilen einen Gesamtpreis; ein
            # teurerer Rückflug verteuert die Kombination, daher zählt der höhere.
            prices = [part.price for part in parts if part.price]
            link = client.build_flight_booking_url(
                item,
                currency=request.currency,
                language=LANGUAGE,
                country=COUNTRY,
                seat_type=filters.seat_type,
                passenger_info=passengers,
            )
            offers.append(
                Offer(
                    price=max(prices, default=0.0),
                    currency=parts[0].currency or request.currency,
                    outbound=_fli_journey(parts[0]),
                    inbound=_fli_journey(parts[1]) if len(parts) > 1 else None,
                    link=link,
                )
            )
        return offers

    def _filters(self, request: SearchRequest) -> dict:
        from fli.models import MaxStops, SeatType

        filters = {
            "stops": MaxStops(stops_code(request.max_stops)),
            "seat_type": SeatType(CABIN_CODES[request.cabin]),
        }
        if request.max_hours:
            filters["max_duration"] = int(request.max_hours * 60)  # je Richtung, in Minuten
        return filters

    def _segments(self, request: SearchRequest, out: date, back: date | None) -> list:
        from fli.models import FlightSegment

        origins = [[airport, 0] for airport in self._airports(request.origins)]
        targets = [[airport, 0] for airport in self._airports(request.destinations)]
        segments = [
            FlightSegment(departure_airport=origins, arrival_airport=targets, travel_date=out.isoformat())
        ]
        if back:
            segments.append(
                FlightSegment(departure_airport=targets, arrival_airport=origins, travel_date=back.isoformat())
            )
        return segments

    @staticmethod
    def _airports(codes: list[str]) -> list:
        from fli.core import resolve_airport

        airports = []
        for code in codes:
            try:
                airports.append(resolve_airport(code))
            except Exception:
                raise SearchError(f"Flughafen-Code '{code}' ist bei Google Flights (fli) unbekannt.") from None
        return airports


def _code(member) -> str:
    """IATA-Code eines fli-Enums (Codes, die mit einer Ziffer beginnen, tragen dort ein '_')."""
    return member.name.removeprefix("_")


def _fli_journey(result) -> Journey:
    from fli.models import display_name

    legs = []
    for leg in result.legs:
        code = _code(leg.airline)
        number = str(leg.flight_number)
        legs.append(
            Leg(
                airline=AIRLINE_NAMES.get(code) or display_name(leg.airline),
                flight_number=number if number.upper().startswith(code) else f"{code} {number}",
                origin=_code(leg.departure_airport),
                destination=_code(leg.arrival_airport),
                departure=leg.departure_datetime,
                arrival=leg.arrival_datetime,
                duration=leg.duration,
            )
        )
    return Journey(legs=legs, duration=result.duration)


def _explain_fli(error: Exception) -> SearchError:
    """Übersetzt Fehler aus fli in verständliche Hinweise."""
    if isinstance(error, SearchError):
        return error
    from fli.search.exceptions import (
        SearchConnectionError,
        SearchHTTPError,
        SearchParseError,
        SearchRejectedError,
        SearchTimeoutError,
    )

    if isinstance(error, SearchHTTPError) and error.status_code == 429:
        return SearchError(
            "Google Flights bremst gerade zu viele Anfragen (HTTP 429) – ein paar Minuten warten "
            "oder weniger Tage auf einmal suchen."
        )
    if isinstance(error, (SearchConnectionError, SearchTimeoutError)):
        return SearchError(f"Google Flights ist nicht erreichbar ({error}). Internetverbindung prüfen.")
    if isinstance(error, (SearchParseError, SearchRejectedError)):
        return SearchError(
            "Google Flights hat keine lesbaren Daten geliefert – vermutlich hat Google die Seite "
            "geändert. fli aktualisieren (siehe README) oder --quelle serpapi verwenden."
        )
    return SearchError(f"Google Flights: {error}")


# --- SerpApi -----------------------------------------------------------------

class SerpApiSource:
    """Google-Flights-Daten über die SerpApi (serpapi.com/google-flights-api)."""

    name = "SerpApi (Google Flights)"
    default_details = 3
    URL = "https://serpapi.com/search.json"

    def __init__(
        self,
        api_key: str | None = None,
        expand: int = 2,
        workers: int = 4,
        fetch: Callable[[dict], dict] | None = None,
    ):
        self.api_key = api_key or os.environ.get("SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY")
        if not self.api_key:
            raise SearchError(
                "Für die Quelle SerpApi fehlt der API-Schlüssel: auf serpapi.com registrieren "
                "und SERPAPI_KEY setzen (siehe README)."
            )
        self.expand = expand  # so viele Hinflüge je Datumspaar mit Rückflügen kombinieren (je 1 Abfrage)
        self.workers = workers
        self.notes: list[str] = []
        self.requests_made = 0
        self._fetch = fetch or self._http_get
        self._lock = threading.Lock()
        self._outbound: dict[DatePair, dict] = {}

    def price_calendar(
        self, request: SearchRequest, pairs: list[DatePair], progress: Progress
    ) -> dict[DatePair, float]:
        def work(run: list[DatePair]) -> dict[DatePair, float]:
            pair = run[0]
            data = self._outbound[pair] = self._get(self._params(request, pair))
            prices = [item["price"] for item in _serp_items(data) if item.get("price")]
            return {pair: float(min(prices))} if prices else {}

        calendar, errors = _parallel([[pair] for pair in pairs], work, progress, len(pairs), self.workers)
        if errors and not calendar:
            raise _as_search_error(errors[0])
        if errors:
            self.notes.append(_failure_note(errors, len(pairs), _as_search_error))
        return calendar

    def offers(self, request: SearchRequest, pair: DatePair) -> list[Offer]:
        data = self._outbound.get(pair)
        if data is None:
            data = self._outbound[pair] = self._get(self._params(request, pair))
        link = _serp_link(data)
        items = [item for item in _serp_items(data) if item.get("price")]
        if pair[1] is None:
            return [
                Offer(float(item["price"]), request.currency, _serp_journey(item), link=link) for item in items
            ]

        # Rückflüge liefert SerpApi erst nach Wahl eines Hinflugs (eine Abfrage je
        # Hinflug), daher nur die vielversprechendsten kombinieren. Für die
        # Auswahl wird angenommen, dass der Rückflug so lange dauert wie der Hinflug.
        value = request.value_per_hour
        limit = request.max_hours * 60 if request.max_hours else None
        candidates = sorted(
            (
                item
                for item in items
                if item.get("departure_token") and not (limit and item.get("total_duration", 0) > limit)
            ),
            key=lambda item: item["price"] + value * 2 * item.get("total_duration", 0) / 60,
        )
        offers = []
        for item in candidates[: self.expand]:
            outbound = _serp_journey(item)
            returns = self._get(self._params(request, pair, departure_token=item["departure_token"]))
            for back in _serp_items(returns):
                prices = [p for p in (item["price"], back.get("price")) if p]
                offers.append(
                    Offer(
                        price=float(max(prices)),
                        currency=request.currency,
                        outbound=outbound,
                        inbound=_serp_journey(back),
                        link=_serp_link(returns) or link,
                    )
                )
        return offers

    def _params(self, request: SearchRequest, pair: DatePair, **extra: str) -> dict:
        out, back = pair
        params = {
            "engine": "google_flights",
            "departure_id": ",".join(request.origins),
            "arrival_id": ",".join(request.destinations),
            "outbound_date": out.isoformat(),
            "type": "1" if back else "2",  # 1 = Hin und Rück, 2 = nur Hinflug
            "travel_class": str(CABIN_CODES[request.cabin]),
            "adults": str(request.adults),
            "stops": str(stops_code(request.max_stops)),
            "currency": request.currency,
            "hl": LANGUAGE,
            "gl": COUNTRY.lower(),
        }
        if back:
            params["return_date"] = back.isoformat()
        params.update(extra)
        return params

    def _get(self, params: dict) -> dict:
        data = self._fetch({**params, "api_key": self.api_key})
        with self._lock:
            self.requests_made += 1
        error = data.get("error")
        if error:
            if "returned any results" in error:  # an diesem Tag keine Flüge
                return {}
            raise SearchError(f"SerpApi: {error}")
        return data

    def _http_get(self, params: dict) -> dict:
        import httpx

        try:
            response = httpx.get(self.URL, params=params, timeout=90)
        except httpx.HTTPError as error:
            raise SearchError(f"SerpApi ist nicht erreichbar ({error}).") from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 401:
            raise SearchError("SerpApi lehnt den API-Schlüssel ab – SERPAPI_KEY prüfen.")
        if response.status_code == 429:
            raise SearchError("SerpApi: Kontingent aufgebraucht oder zu viele Abfragen auf einmal.")
        if response.status_code >= 400 and not data.get("error"):
            raise SearchError(f"SerpApi antwortet mit HTTP {response.status_code}.")
        return data


def _as_search_error(error: Exception) -> SearchError:
    return error if isinstance(error, SearchError) else SearchError(f"SerpApi: {error}")


def _serp_items(data: dict) -> list[dict]:
    return [
        item
        for key in ("best_flights", "other_flights")
        for item in data.get(key) or []
        if item.get("flights")
    ]


def _serp_link(data: dict) -> str:
    return (data.get("search_metadata") or {}).get("google_flights_url", "")


def _serp_time(text: str) -> datetime:
    return datetime.strptime(text.strip()[:16], "%Y-%m-%d %H:%M")


def _serp_journey(item: dict) -> Journey:
    legs = [
        Leg(
            airline=flight.get("airline", ""),
            flight_number=flight.get("flight_number", ""),
            origin=flight["departure_airport"]["id"],
            destination=flight["arrival_airport"]["id"],
            departure=_serp_time(flight["departure_airport"]["time"]),
            arrival=_serp_time(flight["arrival_airport"]["time"]),
            duration=int(flight.get("duration", 0)),
        )
        for flight in item["flights"]
    ]
    duration = item.get("total_duration") or sum(leg.duration for leg in legs) + sum(
        layover.get("duration", 0) for layover in item.get("layovers", [])
    )
    return Journey(legs=legs, duration=int(duration))


SOURCES = {"google": GoogleFlightsSource, "serpapi": SerpApiSource}


def make_source(name: str) -> GoogleFlightsSource | SerpApiSource:
    if name not in SOURCES:
        raise SearchError(f"Unbekannte Quelle '{name}' (google oder serpapi).")
    return SOURCES[name]()
