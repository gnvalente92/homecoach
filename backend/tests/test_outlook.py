"""Race outlook: rule facts and the AI interpretation."""

import json
from datetime import date, timedelta

import httpx
import respx

from homecoach.api import main as api
from homecoach.collector.sync import upsert_fitness
from homecoach.plan import engine as E
from homecoach.providers.base import FitnessRecord

T = date(2026, 11, 5)


def test_riegel_and_prediction_scaling():
    assert round(E.riegel(1500, 5, 10)) == 3127
    t, src = E.predicted_time({"5k": 1392, "10k": 2900, "half": 6518}, 10.0)
    assert src == "10k" and round(t) == 2900
    t, src = E.predicted_time({"5k": 1392, "10k": 2900, "half": 6518}, 14.0)  # nearest is 10k (log scale)
    assert src == "10k" and round(t) == round(2900 * 1.4**1.06)
    assert E.predicted_time(None, 10) is None


def test_verdict_thresholds():
    assert [E.verdict(x) for x in (-0.01, 0.0, 0.015, 0.04, 0.08)] == [
        "On track",
        "On track",
        "Within reach",
        "Stretch",
        "Ambitious",
    ]


def test_outlook_facts():
    runs = [
        E.Run(T - timedelta(days=3), 10, 3000),  # 5:00 /km
        E.Run(T - timedelta(days=10), 5, 1400),  # 4:40 /km -> ~2920 s for 10k
        E.Run(T - timedelta(days=12), 2, 500),  # too short to count as an effort
        E.Run(T - timedelta(days=80), 10, 2500),  # too old
    ]
    race = E.RaceIn("City 10K", T + timedelta(days=21), 10, 2950, "A", 1)
    o = E.race_outlook(race, runs, T, {"10k": 3000})
    assert o["goal_pace_s"] == 295 and o["predicted_s"] == 3000 and o["estimate_s"] == 3000
    assert o["gap_s"] == 50 and o["verdict"] == "Within reach" and o["weeks_to_go"] == 3.0
    assert o["best_effort"]["km"] == 5 and o["best_effort"]["equivalent_s"] == round(1400 * 2**1.06)
    assert o["goal_pace_km_4w"] == 7 and o["runs_4w"] == 3
    # no prediction -> the best effort is the estimate
    o = E.race_outlook(race, runs, T)
    assert o["estimate_s"] == o["best_effort"]["equivalent_s"] and o["verdict"] == "On track"
    # no goal -> facts but no verdict
    assert E.race_outlook(E.RaceIn("Fun run", T + timedelta(days=7), 5), runs, T)["verdict"] is None


@respx.mock
def test_outlook_endpoints(client, monkeypatch):
    s = api.get_settings()
    rid = client.post(
        "/api/races",
        json={"name": "City 10K", "date": str(api.today() + timedelta(days=28)), "distance_km": 10, "goal_time": "49:00"},
    ).json()["id"]
    db = next(api.app.dependency_overrides[api.get_session]())
    upsert_fitness(db, FitnessRecord(day=api.today(), vo2max=50, predictions={"10k": 2990, "half": 6600}))
    db.commit()

    out = client.get("/api/races/outlook").json()
    assert out["llm"] is False and out["races"][0]["verdict"] == "Within reach" and out["races"][0]["insight"] is None
    assert client.get("/api/summary").json()["predictions"]["10k"] == "49:50"
    assert client.post(f"/api/races/{rid}/outlook").status_code == 409

    monkeypatch.setattr(s, "llm_provider", "openai")
    monkeypatch.setattr(s, "llm_base_url", "http://llm.test/v1")
    reply = "You're about 1 minute off.\nFocus:\n- One tempo a week"
    llm = respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": reply}}]})
    )
    r = client.post(f"/api/races/{rid}/outlook", json={}).json()
    assert r["insight"]["text"] == reply and r["insight"]["today"] is True
    sent = json.loads(llm.calls[0].request.content)
    assert "tools" not in sent and "Goal: 49:00 = 4:54 /km" in sent["messages"][0]["content"]
    client.post(f"/api/races/{rid}/outlook", json={})  # cached for the day
    assert llm.call_count == 1
    client.post(f"/api/races/{rid}/outlook", json={"force": True})
    assert llm.call_count == 2
    assert client.delete(f"/api/races/{rid}").status_code == 204
    assert client.get("/api/races/outlook").json()["races"] == []
