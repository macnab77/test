import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

anthropic = pytest.importorskip("anthropic")
from anthropic.lib.tools import ToolError  # noqa: E402

import agent  # noqa: E402
from report import StatusLine  # noqa: E402
from test_cli import RecordingSource  # noqa: E402

OUT = date.today() + timedelta(days=30)


@pytest.fixture
def tools(monkeypatch):
    source = RecordingSource()
    monkeypatch.setattr(agent, "make_source", lambda name: source)
    found = {tool.name: tool for tool in agent.make_tools("google", StatusLine())}
    found["source"] = source
    return found


def test_tool_schemas(tools):
    schema = tools["flug_suchen"].to_dict()["input_schema"]
    assert schema["required"] == ["von", "nach", "hinflug_von", "hinflug_bis"]
    assert schema["properties"]["klasse"]["enum"] == ["economy", "premium", "business", "first"]
    assert "IATA" in schema["properties"]["von"]["description"]
    assert tools["flughafen_suchen"].to_dict()["input_schema"]["required"] == ["suchbegriff"]


def test_flug_suchen_runs_the_search(tools):
    result = tools["flug_suchen"].call({
        "von": ["FRA", "MUC"],
        "nach": ["BKK"],
        "hinflug_von": OUT.isoformat(),
        "hinflug_bis": (OUT + timedelta(days=2)).isoformat(),
        "rueckflug_von": (OUT + timedelta(days=14)).isoformat(),
        "klasse": "business",
        "erwachsene": 2,
        "max_umstiege": 1,
    })
    data = json.loads(result)
    assert data["rangliste"][0]["markierung"] == ["Empfehlung"]
    request = tools["source"].requests[0]
    assert request.cabin == "business" and request.adults == 2 and request.max_stops == 1
    assert request.return_to == OUT + timedelta(days=14)  # nur ein Rückflugtag angegeben


def test_flug_suchen_turns_problems_into_tool_errors(tools):
    with pytest.raises(ToolError, match="Ungültige Eingabe"):
        tools["flug_suchen"].call({"von": ["FRA"], "nach": ["BKK"], "hinflug_von": "1.12.", "hinflug_bis": "2.12."})
    with pytest.raises(ToolError, match="Rückflug-Zeitraum liegt vor dem Hinflug"):
        tools["flug_suchen"].call({
            "von": ["FRA"],
            "nach": ["BKK"],
            "hinflug_von": OUT.isoformat(),
            "hinflug_bis": OUT.isoformat(),
            "rueckflug_von": (OUT - timedelta(days=3)).isoformat(),
        })


def test_flughafen_suchen_knows_german_city_names(tools):
    found = json.loads(tools["flughafen_suchen"].call({"suchbegriff": "Mailand"}))
    assert [entry["code"] for entry in found][:3] == ["MXP", "LIN", "BGY"]


def test_flughafen_suchen_finds_airport_names(tools):
    pytest.importorskip("fli")
    heathrow = json.loads(tools["flughafen_suchen"].call({"suchbegriff": "Heathrow"}))
    assert heathrow[0] == {"code": "LHR", "name": "London Heathrow Airport"}


# --- Gesprächsschleife ohne echte API ----------------------------------------------

def message(text, stop_reason="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)


class FakeRunner:
    def __init__(self, steps):
        self.steps = steps
        self.response = None

    def __iter__(self):
        for reply, response in self.steps:
            self.response = response
            yield reply

    def generate_tool_call_response(self):
        return self.response


class FakeClient:
    """Liefert je Runde vorbereitete Antworten (oder wirft einen Fehler)."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(tool_runner=self.tool_runner))

    def tool_runner(self, **params):
        self.calls.append({**params, "messages": list(params["messages"])})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return FakeRunner(turn)


def run_chat(client, monkeypatch, *inputs, first="Von FRA nach BKK im Dezember"):
    answers = iter([*inputs, "ende"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    agent.chat(client, [], "System", first)


def test_chat_keeps_history_including_tool_calls(monkeypatch, capsys):
    tool_result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}
    client = FakeClient([
        [(message("", "tool_use"), tool_result), (message("Empfehlung: TG 921 für 2.190 €"), None)],
        [(message("In Economy kostet es 780 €."), None)],
    ])
    run_chat(client, monkeypatch, "Und in Economy?")
    assert "Agent: Empfehlung: TG 921 für 2.190 €" in capsys.readouterr().out
    second = client.calls[1]
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user", "assistant", "user"]
    assert second["messages"][2] is tool_result
    assert second["model"] == agent.MODEL and second["fallbacks"] == "default"


def test_chat_drops_refused_or_failed_rounds(monkeypatch, capsys):
    httpx2 = pytest.importorskip("httpx2")  # HTTP-Bibliothek des anthropic-SDK
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    client = FakeClient([
        [(message("", "refusal"), None)],
        anthropic.APIConnectionError(request=request),
        [(message("Gern!"), None)],
    ])
    run_chat(client, monkeypatch, "Zweiter Versuch", "Dritter Versuch")
    captured = capsys.readouterr()
    assert "Dabei kann ich nicht helfen" in captured.out
    assert "Keine Verbindung zur Claude-API" in captured.err
    # Abgelehnte und fehlgeschlagene Runden stehen nicht mehr im Verlauf
    assert [m["content"] for m in client.calls[2]["messages"]] == ["Dritter Versuch"]


def test_chat_explains_missing_api_key(monkeypatch, capsys):
    client = FakeClient([TypeError("Could not resolve authentication method. Expected one of api_key …")])
    run_chat(client, monkeypatch)
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert len(client.calls) == 1  # danach beendet
