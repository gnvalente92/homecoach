"""Nutrition: targets from training, profile import, food log and the food chat. All data is synthetic."""

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from homecoach import nutrition
from homecoach.api import main as api
from homecoach.api import nutrition as napi
from homecoach.models import Activity, NutritionProfile, Race
from homecoach.plan import nutrition as N
from homecoach.providers.coros_mcp import parse_profile

D = date(2026, 11, 6)  # a Friday
P = N.Profile(weight_kg=70, height_cm=178, birth_date=date(1990, 3, 10), sex="male")
LLM = "http://llm.test/v1/chat/completions"


# ---- rules -----------------------------------------------------------------------------------------


def test_resting_energy_and_daily_life_factor():
    assert P.age(D) == 36
    assert N.bmr(P, D) == pytest.approx(10 * 70 + 6.25 * 178 - 5 * 36 + 5)  # Mifflin-St Jeor, 1637.5
    assert N.bmr(N.Profile(70, 178, date(1990, 3, 10), "female"), D) == pytest.approx(1637.5 - 166)
    assert N.life_factor(None) == 1.3 and N.life_factor(2000) == 1.2 and N.life_factor(30000) == 1.5
    assert N.life_factor(10000) == pytest.approx(1.35)


def test_carbs_follow_the_training_load_and_protein_stays_put():
    rest = N.day_targets(P, D, [], [], 10000)
    easy = N.day_targets(P, D, [N.Workout("run", km=7, planned=True)], [], 10000)
    key = N.day_targets(P, D, [N.Workout("run", km=9, hard=True, planned=True)], [], 10000)
    long = N.day_targets(P, D, [N.Workout("run", km=18, long=True, planned=True)], [], 10000)
    assert [t.load for t in (rest, easy, key, long)] == ["Rest", "Light", "Moderate", "High"]
    assert [t.carbs_g for t in (rest, easy, key, long)] == [245, 350, 420, 490]  # 3.5, 5, 6, 7 g/kg
    assert {t.protein_g for t in (rest, easy, key, long)} == {126}  # 1.8 g/kg every day
    assert rest.kcal == 2210 and long.training_kcal == 70 * 18  # ~1 kcal/kg/km for a planned run
    assert rest.fat_g == round((1637.5 * 1.35 - 4 * (245 + 126)) / 9)
    assert long.fuelling[0].startswith("During the 108-min session: 30–60 g carbs per hour")
    assert rest.fluid_l == 2.5 and long.fluid_l == round(2.45 + 0.6 * 108 / 60, 1)


def test_recorded_calories_win_and_walks_are_left_to_the_steps():
    done = N.Workout("run", minutes=50, km=9, kcal=700)
    walk = N.Workout("walk", minutes=90, km=7, kcal=400)
    t = N.day_targets(P, D, [done, walk], [], 10000)
    assert t.training_kcal == 700 and t.load == "Light"


def test_carb_loading_before_a_half_and_a_marathon_race_day():
    half = N.RaceDay(D + timedelta(days=1), 21.1, "A", "Coastal Half")
    marathon = N.RaceDay(D + timedelta(days=2), 42.2, "B", "Harbour Marathon")
    assert N.day_targets(P, D, [], [half]).load == "Carb loading"
    assert N.day_targets(P, D, [], [half]).carbs_g == 8 * 70
    assert N.day_targets(P, D - timedelta(days=1), [], [half]).load == "Rest"  # one day for a half
    assert N.day_targets(P, D, [], [marathon]).carbs_g == 10 * 70  # two days for a marathon
    c_race = N.RaceDay(D + timedelta(days=1), 21.1, "C", "Training half")
    assert N.day_targets(P, D, [], [c_race]).load == "Rest"  # no loading for a C race
    race = N.day_targets(P, half.day, [], [half])
    assert race.load == "Race" and race.carbs_g == 7 * 70 and "60–90 g carbs per hour" in race.fuelling[0]
    loading = N.day_targets(P, D, [], [marathon])
    assert loading.fat_g == round(0.8 * 70)  # fat floor: energy rises instead of fat dropping below 0.8 g/kg
    assert loading.kcal == round((4 * (700 + 126) + 9 * 56) / 10) * 10


def test_weight_loss_never_cuts_on_hard_days_and_never_below_the_floor():
    lose = N.Profile(70, 178, date(1990, 3, 10), "male", goal="lose")
    keep = N.day_targets(P, D, [], [], 10000)
    rest = N.day_targets(lose, D, [], [], 10000)
    hard = N.day_targets(lose, D, [N.Workout("run", km=9, hard=True, planned=True)], [], 10000)
    hard_keep = N.day_targets(P, D, [N.Workout("run", km=9, hard=True, planned=True)], [], 10000)
    assert rest.protein_g == 140 and rest.fat_g == 56  # 2.0 g/kg protein while losing, fat at its floor
    # the energy floor (resting x 1.2 = 1965 kcal) caps the deficit; carbs give way so fat stays at its minimum
    assert rest.kcal == 1960 and rest.carbs_g == 225 and keep.kcal - rest.kcal == 250
    assert hard.kcal == hard_keep.kcal and "No deficit on a hard day" in hard.reasons[-1]
    tiny = N.Profile(45, 150, date(1950, 1, 1), "female", goal="lose")
    t = N.day_targets(tiny, D, [], [], 2000)
    assert t.kcal >= round(N.bmr(tiny, D) * 1.2 / 10) * 10 - 10


def test_parse_profile_from_the_coros_report():
    text = "User Profile Information\n=====\n\nHeight: 181.0 cm\nWeight: 68.5 kg\nBirthday: 1988-07-14 (Age: 38)\nGender: Female"
    assert parse_profile(text) == {"height_cm": 181.0, "weight_kg": 68.5, "birth_date": date(1988, 7, 14), "sex": "female"}
    assert parse_profile("Height: 9 cm\nWeight: 0 kg") == {}


# ---- database and API ------------------------------------------------------------------------------


def test_profile_from_the_watch_unless_edited_by_hand(client):
    db = next(api.app.dependency_overrides[api.get_session]())
    data = {"weight_kg": 68.5, "height_cm": 181, "birth_date": date(1988, 7, 14), "sex": "female"}
    assert nutrition.update_from_provider(db, data) is True
    assert db.get(NutritionProfile, 1).source == "coros"
    assert nutrition.update_from_provider(db, {**data, "weight_kg": 67.9}) is True
    db.get(NutritionProfile, 1).follow_provider = False
    assert nutrition.update_from_provider(db, {**data, "weight_kg": 70}) is False
    assert db.get(NutritionProfile, 1).weight_kg == 67.9


@pytest.fixture
def fed(client, monkeypatch):
    monkeypatch.setattr(napi, "today", lambda: D)
    monkeypatch.setattr(api, "today", lambda: D)
    db = next(api.app.dependency_overrides[api.get_session]())
    db.add(
        Activity(
            id="demo:r1",
            provider="demo",
            external_id="r1",
            sport="Outdoor Run",
            type="Run",
            local_date=D,
            start_time=datetime(2026, 11, 6, 7, tzinfo=UTC),
            duration_s=3000,
            distance_m=9000,
            calories=690,
        )
    )
    db.add(Race(name="Coastal Half", day=D + timedelta(days=9), distance_km=21.0975, priority="A"))
    db.commit()
    return client


def test_targets_need_a_profile_then_cover_this_week_and_next(fed):
    out = fed.get("/api/nutrition").json()
    assert out["days"] == [] and set(out["profile"]["missing"]) == {"weight_kg", "height_cm", "birth_date", "sex"}
    body = {"weight_kg": 70, "height_cm": 178, "birth_date": "1990-03-10", "sex": "male"}
    assert fed.put("/api/nutrition/profile", json={**body, "birth_date": "2020-01-01"}).status_code == 422
    assert fed.put("/api/nutrition/profile", json=body).json()["source"] == "manual"
    days = {x["day"]: x for x in fed.get("/api/nutrition").json()["days"]}
    assert len(days) == 14 and min(days) == "2026-11-02" and max(days) == "2026-11-15"
    today = days["2026-11-06"]
    assert today["training_kcal"] == 690 and today["sessions"][0]["planned"] is False  # the watch's calories
    assert days["2026-11-14"]["load"] == "Carb loading" and days["2026-11-15"]["load"] == "Race"
    assert all(x["protein_g"] == 126 for x in days.values())


def test_food_log_add_list_delete_and_totals(fed):
    fed.put("/api/nutrition/profile", json={"weight_kg": 70, "height_cm": 178, "birth_date": "1990-03-10", "sex": "male"})
    meal = {
        "day": "2026-11-06",
        "meal": "breakfast",
        "items": [
            {"name": "Oats", "quantity": "60 g", "kcal": 230, "protein_g": 8, "carbs_g": 40, "fat_g": 4},
            {"name": "Banana", "quantity": "1", "kcal": 105, "protein_g": 1.3, "carbs_g": 27, "fat_g": 0.4},
        ],
    }
    added = fed.post("/api/nutrition/food", json=meal).json()
    assert [x["name"] for x in added] == ["Oats", "Banana"]
    assert fed.post("/api/nutrition/food", json={**meal, "day": "2026-11-07"}).status_code == 422  # future
    assert fed.post("/api/nutrition/food", json={**meal, "items": [{**meal["items"][0], "kcal": -5}]}).status_code == 422
    eaten = next(x for x in fed.get("/api/nutrition").json()["days"] if x["day"] == "2026-11-06")["eaten"]
    assert eaten == {"kcal": 335, "protein_g": 9, "carbs_g": 67, "fat_g": 4, "entries": 2}
    assert fed.delete(f"/api/nutrition/food/{added[0]['id']}").status_code == 204
    assert [x["name"] for x in fed.get("/api/nutrition/food?day=2026-11-06").json()] == ["Banana"]


# ---- food chat -------------------------------------------------------------------------------------


def _tc(args, i):
    call = {"id": i, "type": "function", "function": {"name": "propose_food", "arguments": json.dumps(args)}}
    return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [call]}}]}


@respx.mock
def test_food_chat_proposes_after_fixing_inconsistent_numbers(fed, monkeypatch):
    assert fed.post("/api/nutrition/chat", json={"messages": [{"role": "user", "content": "2 eggs"}]}).status_code == 409
    s = api.get_settings()
    monkeypatch.setattr(s, "llm_provider", "openai")
    monkeypatch.setattr(s, "llm_base_url", "http://llm.test/v1")
    fed.put("/api/nutrition/profile", json={"weight_kg": 70, "height_cm": 178, "birth_date": "1990-03-10", "sex": "male"})
    eggs = {"name": "Scrambled eggs", "quantity": "2 eggs", "kcal": 150, "protein_g": 12, "carbs_g": 1, "fat_g": 10}
    toast = {"name": "Wholemeal toast", "quantity": "2 slices", "kcal": 400, "protein_g": 8, "carbs_g": 32, "fat_g": 3}
    llm = respx.post(LLM).mock(
        side_effect=[
            httpx.Response(200, json=_tc({"meal": "breakfast", "items": [eggs, toast]}, "c1")),  # toast kcal is off
            httpx.Response(200, json=_tc({"meal": "breakfast", "items": [eggs, {**toast, "kcal": 190}]}, "c2")),
            httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "About 340 kcal."}}]}),
        ]
    )
    out = fed.post("/api/nutrition/chat", json={"messages": [{"role": "user", "content": "2 eggs and 2 toasts"}]}).json()
    assert out["reply"] == "About 340 kcal." and len(out["proposals"]) == 1
    p = out["proposals"][0]
    assert p["meal"] == "breakfast" and p["day"] == "2026-11-06" and p["total"]["kcal"] == 340
    second = json.loads(llm.calls[1].request.content)
    assert "doesn't match the macros" in second["messages"][-1]["content"]  # the model was told why
    system = json.loads(llm.calls[0].request.content)["messages"][0]["content"]
    assert "Target for that day:" in system and "126 g" in system and "Logged so far: nothing logged yet" in system
    assert fed.get("/api/nutrition/food").json() == []  # nothing saved until the user confirms


def test_alcohol_may_exceed_the_macros():
    from homecoach.food_assistant import check_item

    beer = check_item({"name": "Beer", "quantity": "330 ml", "kcal": 145, "protein_g": 1.5, "carbs_g": 13, "fat_g": 0})
    assert beer["kcal"] == 145
