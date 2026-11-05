import json
from datetime import timedelta

import httpx
import respx

from homecoach.api import main as api
from homecoach.models import CheckIn

LLM = "http://llm.test/v1/chat/completions"
SEARX = "http://searx.test/search"


def _tc(name, args, i="c1"):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": i, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
                }
            }
        ]
    }


def _text(t):
    return {"choices": [{"message": {"role": "assistant", "content": t}}]}


def _llm_on(monkeypatch):
    s = api.get_settings()
    monkeypatch.setattr(s, "llm_provider", "openai")
    monkeypatch.setattr(s, "llm_base_url", "http://llm.test/v1")
    monkeypatch.setattr(s, "searxng_url", "http://searx.test")


@respx.mock
def test_race_assistant_searches_reads_and_proposes(client, monkeypatch):
    from homecoach import websearch

    monkeypatch.setattr(websearch, "_public_address", lambda _: "93.184.216.34")
    _llm_on(monkeypatch)
    day = (api.today() + timedelta(days=10)).isoformat()
    respx.get(SEARX).mock(
        return_value=httpx.Response(
            200, json={"results": [{"title": "Corrida Sporting", "url": "https://race.test/", "content": "10 km Lisboa"}]}
        )
    )
    respx.get("https://93.184.216.34/").mock(
        return_value=httpx.Response(
            200,
            text=(
                '<html><script type="application/ld+json">{"@type":"SportsEvent","name":"Corrida Sporting","startDate":"'
                + day
                + '"}'
                "</script><body><h1>Corrida Sporting</h1><p>10 km · Lisboa · 10:00</p></body></html>"
            ),
        )
    )
    llm = respx.post(LLM).mock(
        side_effect=[
            httpx.Response(200, json=_tc("web_search", {"query": "Corrida Sporting"})),
            httpx.Response(200, json=_tc("fetch_page", {"url": "https://race.test/"})),
            httpx.Response(
                200,
                json=_tc("propose_race", {"name": "Corrida Sporting", "date": "2020-01-01", "distance_km": 10, "priority": "A"}),
            ),  # past date -> rejected, model retries
            httpx.Response(
                200,
                json=_tc(
                    "propose_race",
                    {
                        "name": "Corrida Sporting",
                        "date": day,
                        "distance_km": 10,
                        "priority": "A",
                        "goal_time": "49:00",
                        "source_url": "https://race.test/",
                    },
                ),
            ),
            httpx.Response(200, json=_text(f"It's on {day} in Lisbon. Click Add to save it.")),
        ]
    )
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "add corrida do sporting, 49 min"}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [p["date"] for p in out["proposals"]] == [day]
    assert out["sources"][0]["url"] == "https://race.test/"
    page_result = json.loads(llm.calls[2].request.content)["messages"][-1]["content"]
    assert "Structured event data" in page_result and day in page_result
    rejected = json.loads(llm.calls[3].request.content)["messages"][-1]["content"]
    assert rejected.startswith("Rejected:") and "past" in rejected
    assert client.get("/api/races").json() == []  # nothing saved until the user clicks Add


def test_llm_disabled(client):
    assert client.get("/api/assistant").json()["enabled"] is False
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503 and "HC_LLM_PROVIDER" in r.json()["detail"]


@respx.mock
def test_coach_proposes_with_guardrails_and_accept_applies(client, monkeypatch):
    _llm_on(monkeypatch)
    race_day = (api.today() + timedelta(days=60)).isoformat()
    client.post("/api/races", json={"name": "Half", "date": race_day, "distance_km": 21.1, "priority": "A"})
    plan = client.get("/api/plan").json()
    nxt = [s for s in plan["next_week"] if s["session"] == "Long run"][0]
    llm = respx.post(LLM).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_tc(
                    "propose_adjustment",
                    {"day": nxt["day"], "action": "replace", "session": "Long run", "km": 40, "reason": "go big"},
                ),
            ),  # too long -> rejected
            httpx.Response(
                200,
                json=_tc(
                    "propose_adjustment",
                    {
                        "day": nxt["day"],
                        "action": "shorten",
                        "km": nxt["km"] - 3,
                        "reason": "Calf is sore, so trim the long run.",
                    },
                ),
            ),
            httpx.Response(200, json=_text("Trimmed Sunday's long run because of your calf. Shout if it gets worse.")),
        ]
    )
    r = client.post("/api/coach/chat", json={"messages": [{"role": "user", "content": "my calf is a bit sore"}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["suggestions"]) == 1 and out["suggestions"][0]["status"] == "pending"
    sent = json.loads(llm.calls[0].request.content)
    assert "my calf is a bit sore" in sent["messages"][-1]["content"] and "NEXT WEEK" in sent["messages"][0]["content"]
    assert "Rejected:" in json.loads(llm.calls[1].request.content)["messages"][-1]["content"]
    # pending suggestions do not change the plan...
    assert [s for s in client.get("/api/plan").json()["next_week"] if s["session"] == "Long run"][0]["km"] == nxt["km"]
    # ...accepted ones do
    client.post(f"/api/coach/suggestions/{out['suggestions'][0]['id']}", json={"status": "accepted"})
    lr = [s for s in client.get("/api/plan").json()["next_week"] if s["session"] == "Long run"][0]
    assert lr["km"] == nxt["km"] - 3 and lr["adjusted"] and "calf" in lr["reason"].lower()
    state = client.get("/api/coach").json()
    assert state["checkins"][0]["text"] == "my calf is a bit sore" and state["notes"][0]["text"].startswith("Trimmed")


def test_inline_tool_call_parsing():
    from homecoach.llm import Tool, _inline_tool_call

    tools = {"propose_race": Tool("propose_race", "", {}, lambda a: "")}
    assert _inline_tool_call('Sure: {"name": "propose_race", "arguments": {"date": "2026-11-15"}}', tools) == (
        "propose_race",
        {"date": "2026-11-15"},
    )
    assert _inline_tool_call("<think>hmm</think> no tool here", tools) is None


def test_checkin_model_roundtrip(client):
    assert CheckIn.__tablename__ == "checkins"
