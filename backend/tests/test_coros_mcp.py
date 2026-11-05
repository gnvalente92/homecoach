"""COROS MCP: report parsers (synthetic reports in the server's text format) and the OAuth + MCP flow
against a mocked server."""

import json
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import respx

from homecoach import coros_auth
from homecoach.api import main as api
from homecoach.providers import coros_mcp as P

ACTIVITIES = """Sport Records — 2026-10-01 to 2026-11-05 (3 records)
========================

1. Outdoor Run — 2026-11-03
   Location: Riverside Run
   Start Coordinates: 0.0, 0.0
   Time Window: startTimestamp=1793728157 | endTimestamp=1793732494
   Duration: 1:12:18 | Distance: 12.13 km
   Average Pace: 5:58 /km | Avg HR: 151 bpm | Calories: 942 kcal
   LabelId: 111 | SportType: 100

2. Strength — 2026-11-02
   Location: Strength
   Time Window: startTimestamp=1793619509 | endTimestamp=1793622483
   Duration: 49:34 | Sets: 1
 | Avg HR: 92 bpm | Calories: 230 kcal
   LabelId: 222 | SportType: 402

3. Walk — 2026-11-01
   Location: Park Walk
   Duration: 2:31:54 | Distance: 8.83 km
   Average Pace: 17:12 /km | Avg HR: 96 bpm | Calories: 689 kcal
   LabelId: 333 | SportType: 900"""

DAILY = """Daily Health Data — Last 3 days | Resting HR: 52 bpm | HRV Baseline: 42 ms
Note: sleep entries are dated by their wake-up day.

--- 20260511 ---
Steps: 0 | Calories: 0 kcal | Exercise: 0 min
Stress: Avg 12

--- 20260513 ---
Steps: 8,625 | Calories: 407 kcal | Exercise: 1 min
Stress: Avg 20
Sleep Summary: (Score: 73)
  Total: 7h 50min | Awake: 10 min
  Deep: 54 min | Light: 4h 18min | REM: 2h 38min

--- 20261104 ---
Steps: 11,720 | Calories: 593 kcal | Exercise: 1h 3min
Stress: Avg 24
Sleep Summary:
  Total: 7h 6min | Deep: 1h 54min | Light: 3h 27min | REM: 1h 43min | Awake: 2 min
  Sleep HR: Avg 55 bpm | Min 51 bpm | Max 62 bpm"""

SLEEP = """Sleep Overview
========================

2026-11-03
Sleep Score: 88
Main Sleep (asleep): 8h 6min
Main Sleep Window: 2026-11-02 22:30 - 2026-11-03 06:44

2026-11-05
Sleep detail for this day is not available yet."""

RHR = "Resting Heart Rate — Last 3 days\n=====\n\n2026-11-04: 55 bpm\n2026-11-03: 52 bpm\n2026-05-12: No data"
HRV = """HRV Assessment — Last 7 days
========================

2026-11-04:
  HRV Avg: 37 ms — Normal
  Normal Range: 34 - 44 ms
  Baseline: 39 ms

Sleep HRV Time Series — Last 7 days
2026-10-30:
  timestamp=1793318990, timezone=0, hrv=82 ms"""
LOAD = """Training Load Assessment
=====

2026-11-05
Comment: Maintaining
Short-Term Load: 53
Long-Term Load: 56
Load Ratio: 0.94"""
FITNESS = """Fitness Assessment Overview
========================

VO2max: 52
Running Level: 78
Threshold Pace: 4:50 /km
5 km Prediction: 23:12
10 km Prediction: 48:20
Half Marathon Prediction: 1:48:38
Marathon Prediction: 3:49:59"""


def test_parse_activities():
    a = P.parse_activities(ACTIVITIES, "Europe/Lisbon")
    assert [x.type for x in a] == ["Run", "Strength", "Walk"]
    run = a[0]
    assert (
        run.id == "coros:111" and run.distance_m == 12130 and run.duration_s == 4338 and run.avg_hr == 151 and run.calories == 942
    )
    assert run.local_date == date(2026, 11, 3) and run.start_time.tzinfo is not None
    assert a[1].duration_s == 2974 and a[1].distance_m is None and a[1].avg_hr == 92
    assert a[2].start_time.date() == date(2026, 11, 1)  # no timestamp -> start of the day


def test_parse_daily_both_layouts():
    d = P.parse_daily(DAILY)
    assert "steps" not in d[date(2026, 5, 11)]  # not worn yet
    old = d[date(2026, 5, 13)]
    assert (
        old["steps"] == 8625
        and old["sleep_score"] == 73
        and old["sleep_min"] == 470
        and old["rem_min"] == 158
        and old["deep_min"] == 54
    )
    new = d[date(2026, 11, 4)]
    assert new == {
        "steps": 11720,
        "active_kcal": 593,
        "exercise_min": 63,
        "stress_avg": 24,
        "sleep_min": 426,
        "deep_min": 114,
        "light_min": 207,
        "rem_min": 103,
        "awake_min": 2,
    }


def test_parse_sleep_rhr_hrv_load_fitness():
    s = P.parse_sleep(SLEEP, "Europe/Lisbon")
    assert s[date(2026, 11, 3)]["sleep_score"] == 88 and s[date(2026, 11, 3)]["sleep_min"] == 486
    assert s[date(2026, 11, 3)]["sleep_start"].hour == 22 and date(2026, 11, 5) not in s
    assert P.parse_rhr(RHR) == {date(2026, 11, 4): {"resting_hr": 55}, date(2026, 11, 3): {"resting_hr": 52}}
    assert P.parse_hrv(HRV) == {date(2026, 11, 4): {"hrv_avg": 37.0, "hrv_lo": 34.0, "hrv_hi": 44.0}}
    assert P.parse_load(LOAD) == {date(2026, 11, 5): {"load_short": 53.0, "load_long": 56.0, "load_ratio": 0.94}}
    f = P.parse_fitness(FITNESS, date(2026, 11, 5))
    assert (
        f.vo2max == 52
        and f.threshold_pace_s == 290
        and f.predictions == {"5k": 1392, "10k": 2900, "half": 6518, "marathon": 13799}
    )


# ---- OAuth + MCP against a mocked server -----------------------------------------------------------

MCP = "https://mcpeu.coros.test/mcp"
AS = "https://auth.coros.test"
TOOL_TEXT = {
    "querySportRecords": ACTIVITIES,
    "queryDailyHealthData": DAILY,
    "querySleepOverview": SLEEP,
    "queryRestingHeartRate": RHR,
    "querySleepHrv": HRV,
    "queryTrainingLoadAssessment": LOAD,
    "queryFitnessAssessmentOverview": FITNESS,
}
SCHEMAS = {
    "querySportRecords": {"startDate": {}, "endDate": {}, "limit": {}},
    "querySleepOverview": {"startDate": {}, "endDate": {}},
}


def mock_coros(state):
    respx.get("https://mcp.coros.test/mcp").mock(return_value=httpx.Response(302, headers={"location": MCP}))
    respx.get(MCP).mock(return_value=httpx.Response(405))
    respx.get("https://mcpeu.coros.test/.well-known/oauth-protected-resource/mcp").mock(
        return_value=httpx.Response(
            200, json={"resource": MCP, "authorization_servers": [AS], "scopes_supported": ["health:read"]}
        )
    )
    respx.get(f"{AS}/.well-known/oauth-authorization-server").mock(
        return_value=httpx.Response(
            200,
            json={
                "issuer": AS,
                "authorization_endpoint": f"{AS}/authorize",
                "token_endpoint": f"{AS}/token",
                "registration_endpoint": f"{AS}/register",
            },
        )
    )

    def register(req):  # like COROS: remote addresses must be https, loopback http is fine
        body = json.loads(req.content)
        state.setdefault("registrations", []).append(body["redirect_uris"])
        if any(not coros_auth.redirect_allowed(u) for u in body["redirect_uris"]):
            return httpx.Response(
                400, json={"error": "invalid_redirect_uri", "error_description": "Remote redirect_uri must use https"}
            )
        state["redirect_uris"] = body["redirect_uris"]
        return httpx.Response(201, json={"client_id": "hc-client", "redirect_uris": body["redirect_uris"]})

    def token(req):
        form = parse_qs(req.content.decode())
        state.setdefault("token_calls", []).append(form)
        if form["grant_type"] == ["authorization_code"]:
            assert form["code"] == ["the-code"] and len(form["code_verifier"][0]) >= 43 and form["resource"] == [MCP]
            return httpx.Response(200, json={"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 3600})
        assert form["refresh_token"] == ["ref-1"]
        return httpx.Response(200, json={"access_token": "tok-2", "expires_in": 3600})

    respx.post(f"{AS}/register").mock(side_effect=register)
    respx.post(f"{AS}/token").mock(side_effect=token)

    def mcp(req):
        msg = json.loads(req.content)
        if req.headers["authorization"] == "Bearer tok-1" and state.get("expire_once"):
            state["expire_once"] = False
            return httpx.Response(401)
        assert req.headers["authorization"] in ("Bearer tok-1", "Bearer tok-2")
        if msg.get("method") == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}},
                headers={"mcp-session-id": "sess-1"},
            )
        assert req.headers.get("mcp-session-id") == "sess-1"
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "tools/list":
            tools = [
                {"name": n, "inputSchema": {"type": "object", "properties": SCHEMAS.get(n, {"days": {}})}} for n in TOOL_TEXT
            ]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": tools}})
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        state.setdefault("calls", []).append((name, args))
        payload = {"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": TOOL_TEXT[name]}]}}
        sse = f"event: message\ndata: {json.dumps(payload)}\n\n"  # answer as an event stream
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    respx.post(MCP).mock(side_effect=mcp)


@respx.mock
def test_connect_callback_and_sync(client, monkeypatch):
    s = api.get_settings()
    monkeypatch.setattr(s, "provider", "coros-mcp")
    monkeypatch.setattr(s, "coros_mcp_url", "https://mcp.coros.test/mcp")
    monkeypatch.setattr(s, "download_fit", False)
    state: dict = {}
    mock_coros(state)
    assert client.get("/api/coros").json()["connected"] is False

    # the app is on plain http on the LAN: only the localhost address is registered, and the user pastes it back
    out = client.post("/api/coros/connect", json={}).json()
    q = parse_qs(urlsplit(out["url"]).query)
    assert out["paste"] is True and out["url"].startswith(f"{AS}/authorize")
    assert q["code_challenge_method"] == ["S256"] and q["resource"] == [MCP]
    assert q["redirect_uri"] == [api.FALLBACK_REDIRECT] and state["registrations"] == [[api.FALLBACK_REDIRECT]]

    monkeypatch.setattr(api, "_start_first_sync", lambda: None)
    landed = f"{api.FALLBACK_REDIRECT}?code=the-code&state=wrong"
    assert client.post("/api/coros/paste", json={"url": landed}).status_code == 400
    landed = f"{api.FALLBACK_REDIRECT}?code=the-code&state={q['state'][0]}"
    r = client.post("/api/coros/paste", json={"url": landed})
    assert r.status_code == 200 and r.json()["connected"] is True

    # sync through the provider, with one expired token on the way (refresh, retry)
    from homecoach.collector.sync import sync_provider
    from homecoach.providers.coros_mcp import CorosMCPProvider

    db = next(api.app.dependency_overrides[api.get_session]())
    state["expire_once"] = True
    provider = CorosMCPProvider(coros_auth.session(db), "Europe/Lisbon")
    monkeypatch.setattr(s, "backfill_days", 400)
    stats = sync_provider(db, provider, s, full=True)
    assert provider.s.state.pop("changed") and provider.s.state["tokens"]["access_token"] == "tok-2"
    coros_auth.save(db, provider.s.state)
    db.commit()
    assert stats["activities_seen"] == 3 and stats["days"] >= 4
    called = dict(state["calls"])
    assert (
        called["querySportRecords"]["limit"] == 1000
        and "startDate" in called["querySleepOverview"]
        and "days" in called["queryDailyHealthData"]
    )

    daily = {d["day"]: d for d in client.get("/api/daily?days=365").json()}
    nov4 = daily["2026-11-04"]
    assert nov4["steps"] == 11720 and nov4["resting_hr"] == 55 and nov4["hrv_avg"] == 37 and nov4["sleep_min"] == 426
    assert daily["2026-11-03"]["sleep_score"] == 88
    assert client.get("/api/summary?days=365").json()["vo2max"] == 52


def test_paste_fallback_rejects_bad_url(client):
    r = client.post("/api/coros/paste", json={"url": "http://localhost/homecoach-coros-callback?nothing=here"})
    assert r.status_code == 400 and "code=" in r.json()["detail"]


def test_today_window_helper():
    assert (date(2026, 11, 5) - timedelta(days=7)).isoformat() == "2026-10-29"


@respx.mock
def test_connect_over_https_comes_straight_back(client, monkeypatch):
    from fastapi.testclient import TestClient

    s = api.get_settings()
    monkeypatch.setattr(s, "coros_mcp_url", "https://mcp.coros.test/mcp")
    state: dict = {}
    mock_coros(state)
    https = TestClient(api.app, base_url="https://coach.example")
    out = https.post("/api/coros/connect", json={}).json()
    q = parse_qs(urlsplit(out["url"]).query)
    assert out["paste"] is False and q["redirect_uri"] == ["https://coach.example/api/coros/callback"]
    assert sorted(state["redirect_uris"]) == sorted([api.FALLBACK_REDIRECT, "https://coach.example/api/coros/callback"])
    monkeypatch.setattr(api, "_start_first_sync", lambda: None)
    ok = https.get("/api/coros/callback", params={"code": "the-code", "state": q["state"][0]}, follow_redirects=False)
    assert ok.status_code == 303 and https.get("/api/coros").json()["connected"] is True
    assert https.post("/api/coros/connect", json={"paste": True}).json()["paste"] is True  # the manual fallback still works


def test_redirect_allowed():
    assert coros_auth.redirect_allowed("https://coach.example/cb")
    assert coros_auth.redirect_allowed("http://localhost/homecoach-coros-callback")
    assert not coros_auth.redirect_allowed("http://192.168.1.20:8742/api/coros/callback")


def test_config_never_exposes_coros_tokens(client):
    from homecoach.collector.sync import set_state

    db = next(api.app.dependency_overrides[api.get_session]())
    coros_auth.save(db, {"tokens": {"access_token": "secret-token", "refresh_token": "secret-refresh"}})
    set_state(db, "coros", {"last_sync": "2026-11-05T15:40:00+00:00"})
    db.commit()
    body = client.get("/api/config").text
    assert "secret" not in body and coros_auth.KEY not in body and "2026-11-05T15:40" in body
