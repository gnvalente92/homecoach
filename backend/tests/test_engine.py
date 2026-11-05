from datetime import date, timedelta

import pytest

from homecoach.plan import engine as E

TODAY = date(2026, 11, 5)  # a Thursday
NEXT_MON = date(2026, 11, 9)

RUNS = [
    E.Run(date(2026, 11, 3), 12.13, 4338),
    E.Run(date(2026, 10, 29), 12.04, 4236),
    E.Run(date(2026, 10, 28), 12.04, 4154),
    E.Run(date(2026, 10, 27), 12.2, 4340),
    E.Run(date(2026, 10, 25), 11.55, 4168),
    E.Run(date(2026, 10, 23), 10.03, 3557),
    E.Run(date(2026, 10, 22), 10.04, 3565),
    E.Run(date(2026, 10, 20), 10.03, 3600),
    E.Run(date(2026, 10, 18), 10.03, 3641),
    E.Run(date(2026, 10, 11), 21.26, 6613),
]
DAYS = [E.Day(TODAY - timedelta(days=i), resting_hr=54, hrv_avg=40, hrv_lo=34, hrv_hi=44, load_ratio=0.94) for i in range(28)]


@pytest.fixture
def base():
    return E.baseline(RUNS, DAYS, TODAY, threshold_s=290)


def test_baseline(base):
    assert base.next_monday == NEXT_MON
    assert base.weekly_km == pytest.approx(30.3, abs=0.1)
    assert base.longest_km == pytest.approx(21.3, abs=0.1)
    assert base.rhr7 == base.rhr28 == 54


def test_readiness_levels(base):
    assert E.readiness(base).status == "Ready"
    hot = E.Baseline(**{**base.__dict__, "load_ratio": 1.4})
    assert E.readiness(hot).status == "Hold" and E.readiness(hot).factor == 0.9
    bad = E.Baseline(**{**base.__dict__, "load_ratio": 1.6, "hrv": 30})
    r = E.readiness(bad)
    assert r.status == "Back off" and r.factor == 0.75 and r.flags == 3
    assert any("Unavailable or stale: Sleep" in reason for reason in r.reasons)


def test_hrv_range_falls_back_to_rolling_stats():
    days = [E.Day(TODAY - timedelta(days=i), hrv_avg=40 + (i % 5)) for i in range(28)]
    b = E.baseline(RUNS, days, TODAY, threshold_s=290)
    assert b.hrv_lo is not None and b.hrv_lo < 42 < b.hrv_hi


def test_no_race_gives_keep_fitness_week(base):
    weeks = E.race_plan([], base)
    assert weeks == []
    s = E.next_week(base, weeks, E.readiness(base))
    names = [x.session for x in s]
    assert "Threshold run" in names and "Long run" in names
    assert all(NEXT_MON <= x.day < NEXT_MON + timedelta(days=7) for x in s)


def test_marathon_plan_shape(base):
    races = [E.RaceIn("Lisbon Marathon", date(2027, 4, 4), 42.2, 3 * 3600 + 45 * 60, "A")]
    weeks = E.race_plan(races, base)
    assert len(weeks) == 21 and weeks[-1].phase == "Race week" and weeks[0].phase == "Base"
    assert [w.phase for w in weeks[-3:]] == ["Taper", "Taper", "Race week"]
    assert max(w.total_km for w in weeks) <= 60
    assert max(w.long_km for w in weeks if w.phase != "Race week") >= 28
    for a, b in zip(weeks, weeks[1:], strict=False):  # never more than ~10% jump week to week (ignoring easier weeks)
        if not a.easier and not b.easier and b.phase in ("Base", "Build", "Peak") and a.phase in ("Base", "Build", "Peak"):
            assert b.total_km <= a.total_km * 1.1 + 0.5
    assert weeks[3].easier and weeks[3].total_km < weeks[2].total_km
    assert weeks[0].target["pace"] == "5:20"  # 3:45 / 42.2


def test_race_week_next_week(base):
    races = [E.RaceIn("Corrida Sporting", date(2026, 11, 15), 10, 49 * 60, "A")]
    weeks = E.race_plan(races, base)
    assert len(weeks) == 1 and weeks[0].phase == "Race week"
    s = E.next_week(base, weeks, E.readiness(base))
    assert s[-1].session == "Race: Corrida Sporting" and s[-1].day == date(2026, 11, 15) and s[-1].pace == "4:54 /km"
    assert all(x.day < date(2026, 11, 15) for x in s[:-1])


def test_b_and_c_races_shape_weeks(base):
    races = [
        E.RaceIn("Marathon", date(2027, 4, 4), 42.2, None, "A"),
        E.RaceIn("Half", date(2027, 3, 7), 21.1, 105 * 60, "B"),
        E.RaceIn("Sporting", date(2026, 11, 15), 10, 49 * 60, "C"),
    ]
    weeks = E.race_plan(races, base)
    c_week = weeks[0]
    assert c_week.race_in_week["priority"] == "C" and c_week.key.startswith("C race")
    b_idx = next(i for i, w in enumerate(weeks) if w.race_in_week and w.race_in_week["priority"] == "B")
    assert weeks[b_idx].long_km == 21
    assert weeks[b_idx + 1].key.startswith("Easy for the first 3 days")
    s = E.next_week(base, weeks, E.readiness(base))
    assert any(x.session == "C race: Sporting" for x in s)
    assert not any("Threshold" in x.session for x in s)


def test_readiness_scales_next_week(base):
    races = [E.RaceIn("Marathon", date(2027, 4, 4), 42.2, None, "A")]
    weeks = E.race_plan(races, base)
    ok = sum(x.km for x in E.next_week(base, weeks, E.Readiness("Ready", 1.0, 0)))
    low = sum(x.km for x in E.next_week(base, weeks, E.Readiness("Back off", 0.75, 2)))
    assert low < ok


def test_this_week_before_race(base):
    races = [E.RaceIn("Corrida Sporting", date(2026, 11, 15), 10, 49 * 60, "A")]
    s, done, target = E.this_week(RUNS, base, races, E.readiness(base))
    assert done == pytest.approx(12.1, abs=0.05)
    assert target == 26.0  # 30.3 * 0.85 = 25.8, rounded to 0.5 km; race within 14 days
    assert len({x.day for x in s}) == 7
    planned = [x for x in s if x.status == "Planned"]
    assert planned and planned[0].session == "Race-pace session" and planned[0].day == TODAY


def test_this_week_with_b_race_tapers(base):
    races = [E.RaceIn("Main", date(2027, 4, 4), 42.2, None, "A"), E.RaceIn("Tune", date(2026, 11, 8), 5, None, "B")]
    s, _, _ = E.this_week(RUNS, base, races, E.readiness(base))
    planned = [x.session for x in s if x.status == "Planned"]
    assert planned == ["Easy run + strides", "B race: Tune"]


def test_road_has_actual_then_planned(base):
    races = [E.RaceIn("Corrida Sporting", date(2026, 11, 15), 10, None, "A")]
    rows = E.road(RUNS, E.race_plan(races, base), base)
    assert [r["kind"] for r in rows] == ["Actual"] * 8 + ["Planned"]
    assert rows[7]["phase"] == "This week so far" and rows[7]["total_km"] == pytest.approx(12.1, abs=0.05)


def test_readiness_score_and_contributors(base):
    r = E.readiness(base)
    assert (
        r.score == 100
        and r.zone == "High"
        and {c.name for c in r.contributors} == {"Training load", "Overnight HRV", "Resting HR"}
    )
    tired = E.Baseline(**{**base.__dict__, "load_ratio": 1.55, "hrv": 30, "rhr7": 58, "rhr28": 54, "sleep_last_min": 300})
    r2 = E.readiness(tired)
    assert r2.score < 40 and r2.zone == "Poor" and all(c.level != "good" for c in r2.contributors)


def test_performance_chart():
    start = date(2026, 1, 1)
    loads = {start + timedelta(days=i): 60.0 for i in range(120)}
    rows = E.performance(loads, start + timedelta(days=60), start + timedelta(days=140))
    steady = rows[0]
    assert 40 < steady["fitness"] < 60 and steady["fatigue"] > steady["fitness"] and steady["form"] < 0
    rested = rows[-1]  # 20 days without load
    assert rested["form"] > 0 and rested["zone"] in ("Fresh", "Transition") and rested["ramp"] < 0


def test_activity_load_fallback():
    assert E.activity_load(3600, 150, 95) == 95
    assert 30 < E.activity_load(3600, 150, None) < 80
    assert E.activity_load(None, None, None) == 0


def test_endurance_shape():
    runs = [E.Run(TODAY - timedelta(days=i), 10) for i in range(1, 42, 2)]  # ~35 km/week, longest 10
    s = E.endurance_shape(runs, TODAY, 10)
    assert s["weekly_target"] == 40 and s["pct"] < 100 and s["longest_km"] == 10
    assert E.endurance_shape(runs, TODAY, 42.2)["pct"] < s["pct"]


def test_apply_adjustments():
    mon = date(2026, 11, 9)
    week = [
        E.Session(mon + timedelta(days=1), "Easy run", 8, "", ""),
        E.Session(mon + timedelta(days=3), "Key session", 10, "", ""),
        E.Session(mon + timedelta(days=6), "Long run", 16, "", ""),
    ]
    out = E.apply_adjustments(
        week,
        [
            E.Adjustment(mon + timedelta(days=3), "move", move_to=mon + timedelta(days=4), reason="travel"),
            E.Adjustment(mon + timedelta(days=6), "shorten", km=12, reason="sore calf"),
            E.Adjustment(mon + timedelta(days=1), "skip", reason="rest"),
        ],
    )
    by = {x.day.weekday(): x for x in out}
    assert by[4].session == "Key session" and by[4].adjusted and by[3].status == "Rest"
    assert by[6].km == 12 and by[6].reason == "sore calf" and by[1].status == "Rest"
    assert week[2].km == 16  # input untouched


def test_check_adjustment_guardrails():
    mon = date(2026, 11, 9)
    week = [E.Session(mon + timedelta(days=1), "Easy run", 8), E.Session(mon + timedelta(days=6), "Long run", 14)]
    ok = E.Adjustment(mon + timedelta(days=6), "shorten", km=10, reason="x")
    assert E.check_adjustment(ok, week, 22, 14, mon) is None
    too_long = E.Adjustment(mon + timedelta(days=6), "replace", session="Long run", km=25, reason="x")
    assert "longest recent run" in E.check_adjustment(too_long, week, 22, 14, mon)
    too_much = E.Adjustment(mon + timedelta(days=3), "replace", session="Easy run", km=12, reason="x")
    assert "safe limit" in E.check_adjustment(too_much, week, 22, 14, mon)
    past = E.Adjustment(mon - timedelta(days=1), "skip", reason="x")
    assert "past" in E.check_adjustment(past, week, 22, 14, mon)
    cross = E.Adjustment(mon + timedelta(days=6), "move", move_to=mon + timedelta(days=8), reason="x")
    assert "same week" in E.check_adjustment(cross, week, 22, 14, mon)
