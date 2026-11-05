from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from homecoach.api import main as api
from homecoach.collector.sync import upsert_activity, upsert_daily
from homecoach.db import Base
from homecoach.providers.base import ActivityRecord, DailyRecord


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    def session():
        s = Session()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    api.app.dependency_overrides[api.get_session] = session
    today = api.today()
    with Session() as s:
        for i in range(0, 28, 2):
            d = today - timedelta(days=i + 1)
            start = datetime(d.year, d.month, d.day, 7, tzinfo=UTC)
            upsert_activity(
                s, ActivityRecord("coros", f"r{i}", "Outdoor Run", start, d, duration_s=3600, distance_m=10000, avg_hr=148)
            )
        for i in range(28):
            upsert_daily(
                s,
                DailyRecord(
                    today - timedelta(days=i),
                    {"steps": 12000, "resting_hr": 54, "hrv_avg": 40, "sleep_min": 480, "load_ratio": 1.0},
                ),
            )
        s.commit()
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_summary_and_weekly(client):
    s = client.get("/api/summary?days=28").json()
    assert s["runs"] == 14 and s["run_km"] == 140 and s["days_10k"] == 28 and s["avg_sleep_h"] == 8.0
    w = client.get("/api/weekly?days=28").json()
    assert sum(x["run_km"] for x in w) == 140


def test_race_crud_and_plan(client):
    assert client.get("/api/races").json() == []
    race_day = (api.today() + timedelta(days=40)).isoformat()
    r = client.post(
        "/api/races", json={"name": "Test 10k", "date": race_day, "distance_km": 10, "goal_time": "49:00", "priority": "A"}
    )
    assert r.status_code == 201
    race = r.json()
    assert race["goal_time"] == "49:00" and race["goal_pace"] == "4:54"
    bad = client.post("/api/races", json={"name": "x", "date": race_day, "distance_km": 10, "goal_time": "abc"})
    assert bad.status_code == 422

    p = client.get("/api/plan").json()
    assert p["target_race"]["name"] == "Test 10k"
    assert p["race_plan"][-1]["phase"] == "Race week"
    assert len({s["day"] for s in p["this_week"]["sessions"]}) == 7
    assert p["readiness"]["status"] == "Ready"
    assert len(p["road"]) == 8 + len(p["race_plan"])

    upd = client.put(f"/api/races/{race['id']}", json={**race, "priority": "B", "goal_time": "50:00"}).json()
    assert upd["priority"] == "B"
    assert client.delete(f"/api/races/{race['id']}").status_code == 204
    assert client.get("/api/plan").json()["race_plan"] == []


def test_parse_time():
    assert api.parse_time("49:00") == 2940
    assert api.parse_time("3:45") == 13500
    assert api.parse_time("1:45:00") == 6300


def test_basic_auth(client, monkeypatch):
    from pydantic import SecretStr

    s = api.get_settings()
    monkeypatch.setattr(s, "auth_user", "me")
    monkeypatch.setattr(s, "auth_password", SecretStr("pw"))
    assert client.get("/api/races").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/races", auth=("me", "pw")).status_code == 200
    assert client.get("/api/races", auth=("me", "nope")).status_code == 401


def test_fitness_and_summary_previous(client):
    f = client.get("/api/fitness?days=28").json()
    assert len(f) == 28 and {"fitness", "fatigue", "form", "ramp", "zone"} <= set(f[0])
    assert f[-1]["fitness"] > 0
    s = client.get("/api/summary?days=14").json()
    assert s["previous"] is not None and s["previous"]["runs"] == 7 and s["runs"] == 7
    p = client.get("/api/plan").json()
    assert p["readiness"]["score"] is not None and p["readiness"]["contributors"]
