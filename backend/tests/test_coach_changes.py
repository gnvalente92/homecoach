"""Coach changes do what they say: one change per day, gym swaps, no zero-km runs, skips that are really moves,
and Undo. Replays a real mix-up: Friday ended up with an easy run and a gym session, Sunday's run vanished."""

from datetime import date, timedelta

import pytest

from homecoach.api import main as api
from homecoach.models import CoachSuggestion
from homecoach.plan import engine as E

MON = date(2026, 11, 2)
FRI, SAT, SUN = MON + timedelta(days=4), MON + timedelta(days=5), MON + timedelta(days=6)
WEEK = [
    E.Session(MON, "Strength", status="Done", duration_min=49),
    E.Session(FRI, "Easy run", 6, "5:54–6:17 /km"),
    E.Session(SAT, "Rest", status="Rest"),
    E.Session(SUN, "Long run", 12, "6:00 /km"),
]
GYM = E.Adjustment(
    FRI, "replace", session="Strength", duration_min=45, detail="Squats, lunges, planks", reason="gym instead", id=7
)


def check(a, week=WEEK, target=30):
    return E.check_adjustment(a, week, target, 14, FRI)


def test_swapping_a_run_for_gym_leaves_one_session():
    assert check(GYM) is None
    fri = [x for x in E.apply_adjustments(WEEK, [GYM]) if x.day == FRI]
    assert [(x.session, x.duration_min, x.suggestion_id) for x in fri] == [("Strength", 45, 7)]


def test_gym_swap_needs_a_duration_and_a_description_and_not_on_race_day():
    assert "duration_min" in check(E.Adjustment(FRI, "replace", session="Strength", detail="x", reason="r"))
    assert "describe" in check(E.Adjustment(FRI, "replace", session="Strength", duration_min=45, reason="r"))
    race = [E.Session(FRI, "C race: Parkrun", 5)]
    assert "race" in check(GYM, race)


def test_strength_action_still_adds_gym_next_to_the_run():
    add = E.Adjustment(FRI, "strength", duration_min=30, detail="Core", reason="extra", id=8)
    fri = sorted(x.session for x in E.apply_adjustments(WEEK, [add]) if x.day == FRI)
    assert fri == ["Easy run", "Strength"]


def test_replace_needs_a_distance():
    assert "needs a distance" in check(E.Adjustment(FRI, "replace", session="Easy run", reason="Adjusted to fit race schedule"))
    assert check(E.Adjustment(FRI, "replace", session="Easy run", km=5, reason="shorter")) is None


@pytest.mark.parametrize(
    "reason",
    ["Athlete will do easy run on Saturday instead of Sunday", "Move the long run to saturday", "Do it on Saturday"],
)
def test_a_skip_that_describes_a_move_is_rejected(reason):
    assert "use move" in check(E.Adjustment(SUN, "skip", reason=reason))


def test_a_plain_skip_is_fine_even_when_it_mentions_another_day():
    assert check(E.Adjustment(SUN, "skip", reason="Rest after Saturday's busy day")) is None
    assert check(E.Adjustment(SUN, "skip", reason="Calf is sore, take the day off")) is None


def test_every_changed_session_knows_its_suggestion():
    changes = [
        E.Adjustment(SUN, "move", move_to=SAT, reason="busy Sunday", id=1),
        E.Adjustment(FRI, "shorten", km=4, reason="tired", id=2),
    ]
    out = {(x.day, x.session): x.suggestion_id for x in E.apply_adjustments(WEEK, changes)}
    assert out[(SAT, "Long run")] == 1 and out[(SUN, "Rest")] == 1 and out[(FRI, "Easy run")] == 2


# ---- through the API: superseding and Undo ----------------------------------------------------------


@pytest.fixture
def planned(client, monkeypatch):
    monkeypatch.setattr(api, "today", lambda: FRI)
    baseline = E.baseline([], [], FRI)
    baseline.longest_km = 14

    def plan(db):
        changes = api.accepted_adjustments(db, MON)
        week = E.apply_adjustments(WEEK, changes)
        return {
            "baseline": baseline,
            "this_week": {"sessions": week, "target_km": 30},
            "next_week": [],
            "next_week_target_km": 0,
            "rule_this_week": WEEK,
            "rule_next_week": [],
        }

    monkeypatch.setattr(api, "compute_plan", plan)
    monkeypatch.setattr(E, "next_monday", lambda d: MON + timedelta(days=7))
    baseline.next_monday = MON + timedelta(days=7)
    return client, plan


def add(client, **kw):
    db = next(api.app.dependency_overrides[api.get_session]())
    row = CoachSuggestion(day=FRI, reason="r", **kw)
    db.add(row)
    db.commit()
    return row.id


def test_a_newer_change_for_the_day_replaces_the_older_one_and_undo_restores_the_plan(planned):
    client, plan = planned
    easy = add(client, action="replace", session="Easy run", km=5)
    gym = add(client, action="replace", session="Strength", duration_min=45, detail="Squats, lunges")
    assert client.post(f"/api/coach/suggestions/{easy}", json={"status": "accepted"}).status_code == 200
    assert client.post(f"/api/coach/suggestions/{gym}", json={"status": "accepted"}).status_code == 200
    statuses = {x["id"]: x["status"] for x in client.get("/api/coach").json()["suggestions"]}
    assert statuses[easy] == "superseded" and statuses[gym] == "accepted"
    db = next(api.app.dependency_overrides[api.get_session]())
    fri = [x for x in plan(db)["this_week"]["sessions"] if x.day == FRI]
    assert [(x.session, x.suggestion_id) for x in fri] == [("Strength", gym)]
    # Undo = dismiss the accepted change: Friday goes back to the rule plan
    assert client.post(f"/api/coach/suggestions/{gym}", json={"status": "dismissed"}).status_code == 200
    fri = [x for x in plan(db)["this_week"]["sessions"] if x.day == FRI]
    assert [(x.session, x.km, x.suggestion_id) for x in fri] == [("Easy run", 6, None)]


def test_gym_next_to_a_run_does_not_supersede_a_run_change(planned):
    client, _ = planned
    short = add(client, action="shorten", km=4)
    extra = add(client, action="strength", duration_min=30, detail="Core")
    for sid in (short, extra):
        assert client.post(f"/api/coach/suggestions/{sid}", json={"status": "accepted"}).status_code == 200
    statuses = {x["id"]: x["status"] for x in client.get("/api/coach").json()["suggestions"]}
    assert statuses[short] == statuses[extra] == "accepted"


def test_the_api_does_not_expose_the_internal_rule_plan(client):
    assert not any(k.startswith("rule_") for k in client.get("/api/plan").json())
