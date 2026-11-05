import json
from datetime import timedelta

import httpx
import pytest
import respx

from homecoach.api import main as api
from homecoach.plan import engine as E


def test_strength_add_update_and_remove_preserve_running_plan():
    day = api.today()
    week = [E.Session(day, "Easy run", 8), E.Session(day + timedelta(days=1), "Rest", status="Rest")]
    a = E.Adjustment(day, "strength", detail="Controlled bodyweight exercises", reason="Maintain strength", duration_min=25)
    assert E.check_adjustment(a, week, 8, 10, day) is None
    after = E.apply_adjustments(week, [a])
    assert [s.km for s in after if s.session == "Easy run"] == [8]
    assert [s.duration_min for s in after if s.session == "Strength"] == [25]
    a.duration_min = 15
    after = E.apply_adjustments(after, [a])
    assert [s.duration_min for s in after if s.session == "Strength"] == [15]
    a.duration_min = 0
    assert E.check_adjustment(a, after, 8, 10, day) is None
    assert not any(s.session == "Strength" for s in E.apply_adjustments(after, [a]))


@pytest.mark.parametrize("minutes,km", [(61, None), (-1, None), (20.5, None), (True, None), (20, 5)])
def test_strength_rejects_invalid_duration_and_running_units(minutes, km):
    day = api.today()
    a = E.Adjustment(day, "strength", km=km, detail="Easy exercises", duration_min=minutes)
    assert E.check_adjustment(a, [], 30, 14, day)


def test_strength_rejects_race_day_completed_session_and_third_weekly_session():
    day = E.next_monday(api.today())
    a = E.Adjustment(day, "strength", detail="Easy exercises", duration_min=20)
    assert E.check_adjustment(a, [E.Session(day, "Race: 10K", 10)], 30, 14, api.today())
    assert E.check_adjustment(a, [E.Session(day, "Strength", status="Done")], 30, 14, api.today())
    existing = [E.Session(day + timedelta(days=d), "Strength", duration_min=30) for d in (1, 3)]
    assert E.check_adjustment(a, existing, 30, 14, api.today())


@respx.mock
def test_coach_proposes_strength_and_acceptance_adds_it_without_removing_run(client, monkeypatch):
    settings = api.get_settings()
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.test/v1")
    original = client.get("/api/plan").json()
    run = next(x for x in original["next_week"] if x["status"] == "Planned" and x["session"] != "Strength")
    args = {
        "day": run["day"],
        "action": "strength",
        "duration_min": 20,
        "detail": "Bodyweight squat, calf raise and core exercises at an easy effort.",
        "reason": "Make room for strength work alongside the easy run.",
    }
    call = {
        "role": "assistant",
        "tool_calls": [
            {"id": "s1", "type": "function", "function": {"name": "propose_adjustment", "arguments": json.dumps(args)}}
        ],
    }
    route = respx.post("http://llm.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": call}]}),
            httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "Suggested a short strength session."}}]}
            ),
        ]
    )
    result = client.post("/api/coach/chat", json={"messages": [{"role": "user", "content": "Include some strength training"}]})
    assert result.status_code == 200, result.text
    suggestion = result.json()["suggestions"][0]
    assert suggestion["duration_min"] == 20 and suggestion["status"] == "pending"
    assert len([x for x in client.get("/api/plan").json()["next_week"] if x["session"] == "Strength"]) == 1
    accepted = client.post(f"/api/coach/suggestions/{suggestion['id']}", json={"status": "accepted"})
    assert accepted.status_code == 200, accepted.text
    plan = client.get("/api/plan").json()
    same_day = [x for x in plan["next_week"] if x["day"] == run["day"]]
    assert len(same_day) == 2 and next(x for x in same_day if x["session"] == "Strength")["duration_min"] == 20
    assert sum(x["km"] for x in plan["next_week"]) == sum(x["km"] for x in original["next_week"])
    assert "Review strength training" in json.loads(route.calls[0].request.content)["messages"][0]["content"]
