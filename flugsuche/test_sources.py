from datetime import date, datetime, timedelta

import pytest

import sources
from search import SearchError, SearchRequest, date_pairs
from sources import SerpApiSource, _runs_by_nights, stops_code

# Relativ zu heute: fli lehnt Reisedaten in der Vergangenheit ab.
OUT = date.today() + timedelta(days=40)
BACK = OUT + timedelta(days=19)


def day(offset: int, base: date = OUT) -> date:
    return base + timedelta(days=offset)


def at(base: date, offset: int, clock: str) -> str:
    """SerpApi-Zeitangabe, z. B. '2026-12-01 21:35'."""
    return f"{day(offset, base).isoformat()} {clock}"


def dt(base: date, offset: int, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day(offset, base), datetime.min.time()).replace(hour=hour, minute=minute)


def request(**changes):
    values = dict(
        origins=["FRA", "MUC"],
        destinations=["BKK"],
        depart_from=OUT,
        depart_to=OUT,
        return_from=BACK,
        return_to=BACK,
        cabin="business",
        adults=2,
    )
    values.update(changes)
    return SearchRequest(**values)


def test_stops_code():
    assert [stops_code(n) for n in (None, 0, 1, 2, 5)] == [0, 1, 2, 3, 0]


def test_runs_by_nights_groups_consecutive_days_with_same_stay():
    r = request(depart_to=day(2), return_to=day(1, BACK))
    runs = _runs_by_nights(date_pairs(r))
    by_nights = {(run[0][1] - run[0][0]).days: run for run in runs}
    assert by_nights == {
        17: [(day(2), BACK)],
        18: [(day(1), BACK), (day(2), day(1, BACK))],
        19: [(OUT, BACK), (day(1), day(1, BACK))],
        20: [(OUT, day(1, BACK))],
    }
    # Lücke bei den Hinflugtagen -> getrennte Gruppen
    gap = [(OUT, day(7)), (day(2), day(9))]
    assert _runs_by_nights(gap) == [[gap[0]], [gap[1]]]


# --- SerpApi -------------------------------------------------------------------

def flight(origin, destination, departure, arrival, minutes, airline, number):
    return {
        "departure_airport": {"name": origin, "id": origin, "time": departure},
        "arrival_airport": {"name": destination, "id": destination, "time": arrival},
        "duration": minutes,
        "airline": airline,
        "travel_class": "Business",
        "flight_number": number,
    }


OUTBOUND = {
    "search_metadata": {"status": "Success", "google_flights_url": "https://www.google.com/travel/flights?hl=de&tfs=out"},
    "best_flights": [
        {
            "flights": [flight("FRA", "BKK", at(OUT, 0, "21:35"), at(OUT, 1, "13:25"), 650, "Thai Airways", "TG 921")],
            "total_duration": 650,
            "price": 4380,
            "type": "Round trip",
            "departure_token": "tok-tg",
        }
    ],
    "other_flights": [
        {
            "flights": [
                flight("MUC", "IST", at(OUT, 0, "13:05"), at(OUT, 0, "17:00"), 175, "Turkish Airlines", "TK 1630"),
                flight("IST", "BKK", at(OUT, 0, "19:00"), at(OUT, 1, "08:45"), 585, "Turkish Airlines", "TK 68"),
            ],
            "layovers": [{"duration": 120, "name": "Istanbul Airport", "id": "IST"}],
            "total_duration": 880,
            "price": 3300,
            "departure_token": "tok-tk",
        },
        {
            "flights": [flight("FRA", "BKK", at(OUT, 0, "06:00"), at(OUT, 1, "19:00"), 1500, "Langsam Air", "LA 1")],
            "total_duration": 1500,
            "price": 3200,
            "departure_token": "tok-slow",
        },
        {"flights": [], "price": 100},  # unvollständig -> ignoriert
    ],
}

RETURNS = {
    "tok-tg": {
        "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?tfs=tg"},
        "best_flights": [
            {"flights": [flight("BKK", "FRA", at(BACK, 0, "00:05"), at(BACK, 0, "06:50"), 765, "Thai Airways", "TG 920")],
             "total_duration": 765, "price": 4380},
        ],
        "other_flights": [
            {"flights": [flight("BKK", "MUC", at(BACK, 0, "23:55"), at(BACK, 1, "06:20"), 745, "Thai Airways", "TG 924")],
             "total_duration": 745, "price": 4520},
        ],
    },
    "tok-tk": {
        "best_flights": [
            {"flights": [
                flight("BKK", "IST", at(BACK, 0, "10:00"), at(BACK, 0, "16:30"), 630, "Turkish Airlines", "TK 69"),
                flight("IST", "MUC", at(BACK, 0, "18:30"), at(BACK, 0, "20:45"), 195, "Turkish Airlines", "TK 1629"),
            ], "total_duration": 945, "price": 3300},
        ],
    },
}


class FakeSerpApi:
    def __init__(self, outbound=OUTBOUND, returns=RETURNS):
        self.outbound = outbound
        self.returns = returns
        self.calls = []

    def __call__(self, params):
        self.calls.append(params)
        token = params.get("departure_token")
        return self.returns[token] if token else self.outbound


def test_serpapi_needs_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with pytest.raises(SearchError, match="SERPAPI_KEY"):
        SerpApiSource()


def test_serpapi_round_trip_calendar_and_offers():
    fetch = FakeSerpApi()
    source = SerpApiSource(api_key="geheim", fetch=fetch)
    r = request()
    pair = (OUT, BACK)

    assert source.price_calendar(r, [pair], lambda message: None) == {pair: 3200.0}
    params = fetch.calls[0]
    assert params == {
        "engine": "google_flights",
        "departure_id": "FRA,MUC",
        "arrival_id": "BKK",
        "outbound_date": OUT.isoformat(),
        "return_date": BACK.isoformat(),
        "type": "1",
        "travel_class": "3",
        "adults": "2",
        "stops": "0",
        "currency": "EUR",
        "hl": "de",
        "gl": "de",
        "api_key": "geheim",
    }

    offers = source.offers(r, pair)
    # Kombiniert werden die 2 Hinflüge mit der besten Schätzung (TK, TG), nicht der langsame
    assert [call.get("departure_token") for call in fetch.calls[1:]] == ["tok-tk", "tok-tg"]
    assert source.requests_made == 3
    assert sorted(o.price for o in offers) == [3300.0, 4380.0, 4520.0]  # teurerer Rückflug verteuert die Kombination

    tk = next(o for o in offers if o.price == 3300)
    assert tk.outbound.stops == 1 and tk.outbound.via == ["IST"] and tk.outbound.duration == 880
    assert tk.outbound.departure == dt(OUT, 0, 13, 5)
    assert tk.inbound.destination == "MUC" and tk.inbound.duration == 945
    assert tk.link == "https://www.google.com/travel/flights?hl=de&tfs=out"  # ohne eigenen Link -> Link der Suche
    tg = next(o for o in offers if o.price == 4380)
    assert tg.outbound.legs[0].flight_number == "TG 921" and tg.link.endswith("tfs=tg")


def test_serpapi_respects_max_hours_when_choosing_outbounds():
    fetch = FakeSerpApi()
    source = SerpApiSource(api_key="k", expand=5, fetch=fetch)
    r = request(max_hours=12)
    pair = (OUT, BACK)
    source.price_calendar(r, [pair], lambda message: None)
    source.offers(r, pair)
    assert [call.get("departure_token") for call in fetch.calls[1:]] == ["tok-tg"]  # nur unter 12 Std.


def test_serpapi_one_way_uses_first_answer_and_handles_empty_days():
    no_flights = {"error": "Google Flights hasn't returned any results for this query."}

    def fetch(params):
        return no_flights if params["outbound_date"] == day(1).isoformat() else OUTBOUND

    source = SerpApiSource(api_key="k", fetch=fetch)
    r = request(depart_to=day(1), return_from=None, return_to=None)
    pairs = date_pairs(r)
    calendar = source.price_calendar(r, pairs, lambda message: None)
    assert calendar == {(OUT, None): 3200.0}
    offers = source.offers(r, (OUT, None))
    assert len(offers) == 3 and all(o.inbound is None for o in offers)
    assert source.requests_made == 2  # Details ohne weitere Abfrage


def test_serpapi_errors_are_reported():
    source = SerpApiSource(api_key="k", fetch=lambda params: {"error": "Invalid API key."})
    r = request()
    with pytest.raises(SearchError, match="Invalid API key"):
        source.price_calendar(r, date_pairs(r), lambda message: None)


# --- Google Flights (fli) --------------------------------------------------------

fli = pytest.importorskip("fli")


def fli_result(price, legs):
    from fli.models import Airline, Airport, FlightLeg, FlightResult

    built = [
        FlightLeg(
            airline=getattr(Airline, airline),
            flight_number=number,
            departure_airport=getattr(Airport, origin),
            arrival_airport=getattr(Airport, destination),
            departure_datetime=departure,
            arrival_datetime=arrival,
            duration=int((arrival - departure).total_seconds() // 60),
        )
        for airline, number, origin, destination, departure, arrival in legs
    ]
    total = int((built[-1].arrival_datetime - built[0].departure_datetime).total_seconds() // 60)
    return FlightResult(legs=built, price=price, currency="EUR", duration=total, stops=len(built) - 1)


def test_google_round_trip_calendar_builds_date_search(monkeypatch):
    from fli.models import MaxStops, SeatType, TripType
    from fli.search import SearchDates
    from fli.search.dates import DatePrice

    seen = []

    def fake_search(self, filters, currency=None, language=None, country=None):
        seen.append((filters, currency, language, country))
        start = filters.parsed_from_date
        nights = (filters.flight_segments[1].parsed_travel_date - filters.flight_segments[0].parsed_travel_date).days
        return [
            DatePrice(date=(start, start + timedelta(days=nights)), price=4000 + nights, currency="EUR"),
            DatePrice(date=(start + timedelta(days=3), start + timedelta(days=4)), price=1, currency="EUR"),  # nicht gefragt
        ]

    monkeypatch.setattr(SearchDates, "search", fake_search)
    source = sources.GoogleFlightsSource()
    r = request(return_to=day(1, BACK), max_stops=0)
    calendar = source.price_calendar(r, date_pairs(r), lambda message: None)
    assert calendar == {(OUT, BACK): 4019.0, (OUT, day(1, BACK)): 4020.0}

    filters, currency, language, country = seen[0]
    assert (currency, language, country) == ("EUR", "de", "DE")
    assert filters.trip_type == TripType.ROUND_TRIP and filters.seat_type == SeatType.BUSINESS
    assert filters.stops == MaxStops.NON_STOP and filters.passenger_info.adults == 2
    outbound, back = filters.flight_segments
    assert [a[0].name for a in outbound.departure_airport] == ["FRA", "MUC"]
    assert [a[0].name for a in back.arrival_airport] == ["FRA", "MUC"]
    assert outbound.travel_date == OUT.isoformat()


def test_google_offers_convert_results(monkeypatch):
    from fli.search import SearchFlights

    out = fli_result(4380, [("LH", "772", "FRA", "BKK", dt(OUT, 0, 22), dt(OUT, 1, 14, 30))])
    back_a = fli_result(4380, [("LH", "773", "BKK", "FRA", dt(BACK, 0, 23), dt(BACK, 1, 6, 30))])
    back_b = fli_result(4710, [
        ("TG", "920", "BKK", "IST", dt(BACK, 0, 9), dt(BACK, 0, 15)),
        ("TK", "1590", "IST", "MUC", dt(BACK, 0, 17), dt(BACK, 0, 19)),
    ])
    seen = {}

    def fake_search(self, filters, top_n=5, currency=None, language=None, country=None):
        seen["filters"], seen["top_n"] = filters, top_n
        return [(out, back_a), (out, back_b)]

    monkeypatch.setattr(SearchFlights, "search", fake_search)
    source = sources.GoogleFlightsSource(expand=4)
    offers = source.offers(request(), (OUT, BACK))

    assert seen["top_n"] == 4 and len(seen["filters"].flight_segments) == 2
    first, second = offers
    assert first.price == 4380 and second.price == 4710  # der teurere Rückflug zählt
    leg = first.outbound.legs[0]
    assert (leg.airline, leg.flight_number, leg.origin, leg.destination) == ("Lufthansa", "LH 772", "FRA", "BKK")
    assert first.outbound.duration == 990 and first.inbound.stops == 0
    assert second.inbound.via == ["IST"] and second.inbound.airlines == ["Thai Airways", "Turkish Airlines"]
    assert first.link.startswith("https://www.google.com/travel/flights/booking?tfs=")


def test_google_one_way_reuses_day_search(monkeypatch):
    from fli.search import SearchFlights

    calls = []
    result = fli_result(199, [("FR", "1234", "FRA", "BKK", dt(OUT, 0, 6), dt(OUT, 0, 18))])

    def fake_search(self, filters, top_n=5, currency=None, language=None, country=None):
        calls.append(filters.flight_segments[0].travel_date)
        return [result]

    monkeypatch.setattr(SearchFlights, "search", fake_search)
    source = sources.GoogleFlightsSource()
    r = request(return_from=None, return_to=None)
    pair = (OUT, None)
    assert source.price_calendar(r, [pair], lambda message: None) == {pair: 199.0}
    assert source.offers(r, pair)[0].outbound.legs[0].flight_number == "FR 1234"
    assert calls == [OUT.isoformat()]  # Details kamen schon mit der Preisabfrage


def test_google_rejects_unknown_airport_and_explains_errors(monkeypatch):
    from fli.search import SearchDates
    from fli.search.exceptions import SearchHTTPError

    source = sources.GoogleFlightsSource()
    r = request(destinations=["QQQ"])
    with pytest.raises(SearchError, match="QQQ"):
        source.price_calendar(r, date_pairs(r), lambda message: None)

    def blocked(self, filters, **kwargs):
        raise SearchHTTPError("Too Many Requests", status_code=429)

    monkeypatch.setattr(SearchDates, "search", blocked)
    r = request()
    with pytest.raises(SearchError, match="429"):
        source.price_calendar(r, date_pairs(r), lambda message: None)
