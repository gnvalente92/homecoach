"""Rule-based training plan engine.

Pure functions over plain data (no database), so it is easy to test and reason about.
Rules:
- Baseline: last 28 days of running (weekly km, runs per week, longest run) + threshold pace.
- Readiness: load ratio > 1.3 (> 1.5 counts twice), last night's HRV below its normal range, resting HR
  7-day average >= 3 bpm above the 28-day average. 0 flags: Ready 100%, 1: Hold 90%, 2+: Back off 75%.
- Race plan to the target race (nearest A race, else nearest race): base / build / peak / taper,
  +8% volume a week capped at a distance-specific peak, every 4th week 20% easier, taper by distance.
  B races: mini-taper week (80% volume, race replaces key session and long run), easier start next week.
  C races: replace that week's hard session.
- Next week: week 1 of the race plan (or a "keep fitness" week with no race), scaled by readiness.
- This week: what was done so far + suggestions for the remaining days to reach the week's target.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from statistics import mean, pstdev

DAY = timedelta(days=1)


# ---- inputs -------------------------------------------------------------------------------------


@dataclass
class Run:
    day: date
    km: float
    duration_s: int | None = None
    type: str = "Run"
    sport: str = "Run"
    avg_hr: int | None = None

    @property
    def pace_s(self) -> float | None:
        return self.duration_s / self.km if self.duration_s and self.km else None


@dataclass
class Day:
    day: date
    resting_hr: float | None = None
    hrv_avg: float | None = None
    hrv_lo: float | None = None
    hrv_hi: float | None = None
    load_ratio: float | None = None


@dataclass
class RaceIn:
    name: str
    day: date
    distance_km: float
    goal_time_s: int | None = None
    priority: str = "A"
    id: int | None = None


# ---- outputs ------------------------------------------------------------------------------------


@dataclass
class Baseline:
    today: date
    next_monday: date
    weekly_km: float
    runs_per_week: float
    longest_km: float
    threshold_s: float
    load_ratio: float | None
    hrv: float | None
    hrv_lo: float | None
    hrv_hi: float | None
    rhr7: float | None
    rhr28: float | None


@dataclass
class Readiness:
    status: str
    factor: float
    flags: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class Week:
    week: date
    weeks_to_go: int
    phase: str
    total_km: float
    long_km: float
    key: str
    easier: bool = False
    race_in_week: dict | None = None
    target: dict | None = None  # the target race: name, day, km, pace


@dataclass
class Session:
    day: date
    session: str
    km: float = 0
    pace: str = ""
    detail: str = ""
    status: str = "Planned"  # Planned | Done | Rest


# ---- helpers ------------------------------------------------------------------------------------


def half(x: float) -> float:
    return round(x * 2) / 2


def clamp(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))


def fmt_pace(s: float) -> str:
    s = round(s)
    return f"{s // 60}:{s % 60:02d}"


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def next_monday(d: date) -> date:
    return monday(d) + timedelta(days=7)


def race_pace(r: RaceIn, threshold_s: float) -> float:
    if r.goal_time_s:
        return r.goal_time_s / r.distance_km
    f = 1.12 if r.distance_km >= 40 else 1.05 if r.distance_km >= 20 else 1.0 if r.distance_km >= 9 else 0.96
    return threshold_s * f


def easy_range(t: float) -> str:
    return f"{fmt_pace(t * 1.22)}–{fmt_pace(t * 1.30)} /km"


# ---- baseline & readiness -----------------------------------------------------------------------


def estimate_threshold(runs: list[Run], today: date) -> float:
    """Fallback when the provider gives no threshold pace: ~82% of the median recent run pace."""
    paces = sorted(r.pace_s for r in runs if r.pace_s and r.day >= today - 56 * DAY)
    return paces[len(paces) // 2] * 0.82 if paces else 300.0


def baseline(runs: list[Run], days: list[Day], today: date, threshold_s: float | None = None) -> Baseline:
    frm = today - 27 * DAY
    recent = [r for r in runs if r.type == "Run" and r.km and frm <= r.day <= today]
    days = sorted(days, key=lambda d: d.day)

    def avg(xs: list[float]) -> float | None:
        xs = [x for x in xs if x is not None]
        return round(mean(xs), 1) if xs else None

    last = days[-1] if days else None
    hrv_lo, hrv_hi = (last.hrv_lo, last.hrv_hi) if last else (None, None)
    if last and last.hrv_avg is not None and (hrv_lo is None or hrv_hi is None):
        hist = [d.hrv_avg for d in days if d.hrv_avg is not None and d.day >= today - 28 * DAY]
        if len(hist) >= 7:
            m, sd = mean(hist), pstdev(hist)
            hrv_lo, hrv_hi = round(m - sd, 1), round(m + sd, 1)
    ratios = [d.load_ratio for d in days if d.load_ratio is not None]
    return Baseline(
        today=today,
        next_monday=next_monday(today),
        weekly_km=round(sum(r.km for r in recent) / 4, 1),
        runs_per_week=round(len(recent) / 4, 1),
        longest_km=round(max((r.km for r in recent), default=0), 1),
        threshold_s=threshold_s or estimate_threshold(runs, today),
        load_ratio=ratios[-1] if ratios else None,
        hrv=last.hrv_avg if last else None,
        hrv_lo=hrv_lo,
        hrv_hi=hrv_hi,
        rhr7=avg([d.resting_hr for d in days if d.day >= today - 6 * DAY]),
        rhr28=avg([d.resting_hr for d in days if d.day >= frm]),
    )


def readiness(b: Baseline) -> Readiness:
    reasons: list[str] = []
    flags = 0
    if b.load_ratio is not None and b.load_ratio > 1.5:
        flags += 2
        reasons.append(f"load ratio {b.load_ratio:.2f} is in the excessive range")
    elif b.load_ratio is not None and b.load_ratio > 1.3:
        flags += 1
        reasons.append(f"load ratio {b.load_ratio:.2f} is high")
    if b.hrv is not None and b.hrv_lo is not None and b.hrv < b.hrv_lo:
        flags += 1
        reasons.append(f"HRV {b.hrv:g} ms is below your {b.hrv_lo:g}–{b.hrv_hi:g} ms range")
    if b.rhr7 is not None and b.rhr28 is not None and b.rhr7 - b.rhr28 >= 3:
        flags += 1
        reasons.append(f"resting HR is up {b.rhr7 - b.rhr28:.1f} bpm")
    status, factor = ("Ready", 1.0) if flags == 0 else ("Hold", 0.9) if flags == 1 else ("Back off", 0.75)
    return Readiness(status, factor, flags, reasons or ["load, HRV and resting HR all look normal"])


# ---- races --------------------------------------------------------------------------------------


def upcoming(races: list[RaceIn], today: date) -> list[RaceIn]:
    return sorted((r for r in races if r.day >= today), key=lambda r: r.day)


def target_race(races: list[RaceIn], today: date) -> RaceIn | None:
    up = upcoming(races, today)
    return next((r for r in up if r.priority == "A"), up[0] if up else None)


CATS = {
    "marathon": dict(peak=60, long_max=32, taper=3, cut=[0.75, 0.6, 0.4], share=0.5),
    "half": dict(peak=45, long_max=20, taper=2, cut=[0.75, 0.5], share=0.45),
    "10k": dict(peak=40, long_max=16, taper=1, cut=[0.6], share=0.45),
    "5k": dict(peak=35, long_max=13, taper=1, cut=[0.6], share=0.45),
}


def category(km: float) -> str:
    return "marathon" if km >= 40 else "half" if km >= 20 else "10k" if km >= 9 else "5k"


def race_plan(races: list[RaceIn], b: Baseline) -> list[Week]:
    race = target_race(races, b.today)
    if race is None:
        return []
    n_weeks = (monday(race.day) - b.next_monday).days // 7 + 1
    if n_weeks < 1:
        return []
    cat = category(race.distance_km)
    P = CATS[cat]
    T = b.threshold_s
    rp = race_pace(race, T)
    p = fmt_pace
    key = {
        "Base": "Easy runs plus 6 × 20 s strides and 6 × 10 s hill sprints",
        "Build": {"marathon": f"4 × 8 min at {p(T)}/km (threshold)", "half": f"4 × 8 min at {p(T)}/km (threshold)",
                  "10k": f"5 × 1 km at {p(rp)}/km, 2 min jog", "5k": f"6 × 800 m at {p(rp - 5)}/km, 400 m jog"}[cat],
        "Peak": {"marathon": f"Long run with 2 × 6 km at {p(rp)}/km", "half": f"3 × 4 km at {p(rp)}/km, 2 min jog",
                 "10k": f"4 × 2 km at {p(rp)}/km, 2 min jog", "5k": f"8 × 400 m at {p(rp - 10)}/km, 200 m jog"}[cat],
        "Taper": f"Short sharpener: 4 × 1 km at {p(rp)}/km",
        "Race week": f"Race: {race.name} at {p(rp)}/km. Two short easy runs before it",
    }
    base = max(b.weekly_km, 15)
    peak = max(base, P["peak"])
    vol = base
    long = max(8.0, min(b.longest_km, base * 0.4))
    build_weeks = n_weeks - P["taper"]
    tgt = {"name": race.name, "day": race.day, "km": race.distance_km, "pace": p(rp), "priority": race.priority}
    weeks: list[Week] = []
    for i in range(n_weeks):
        left = n_weeks - i
        easier = False
        if left <= P["taper"]:
            phase = "Race week" if left == 1 else "Taper"
            cut = P["cut"][P["taper"] - left]
            total = race.distance_km + vol * 0.2 if left == 1 else vol * cut
            lr = race.distance_km if left == 1 else long * cut
        else:
            phase = "Peak" if left <= P["taper"] + 3 else "Base" if build_weeks > 8 and i < build_weeks * 0.35 else "Build"
            easier = i % 4 == 3
            if not easier and i > 0:
                vol = min(peak, vol * 1.08)
                long = min(P["long_max"], long + 1.5, max(vol * P["share"], long))
            total = vol * 0.8 if easier else vol
            lr = long * 0.75 if easier else long
        weeks.append(Week(b.next_monday + timedelta(weeks=i), left, phase, half(total), half(lr), key[phase], easier,
                          target=tgt))

    for r in upcoming(races, b.today):
        if r is race:
            continue
        idx = next((i for i, w in enumerate(weeks) if w.week <= r.day < w.week + 7 * DAY), None)
        if idx is None or weeks[idx].phase == "Race week":
            continue
        w = weeks[idx]
        pri = "C" if r.priority == "C" else "B"
        w.race_in_week = {"name": r.name, "day": r.day, "km": r.distance_km, "priority": pri, "pace": p(race_pace(r, T))}
        if pri == "B":
            w.total_km, w.long_km = half(w.total_km * 0.8), half(r.distance_km)
            w.key = f"B race: {r.name}, {r.distance_km:g} km at {p(race_pace(r, T))}/km. Mini-taper, no other hard session"
            if idx + 1 < len(weeks) and weeks[idx + 1].phase not in ("Taper", "Race week"):
                nx = weeks[idx + 1]
                nx.total_km = half(nx.total_km * 0.9)
                nx.key = f"Easy for the first 3 days after the B race, then: {nx.key}"
        else:
            w.key = f"C race: {r.name}, {r.distance_km:g} km at {p(race_pace(r, T))}/km as this week's hard session"
    return weeks


# ---- weekly sessions ----------------------------------------------------------------------------


def _strength(d: date) -> Session:
    return Session(d, "Strength", detail="45 min, legs and core (single-leg work, calves, hips). Lighter in race weeks")


def _lay(b: Baseline, total: float, long: float, quality: Session | None, *, strides: bool = False,
         runs: int | None = None, long_finish: str | None = None) -> list[Session]:
    T = b.threshold_s
    easy = easy_range(T)
    total, long = half(total), half(long)
    runs = runs or (4 if total >= 34 else 3)
    n_easy = runs - 1 - (1 if quality else 0)
    easy_km = half(max(5, (total - long - (quality.km if quality else 0)) / max(1, n_easy)))
    days = [1, 3, 4, 6] if runs == 4 else [1, 3, 6]
    items: list[Session] = []
    for i in range(n_easy):
        s = i == 0 and strides
        items.append(Session(b.next_monday, "Easy run + strides" if s else "Easy run", easy_km, easy,
                             "Relaxed; finish with 6 × 20 s strides" if s else "Conversational effort, keep heart rate low"))
    if quality:
        items.insert(1, quality)
    items.append(Session(b.next_monday, "Long run", long, easy,
                         f"Easy, {long_finish}" if long_finish else "Easy and steady, take water if over 90 min"))
    for s, d in zip(items, days, strict=False):
        s.day = b.next_monday + timedelta(days=d)
    return [_strength(b.next_monday), *items]


def next_week(b: Baseline, weeks: list[Week], r: Readiness) -> list[Session]:
    T = b.threshold_s
    easy = easy_range(T)
    k = r.factor
    if not weeks:  # no race: keep fitness
        total = max(b.weekly_km, 18) * k
        q = Session(b.next_monday, "Threshold run", half(clamp(total * 0.3, 8, 12)), f"{fmt_pace(T)} /km",
                    f"2 km easy, 3 × 8 min at {fmt_pace(T)}/km with 2 min jog, then easy to finish")
        return _lay(b, total, clamp(total * 0.4, 8, total * 0.45), q)

    w = weeks[0]
    nm = b.next_monday
    if w.phase == "Race week":
        t = w.target or {}
        rp, race_day = t["pace"], t["day"]
        before = [
            Session(race_day - 5 * DAY, "Easy run + strides", 6, easy, "6 × 20 s strides at the end, nothing harder"),
            Session(race_day - 3 * DAY, "Race-pace sharpener", 6, f"{rp} /km", f"2 km easy, 3 × 1 km at {rp}/km, 1 km easy"),
        ]
        race_s = Session(race_day, f"Race: {t['name']}", t["km"], f"{rp} /km",
                         "Start a little slower than race pace for the first 3 km, then settle in")
        return [x for x in before if x.day >= nm] + [race_s]
    if w.race_in_week:
        x = w.race_in_week
        rd: date = x["day"]
        out: list[Session] = [_strength(nm)]
        if x["priority"] == "B":
            for off, s in ((5, Session(rd, "Easy run + strides", 6, easy, "6 × 20 s strides at the end")),
                           (3, Session(rd, "Race-pace sharpener", 6, f"{x['pace']} /km",
                                       f"2 km easy, 3 × 1 km at {x['pace']}/km, 1 km easy"))):
                s.day = max(nm, rd - timedelta(days=off))
                if s.day < rd and all(o.day != s.day for o in out[1:]):
                    out.append(s)
            out.append(Session(rd, f"B race: {x['name']}", x["km"], f"{x['pace']} /km", "Tune-up: race it, then easy for 3 days"))
        else:
            out.append(Session(max(nm, rd - 3 * DAY), "Easy run", half(clamp(w.total_km * 0.25, 5, 10)), easy,
                               "Conversational effort"))
            race_s = Session(rd, f"C race: {x['name']}", x["km"], f"{x['pace']} /km",
                             "Training race: this is the week's hard session, no taper")
            out.append(race_s)
            if rd.weekday() < 6:
                out.append(Session(nm + 6 * DAY, "Long run", half(w.long_km * k), easy, "Easy and steady"))
            else:
                race_s.detail += "; add 3–5 km easy afterwards"
        return sorted(out, key=lambda s: s.day)

    kk = 1.0 if w.easier else k
    total, long = w.total_km * kk, w.long_km * kk
    q = None
    if w.phase != "Base":
        label = {"Taper": "Sharpener", "Peak": "Race-specific session"}.get(w.phase, "Key session")
        q = Session(nm, label, half(clamp(total * 0.25, 8, 14)), "", w.key)
    finish = w.key.replace("Long run with ", "") if w.phase == "Peak" and w.key.startswith("Long run with") else None
    return _lay(b, total, long, q, strides=w.phase == "Base", runs=3 if w.phase == "Taper" else None, long_finish=finish)


def this_week(runs: list[Run], b: Baseline, races: list[RaceIn], r: Readiness,
              done: list[Run] | None = None) -> tuple[list[Session], float, float]:
    """Returns (sessions Monday..Sunday, done_km, target_km). `done` = all activities this week
    (any sport) for display; `runs` drives the maths."""
    today, T = b.today, b.threshold_s
    easy = easy_range(T)
    mon = monday(today)
    days = [mon + timedelta(days=i) for i in range(7)]
    race = target_race(races, today)
    race_soon = race is not None and (race.day - today).days <= 14
    target = half(max(b.weekly_km, 18) * r.factor * (0.85 if race_soon else 1))
    wk_runs = [x for x in runs if x.type == "Run" and x.km and mon <= x.day <= days[-1]]
    done_km = round(sum(x.km for x in wk_runs), 1)
    out: list[Session] = []
    for a in done or wk_runs:
        if mon <= a.day <= days[-1]:
            out.append(Session(a.day, a.sport, round(a.km, 2) if a.km else 0, f"{fmt_pace(a.pace_s)} /km" if a.pace_s else "",
                               f"{a.duration_s // 60} min" + (f" · avg HR {a.avg_hr}" if a.avg_hr else "") if a.duration_s else "",
                               status="Done"))
    ran_today = any(x.day == today for x in wk_runs)
    free = [d for d in days if d > today or (d == today and not ran_today)]
    left = max(0.0, target - done_km)
    plan: list[Session] = []
    in_week = next((x for x in upcoming(races, today) if x.day <= days[-1]), None)
    hard_done = any(x.pace_s and x.pace_s < T * 1.1 for x in wk_runs)
    long_done = any(x.km >= target * 0.35 for x in wk_runs)
    if in_week:
        label = "Race" if in_week is race else f"{in_week.priority} race"
        rp = fmt_pace(race_pace(in_week, T))
        plan.append(Session(in_week.day, f"{label}: {in_week.name}", in_week.distance_km, f"{rp} /km",
                            "Training race, this week's hard session" if in_week.priority == "C" else "Race day"))
        left -= in_week.distance_km
        if in_week.priority != "C":
            before = [d for d in free if d < in_week.day]
            if len(before) >= 2:
                plan.append(Session(before[-2], "Easy run + strides", 6, easy, "Short and relaxed, 6 × 20 s strides"))
            return _fill_rest(out + plan, days, today), done_km, target
        hard_done = True
    sunday = days[-1]
    if not long_done and sunday in free and all(p.day != sunday for p in plan) and left >= 8:
        km = half(min(left, max(10, target * 0.4)))
        plan.append(Session(sunday, "Long run", km, easy, "Easy and steady"))
        left -= km
    mid = [d for d in free if d != sunday and all(p.day != d for p in plan)]
    if not hard_done and len(mid) >= 2 and left >= 6:
        km = half(min(left, 9))
        if race_soon and race:
            rp = fmt_pace(race_pace(race, T))
            plan.append(Session(mid[0], "Race-pace session", km, f"{rp} /km", f"2 km easy, 3 × 2 km at {rp}/km, 2 min jog, 1 km easy"))
        else:
            plan.append(Session(mid[0], "Threshold run", km, f"{fmt_pace(T)} /km",
                                f"2 km easy, 3 × 8 min at {fmt_pace(T)}/km with 2 min jog, then easy"))
        left -= km
    rest = [d for d in free if all(p.day != d for p in plan) and (not mid or d != mid[0])]
    if left >= 4 and rest:
        plan.append(Session(rest[-1], "Easy run", half(min(left, 12)), easy, "Conversational effort"))
    return _fill_rest(out + plan, days, today), done_km, target


def _fill_rest(items: list[Session], days: list[date], today: date) -> list[Session]:
    for d in days:
        if all(s.day != d for s in items):
            items.append(Session(d, "Rest", status="Rest", detail="Nothing recorded" if d < today else "Rest or an easy walk"))
    return sorted(items, key=lambda s: (s.day, s.status != "Done"))


def road(runs: list[Run], weeks: list[Week], b: Baseline, past_weeks: int = 8) -> list[dict]:
    out = []
    for i in range(past_weeks, 0, -1):
        ws = b.next_monday - timedelta(weeks=i)
        wk = [r for r in runs if r.type == "Run" and r.km and ws <= r.day < ws + 7 * DAY]
        out.append({"week": ws, "kind": "Actual", "phase": "This week so far" if i == 1 else "Actual",
                    "total_km": round(sum(r.km for r in wk), 1), "long_km": round(max((r.km for r in wk), default=0), 1)})
    out += [{"week": w.week, "kind": "Planned", "phase": w.phase + (" (easier week)" if w.easier else ""),
             "total_km": w.total_km, "long_km": w.long_km} for w in weeks]
    return out


def to_dict(x) -> dict:
    return asdict(x)
