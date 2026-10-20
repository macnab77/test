import io
import json
from datetime import date

from report import StatusLine, fmt_day, fmt_duration, fmt_price, render_text, result_to_dict
from search import run_search
from test_search import TODAY, offer, request


class ReportSource:
    """Drei Verbindungen: Empfehlung, günstigste und schnellste sind verschieden."""

    name = "Testquelle"
    notes = ["2 von 6 Preisabfragen fehlgeschlagen (Test)."]

    def price_calendar(self, request, pairs, progress):
        return {pairs[0]: 700.0, pairs[1]: 650.0}  # übrige Tage ohne Preis

    def offers(self, request, pair):
        offers = [
            offer(900, 650, 650, number="TG 9"),  # Bewertung 1.442
            offer(650, 960, 960, via=("IST",), number="TK 1"),  # 1.450, am günstigsten
            offer(1500, 600, 600, number="LH 1"),  # 2.000, am schnellsten
        ]
        if pair[1] is None:  # nur Hinflug
            for one_way in offers:
                one_way.inbound = None
        return offers


def make_result(**changes):
    return run_search(request(**changes), ReportSource(), today=TODAY)


def test_formats():
    assert fmt_price(2190) == "2.190 €"
    assert fmt_price(1234.6, "USD") == "1.235 USD"
    assert fmt_duration(650) == "10 Std. 50 Min."
    assert fmt_duration(45) == "45 Min."
    assert fmt_duration(120) == "2 Std."
    assert fmt_day(date(2026, 12, 2)) == "Mi 02.12."
    assert fmt_day(date(2026, 12, 2), year=True) == "Mi 02.12.2026"


def test_render_text_shows_calendar_highlights_and_notes():
    result = make_result()
    text = render_text(result, top=1)
    assert "FRA, MUC → BKK · Economy · 1 Erwachsener · Testquelle" in text
    assert "Hinflug Di 01.12.–Do 03.12.2026 · Rückflug So 20.12.–Mo 21.12.2026" in text
    assert "25 € je Stunde Reisezeit" in text
    calendar = text[text.index("Preiskalender"):text.index("Beste Verbindungen")]
    assert "650★" in calendar and "700 " in calendar and "–" in calendar
    # Platz 1 plus günstigste und schnellste, obwohl nur top=1 verlangt
    assert text.count("\n 1. 900 €") == 1 and "★ Empfehlung" in text
    assert " 2. 650 €" in text and "günstigste" in text and "1 Umstieg (IST)" in text
    assert " 3. 1.500 €" in text and "schnellste" in text
    assert "Hin   Di 01.12.  10:00 FRA → 20:50 BKK" in text
    assert "Hinweis: 2 von 6 Preisabfragen fehlgeschlagen (Test)." in text


def test_render_text_one_way_and_empty():
    text = render_text(make_result(return_from=None, return_to=None))
    assert "günstigster Preis je Tag" in text and "Rück " not in text
    empty = run_search(request(), type("Empty", (ReportSource,), {"price_calendar": lambda *a: {}})(), today=TODAY)
    assert "Keine passenden Verbindungen gefunden." in render_text(empty)


def test_result_to_dict_is_json_for_the_agent():
    data = result_to_dict(make_result(), top=1)
    json.dumps(data)
    assert [entry["markierung"] for entry in data["rangliste"]] == [["Empfehlung"], ["günstigste"], ["schnellste"]]
    first = data["rangliste"][0]
    assert first["preis"] == 900 and first["hinflug"]["abflug"] == "FRA 10:00" and first["rueckflug"]["umstiege"] == 0
    assert data["guenstigste_termine"][0] == {"hin": "2026-12-01", "rueck": "2026-12-21", "preis": 650.0}
    assert data["datumskombinationen"] == {"geprueft": 6, "mit_preis": 2, "verbindungen_geladen_fuer": 2}


def test_status_line_only_writes_to_terminals():
    piped = io.StringIO()
    StatusLine(piped)("Preise: 1/3")
    assert piped.getvalue() == ""

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    status = StatusLine(terminal)
    status("Preise: 1/30")
    status("Fertig")
    status.clear()
    assert terminal.getvalue() == "\rPreise: 1/30\rFertig      \r      \r"
