import json
from datetime import date, timedelta

import pytest

import flugsuche
from test_report import ReportSource

# main() rechnet mit dem echten Datum, also Reisedaten relativ zu heute
OUT = date.today() + timedelta(days=30)
BACK = OUT + timedelta(days=14)
HIN = f"{OUT:%d.%m.%Y}-{OUT + timedelta(days=2):%d.%m.%Y}"
RUECK = f"{BACK:%d.%m.%Y}-{BACK + timedelta(days=1):%d.%m.%Y}"


class RecordingSource(ReportSource):
    default_details = 5

    def __init__(self):
        self.requests = []

    def price_calendar(self, request, pairs, progress):
        self.requests.append(request)
        return super().price_calendar(request, pairs, progress)


@pytest.fixture
def source(monkeypatch):
    recording = RecordingSource()
    monkeypatch.setattr(flugsuche, "make_source", lambda name: recording)
    return recording


def test_cli_prints_ranking(source, capsys):
    code = flugsuche.main(
        ["--von", "FRA,München", "--nach", "Bangkok", "--hin", HIN, "--rueck", RUECK, "--klasse", "Business", "--direkt"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "FRA, MUC → BKK, DMK · Business" in out and "★ Empfehlung" in out and "Preiskalender" in out
    request = source.requests[0]
    assert (request.depart_from, request.depart_to) == (OUT, OUT + timedelta(days=2))
    assert (request.return_from, request.return_to) == (BACK, BACK + timedelta(days=1))
    assert request.max_stops == 0 and request.cabin == "business"


def test_cli_json_output(source, capsys):
    code = flugsuche.main(["--von", "HAM", "--nach", "LHR", "--hin", HIN, "--json", "--naechte", "1-3"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["suche"]["von"] == ["HAM"] and data["rangliste"]
    assert source.requests[0].return_from is None  # ohne --rueck nur Hinflug


def test_cli_reports_input_errors(source, capsys):
    assert flugsuche.main(["--von", "FRA", "--nach", "BKK", "--hin", "31.02."]) == 1
    assert "Fehler: Ungültiges Datum: 31.02" in capsys.readouterr().err
    assert flugsuche.main(["--von", "FRA", "--nach", "Atlantis", "--hin", HIN]) == 1
    assert "Atlantis" in capsys.readouterr().err


def test_cli_without_terminal_needs_arguments(source):
    with pytest.raises(SystemExit) as exit_info:
        flugsuche.main([])
    assert exit_info.value.code == 2


def test_cli_asks_for_missing_values(source, monkeypatch, capsys):
    answers = iter(["FRA", "BKK", HIN, "", "business", "2"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert flugsuche.main([]) == 0
    request = source.requests[0]
    assert request.origins == ["FRA"] and request.return_from is None
    assert request.cabin == "business" and request.adults == 2
