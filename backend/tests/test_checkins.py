"""Check-ins change the plan by rules; the plan lists the next and last 7 days without weekdays."""

from datetime import timedelta

from homecoach.api import main as api


def test_checkin_changes_the_rolling_plan_until_it_expires(client):
    before = client.get("/api/plan").json()
    todo = before["next_7_days"]["todo"]
    assert todo and all(x["status"] == "Planned" for x in todo) and before["checkin"] is None
    assert before["last_7_days"]["done_km"] == 30.0  # the fixture runs 10 km every other day: 3 in the last 7 days
    assert client.post("/api/checkins", json={"feeling": "angry"}).status_code == 422
    c = client.post("/api/checkins", json={"feeling": "ill", "note": "Head cold"}).json()
    assert any(x["day"] <= c["until"] and "race" not in x["session"].lower() for x in todo)  # something to cancel
    assert c["until"] == str(api.today() + timedelta(days=2)) and c["note"] == "Head cold"
    after = client.get("/api/plan").json()
    assert after["checkin"]["feeling"] == "ill"
    soon = [x for x in after["next_7_days"]["todo"] if x["day"] <= c["until"]]
    assert all("race" in x["session"].lower() for x in soon)  # ill: everything but races becomes rest
    assert client.get("/api/checkins").json()["active"]["id"] == c["id"]
    # a newer "good" check-in ends the earlier one
    client.post("/api/checkins", json={"feeling": "good"})
    assert client.get("/api/plan").json()["checkin"] is None
    assert client.get("/api/checkins").json()["active"] is None
    assert client.delete(f"/api/checkins/{c['id']}").status_code == 204
    assert client.delete(f"/api/checkins/{c['id']}").status_code == 404


def test_race_prompt_and_import(client):
    client.post("/api/races", json={"name": "Old 10K", "date": str(api.today() + timedelta(days=20)), "distance_km": 10})
    prompt = client.get("/api/races/prompt", params={"request": "Example Half, goal 1:45:00"}).json()["prompt"]
    assert "Example Half, goal 1:45:00" in prompt and api.today().isoformat() in prompt and "race_id" in prompt
    assert '"B"' in prompt  # an A race exists, so new races default to B
    rid = client.get("/api/races").json()[0]["id"]
    day = api.today() + timedelta(days=60)
    answer = (
        "Here you go:\n```json\n"
        f'{{"races": [{{"race_id": {rid}, "name": "Old 10K", "date": "{day}", "distance_km": 10, "priority": "A"}},'
        f' {{"race_id": 999, "name": "Example Half", "date": "{day}", "distance_km": "21.0975", "goal_time": "1:45:00",'
        f' "priority": "Z", "source_url": "javascript:alert(1)"}}]}}\n```'
    )
    got = client.post("/api/races/import", json={"text": answer}).json()["proposals"]
    assert got[0]["race_id"] == rid and got[1]["race_id"] is None and got[1]["distance_km"] == 21.0975
    assert got[1]["priority"] == "A" and got[1]["source_url"] is None and got[1]["goal_time"] == "1:45:00"
    assert len(client.get("/api/races").json()) == 1  # nothing saved until the user confirms
    past = answer.replace(str(day), str(api.today() - timedelta(days=1)))
    assert "in the past" in client.post("/api/races/import", json={"text": past}).json()["detail"]
    none = client.post("/api/races/import", json={"text": '{"races": [], "error": "date not published"}'})
    assert none.status_code == 422 and "date not published" in none.json()["detail"]
