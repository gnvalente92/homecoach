"""Race outlook: rule facts and the rule-written summary."""

from datetime import date, timedelta

from homecoach import outlook
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


def test_summary_reads_the_facts():
    runs = [E.Run(T - timedelta(days=3), 10, 3000)]
    race = E.RaceIn("City 10K", T + timedelta(days=28), 10, 2950, "A", 1)
    o = outlook.summary(E.race_outlook(race, runs, T, {"10k": 3000}))
    assert "50:00" in o["text"] and "0:50 (1.7%) slower than your goal of 49:10" in o["text"]
    assert "usually closes a gap of that size" in o["text"]  # 4 weeks x 0.5% covers 1.7%
    assert "Extend the long run towards 14 km." in o["focus"] and 1 <= len(o["focus"]) <= 3
    far = outlook.summary(E.race_outlook(E.RaceIn("City 10K", T + timedelta(days=28), 10, 2700), runs, T, {"10k": 3000}))
    assert "more than training usually closes" in far["text"] and "a goal around 49:00" in far["text"]
    fast = outlook.summary(E.race_outlook(E.RaceIn("City 10K", T + timedelta(days=7), 10, 3100), runs, T, {"10k": 3000}))
    assert "faster than your goal" in fast["text"] and fast["focus"][0].startswith("Taper")
    none = outlook.summary(E.race_outlook(E.RaceIn("City 10K", T + timedelta(days=21), 10, 2950), [], T))
    assert none["text"].startswith("There's no estimate yet")
    assert outlook.summary(E.race_outlook(E.RaceIn("Fun run", T + timedelta(days=7), 5), runs, T)) is None


def test_outlook_endpoint(client):
    rid = client.post(
        "/api/races",
        json={"name": "City 10K", "date": str(api.today() + timedelta(days=28)), "distance_km": 10, "goal_time": "49:00"},
    ).json()["id"]
    db = next(api.app.dependency_overrides[api.get_session]())
    upsert_fitness(db, FitnessRecord(day=api.today(), vo2max=50, predictions={"10k": 2990, "half": 6600}))
    db.commit()

    out = client.get("/api/races/outlook").json()["races"][0]
    assert out["verdict"] == "Within reach" and "49:50" in out["summary"]["text"] and out["summary"]["focus"]
    assert client.get("/api/summary").json()["predictions"]["10k"] == "49:50"
    assert client.delete(f"/api/races/{rid}").status_code == 204
    assert client.get("/api/races/outlook").json()["races"] == []
