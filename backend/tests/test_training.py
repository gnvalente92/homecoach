from datetime import UTC, date, datetime

import pytest

from homecoach.api import main as api
from homecoach.api import training
from homecoach.models import Activity


@pytest.fixture
def history(client, monkeypatch):
    monkeypatch.setattr(training, "today", lambda: date(2026, 11, 5))
    factory = api.app.dependency_overrides[api.get_session]
    gen = factory()
    db = next(gen)
    db.query(Activity).delete()
    for key, day, sport, km, duration, name in [
        ("run1", date(2026, 11, 2), "Run", 5, 1800, "Morning hills"),
        ("run2", date(2026, 11, 5), "Run", 10, 3600, "Lunch 100% effort"),
        ("gym", date(2026, 11, 4), "Strength", None, 1800, "Strength"),
        ("oct", date(2026, 10, 31), "Run", 8, 2800, "Earlier run"),
        ("prev", date(2025, 11, 3), "Run", 6, 1900, "Previous year"),
        ("prevlate", date(2025, 11, 20), "Run", 20, 7000, "Not in elapsed days"),
        ("future", date(2026, 11, 6), "Run", 100, 40000, "Future dated"),
    ]:
        db.add(
            Activity(
                id=key,
                provider="test",
                external_id=key,
                sport=sport,
                type=sport,
                name=name,
                local_date=day,
                start_time=datetime(day.year, day.month, day.day, 7, tzinfo=UTC),
                distance_m=km * 1000 if km is not None else None,
                duration_s=duration,
            )
        )
    db.commit()
    gen.close()
    return client


@pytest.mark.parametrize(
    "day,period,start,end",
    [
        (date(2026, 11, 1), "weekly", date(2026, 10, 26), date(2026, 11, 2)),
        (date(2024, 2, 29), "monthly", date(2024, 2, 1), date(2024, 3, 1)),
        (date(2026, 12, 31), "yearly", date(2026, 1, 1), date(2027, 1, 1)),
    ],
)
def test_calendar_boundaries(day, period, start, end):
    assert training.period_bounds(day, period) == (start, end)


def test_goals_count_only_matching_recorded_activities_and_recur(history, monkeypatch):
    body = {"period": "weekly", "sport": "Run", "metric": "distance_km", "target": 20}
    created = history.post("/api/goals", json=body)
    assert created.status_code == 201
    goal = history.get("/api/goals").json()[0]
    assert goal["value"] == 15 and goal["remaining"] == 5 and goal["percent"] == 75
    assert goal["start"] == "2026-11-02" and goal["end"] == "2026-11-08"
    assert not goal["complete"]
    duplicate = history.post("/api/goals", json=body)
    assert duplicate.status_code == 409
    gid = created.json()["id"]
    assert history.put(f"/api/goals/{gid}", json={**body, "target": 10}).status_code == 200
    assert history.get("/api/goals").json()[0]["complete"]
    monkeypatch.setattr(training, "today", lambda: date(2026, 11, 9))
    assert history.get("/api/goals").json()[0]["value"] == 0
    assert history.delete(f"/api/goals/{gid}").status_code == 204
    assert history.get("/api/goals").json() == []
    assert history.get("/api/training/log").json()["total"] == 7


def test_time_and_session_goals_include_strength_but_report_missing_distance(history):
    for metric, target in [("duration_h", 1), ("sessions", 2), ("distance_km", 1)]:
        assert (
            history.post(
                "/api/goals", json={"period": "monthly", "sport": "Strength", "metric": metric, "target": target}
            ).status_code
            == 201
        )
    time, sessions, distance = history.get("/api/goals").json()
    assert time["value"] == 0.5 and time["percent"] == 50
    assert sessions["value"] == 1 and sessions["missing_sessions"] == 0
    assert distance["value"] == 0 and distance["missing_sessions"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"target": -1},
        {"target": 0},
        {"target": 1e-200},
        {"target": 1.5, "metric": "sessions"},
        {"sport": "bad"},
        {"period": "daily"},
    ],
)
def test_invalid_goal_is_rejected(client, changes):
    body = {"period": "weekly", "sport": "Run", "metric": "distance_km", "target": 20}
    assert client.post("/api/goals", json={**body, **changes}).status_code == 422


def test_training_log_search_dates_sport_and_pagination(history):
    first = history.get("/api/training/log?sport=Run&start=2026-11-01&end=2026-11-05&limit=1").json()
    second = history.get("/api/training/log?sport=Run&start=2026-11-01&end=2026-11-05&limit=1&offset=1").json()
    assert first["total"] == 2
    assert [first["items"][0]["id"], second["items"][0]["id"]] == ["run2", "run1"]
    assert history.get("/api/training/log?q=HILLS").json()["total"] == 1
    assert history.get("/api/training/log?q=%25").json()["total"] == 1  # literal %, not a SQL wildcard
    assert history.get("/api/training/log?sport=Strength").json()["items"][0]["duration_s"] == 1800
    assert history.get("/api/training/log?start=2026-11-10&end=2026-11-01").status_code == 422
    assert history.get("/api/training/log?limit=5000").status_code == 422


def test_monthly_comparison_matches_elapsed_days_not_full_previous_month(history):
    result = history.get("/api/training/monthly?year=2026&sport=Run").json()
    november = result["months"][-1]
    assert len(result["months"]) == 11
    assert november["current"]["distance_km"] == 15
    assert november["previous"]["distance_km"] == 6
    assert november["end"] == "2026-11-05" and november["previous_end"] == "2025-11-05"
    assert history.get("/api/training/monthly?year=2027").status_code == 422
    assert "raw" not in history.get("/api/training/log").json()["items"][0]


def test_leap_day_comparison_has_valid_previous_year_end(history, monkeypatch):
    monkeypatch.setattr(training, "today", lambda: date(2024, 2, 29))
    february = history.get("/api/training/monthly?year=2024").json()["months"][-1]
    assert february["end"] == "2024-02-29" and february["previous_end"] == "2023-02-28"
