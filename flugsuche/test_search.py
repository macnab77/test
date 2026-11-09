from datetime import date, datetime, timedelta

import pytest

from search import (
    Journey,
    Leg,
    Offer,
    SearchError,
    SearchRequest,
    date_pairs,
    parse_airports,
    parse_cabin,
    parse_date_range,
    parse_nights,
    rank_offers,
    run_search,
)

TODAY = date(2026, 10, 20)


def journey(origin, destination, day, hour, minutes, via=(), number="XX 1"):
    """Reise mit `minutes` Gesamtdauer; jede Station in `via` ist ein Umstieg."""
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
    stops = [origin, *via, destination]
    part = timedelta(minutes=minutes / (len(stops) - 1))
    legs = [
        Leg("Testair", f"{number}{i}", a, b, start + part * i, start + part * (i + 1), int(part.total_seconds() // 60))
        for i, (a, b) in enumerate(zip(stops, stops[1:]))
    ]
    return Journey(legs, minutes)


def offer(price, out_minutes=600, back_minutes=600, via=(), day=date(2026, 12, 1), number="XX 1"):
    return Offer(
        price,
        "EUR",
        journey("FRA", "BKK", day, 10, out_minutes, via, number),
        journey("BKK", "FRA", day + timedelta(days=14), 10, back_minutes, via, number + "R"),
    )


def request(**changes):
    values = dict(
        origins=["FRA", "MUC"],
        destinations=["BKK"],
        depart_from=date(2026, 12, 1),
        depart_to=date(2026, 12, 3),
        return_from=date(2026, 12, 20),
        return_to=date(2026, 12, 21),
    )
    values.update(changes)
    return SearchRequest(**values)


# --- Eingaben ------------------------------------------------------------------

def test_parse_airports_codes_cities_and_umlauts():
    assert parse_airports("fra, MUC") == ["FRA", "MUC"]
    assert parse_airports("München; London") == ["MUC", "LHR", "LGW", "STN", "LTN", "LCY"]
    assert parse_airports("Köln-Bonn / Düsseldorf") == ["CGN", "DUS"]
    assert parse_airports(["BKK", "bangkok"]) == ["BKK", "DMK"]  # ohne Doppelte
    with pytest.raises(SearchError, match="Atlantis"):
        parse_airports("Atlantis")


def test_parse_date_range_formats_and_year_guess():
    assert parse_date_range("1.12.2026", TODAY) == (date(2026, 12, 1), date(2026, 12, 1))
    assert parse_date_range("1.12.-5.12.", TODAY) == (date(2026, 12, 1), date(2026, 12, 5))
    assert parse_date_range("20.12. bis 5.1.", TODAY) == (date(2026, 12, 20), date(2027, 1, 5))
    assert parse_date_range("5.1.", TODAY) == (date(2027, 1, 5), date(2027, 1, 5))  # schon vorbei -> nächstes Jahr
    assert parse_date_range("2026-12-01..2026-12-05", TODAY) == (date(2026, 12, 1), date(2026, 12, 5))
    assert parse_date_range("2026-12-01-2026-12-05", TODAY) == (date(2026, 12, 1), date(2026, 12, 5))
    assert parse_date_range("01.12.26 - 3.12.26", TODAY) == (date(2026, 12, 1), date(2026, 12, 3))
    # Rückflug ohne Jahr: Bezug ist der Hinflug
    assert parse_date_range("3.1.", date(2026, 12, 28)) == (date(2027, 1, 3), date(2027, 1, 3))


@pytest.mark.parametrize("text", ["31.02.2027", "5.12.2026-1.12.2026", "nächste Woche", "1.1.-2.1.-3.1."])
def test_parse_date_range_rejects_nonsense(text):
    with pytest.raises(SearchError):
        parse_date_range(text, TODAY)


def test_parse_nights_and_cabin():
    assert parse_nights("7-14") == (7, 14)
    assert parse_nights("10") == (10, 10)
    assert parse_cabin("Business") == "business"
    assert parse_cabin("Premium Economy") == "premium"
    assert parse_cabin("erste Klasse") == "first"
    assert parse_cabin("eco") == "economy"
    with pytest.raises(SearchError):
        parse_cabin("Luxus")
    with pytest.raises(SearchError):
        parse_nights("lange")


# --- Datumspaare und Prüfung -------------------------------------------------------

def test_date_pairs_respect_ranges_and_nights():
    r = request(return_from=date(2026, 12, 2), return_to=date(2026, 12, 4), min_nights=1, max_nights=2)
    assert date_pairs(r) == [
        (date(2026, 12, 1), date(2026, 12, 2)),
        (date(2026, 12, 1), date(2026, 12, 3)),
        (date(2026, 12, 2), date(2026, 12, 3)),
        (date(2026, 12, 2), date(2026, 12, 4)),
        (date(2026, 12, 3), date(2026, 12, 4)),
    ]
    # Rückflug vor dem Hinflug fällt weg, am selben Tag ist erlaubt
    r = request(return_from=date(2026, 12, 1), return_to=date(2026, 12, 2))
    assert (date(2026, 12, 2), date(2026, 12, 1)) not in date_pairs(r)
    assert (date(2026, 12, 1), date(2026, 12, 1)) in date_pairs(r)
    assert date_pairs(request(return_from=None, return_to=None)) == [
        (date(2026, 12, 1), None), (date(2026, 12, 2), None), (date(2026, 12, 3), None)
    ]


def test_validate_trims_started_range_and_rejects_errors():
    r = request(depart_from=date(2026, 10, 18), depart_to=date(2026, 10, 25), return_to=None)
    notes = r.validate(TODAY)
    assert r.depart_from == TODAY and "ab heute" in notes[0]
    assert r.return_to == r.return_from  # nur ein Rückflugtag angegeben

    for bad in (
        request(depart_from=date(2026, 10, 1), depart_to=date(2026, 10, 5)),
        request(return_from=date(2026, 11, 1), return_to=date(2026, 11, 2)),
        request(destinations=["MUC"]),
        request(cabin="luxus"),
        request(adults=0),
        request(min_nights=10, max_nights=5),
    ):
        with pytest.raises(SearchError):
            bad.validate(TODAY)


# --- Bewertung -----------------------------------------------------------------

def test_rank_offers_weighs_price_against_travel_time():
    cheap_slow = offer(600, 960, 960, via=("IST",), number="TK 1")  # 32 Std. -> 600 + 25*32 = 1400
    direct = offer(750, 650, 650, number="TG 9")  # 21:40 Std. -> 750 + 25*21.67 = 1291.7
    ranked = rank_offers([cheap_slow, direct], request())
    assert ranked == [direct, cheap_slow]
    assert round(direct.score, 1) == 1291.7
    # Mit niedrigem Zeitwert gewinnt der günstigere Flug
    assert rank_offers([cheap_slow, direct], request(time_value=5))[0] is cheap_slow
    # In der Business-Klasse zählt die Zeit mehr (Standard 50 €/Std.)
    assert request(cabin="business").value_per_hour == 50


def test_rank_offers_filters_and_collapses_same_flights():
    nonstop = offer(900, 650, 650, number="LH 7")
    one_stop = offer(700, 900, 900, via=("DXB",), number="EK 4")
    two_stops = offer(500, 1200, 1200, via=("IST", "DEL"), number="AI 2")
    too_long = offer(400, 1500, 600, number="XY 3")
    priceless = offer(0, 600, 600, number="NO 1")
    later_day = offer(950, 650, 650, day=date(2026, 12, 2), number="LH 7")  # gleiche Flüge, teurer
    ranked = rank_offers([nonstop, one_stop, two_stops, too_long, priceless, later_day], request(max_stops=1, max_hours=20))
    assert ranked == [nonstop, one_stop]


# --- Ablauf --------------------------------------------------------------------

class FakeSource:
    name = "Testquelle"

    def __init__(self, prices, fail=()):
        self.prices = prices
        self.fail = set(fail)
        self.detail_calls = []
        self.notes = ["Hinweis der Quelle"]

    def price_calendar(self, request, pairs, progress):
        progress("Preise …")
        return {pair: self.prices[pair] for pair in pairs if pair in self.prices}

    def offers(self, request, pair):
        self.detail_calls.append(pair)
        if pair in self.fail:
            raise SearchError("Netzwerk weg")
        return [offer(self.prices[pair], day=pair[0], number=f"T{pair[0].day}-{pair[1].day} ")]


def test_run_search_loads_details_only_for_cheapest_dates():
    r = request()
    pairs = date_pairs(r)
    prices = {pair: 1000 - 10 * i for i, pair in enumerate(pairs)}
    source = FakeSource(prices)
    messages = []
    result = run_search(r, source, details=2, progress=messages.append, today=TODAY)
    assert result.pairs == pairs and len(pairs) == 6
    assert source.detail_calls == [pairs[-1], pairs[-2]]  # die zwei günstigsten
    assert [o.price for o in result.offers] == [950, 960]
    assert result.best.price == 950 and result.cheapest is result.best
    assert "Hinweis der Quelle" in result.notes
    assert messages[0].startswith("Prüfe 6 Datumskombination(en)")


def test_run_search_reports_failed_details_and_limits():
    r = request()
    pairs = date_pairs(r)
    prices = {pair: 500 + i for i, pair in enumerate(pairs)}
    result = run_search(r, FakeSource(prices, fail=[pairs[0]]), details=3, today=TODAY)
    assert len(result.offers) == 2
    assert any("1 von 3 Detailabfragen fehlgeschlagen" in note for note in result.notes)

    with pytest.raises(SearchError, match="Netzwerk weg"):
        run_search(request(), FakeSource(prices, fail=pairs), details=2, today=TODAY)
    with pytest.raises(SearchError, match="zu viele"):
        run_search(request(), FakeSource(prices), max_pairs=5, today=TODAY)


def test_run_search_without_prices_returns_empty_result():
    result = run_search(request(), FakeSource({}), today=TODAY)
    assert result.offers == [] and result.best is None and result.detailed == []
