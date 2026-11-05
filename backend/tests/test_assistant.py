import httpx
import respx
from pydantic import SecretStr

from homecoach.api import main as api
from homecoach.assistant import API_URL

from .test_api import client  # noqa: F401  (fixture)

FIRST = {
    "stop_reason": "tool_use",
    "content": [
        {"type": "server_tool_use", "id": "srv1", "name": "web_search", "input": {"query": "Corrida Sporting 2026"}},
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srv1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://corridasporting.pt",
                    "title": "Corrida Sporting",
                    "encrypted_content": "x",
                }
            ],
        },
        {"type": "text", "text": "Let me add that."},
        {
            "type": "tool_use",
            "id": "tu1",
            "name": "propose_race",
            "input": {
                "name": "Corrida Sporting 2026",
                "date": "2026-11-15",
                "distance_km": 10,
                "goal_time": "49:00",
                "priority": "A",
                "source_url": "https://corridasporting.pt",
            },
        },
    ],
}
SECOND = {
    "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "It's on 15 November in Lisbon. Click Add to save it."}],
}


@respx.mock
def test_assistant_proposes_without_saving(client, monkeypatch):  # noqa: F811
    s = api.get_settings()
    monkeypatch.setattr(s, "anthropic_api_key", SecretStr("sk-test"))
    route = respx.post(API_URL).mock(side_effect=[httpx.Response(200, json=FIRST), httpx.Response(200, json=SECOND)])
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "add corrida do sporting, 49 min"}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["proposals"][0]["date"] == "2026-11-15" and out["searches"] == 1
    assert out["reply"].startswith("It's on 15 November") and out["sources"][0]["url"] == "https://corridasporting.pt"
    sent = route.calls[1].request.content.decode()
    assert '"tool_result"' in sent and "propose_race" in route.calls[0].request.content.decode()
    assert route.calls[0].request.headers["x-api-key"] == "sk-test"
    assert client.get("/api/races").json() == []  # nothing saved until the user clicks Add


def test_assistant_disabled_without_key(client):  # noqa: F811
    assert client.get("/api/assistant").json()["enabled"] is False
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503 and "HC_ANTHROPIC_API_KEY" in r.json()["detail"]
