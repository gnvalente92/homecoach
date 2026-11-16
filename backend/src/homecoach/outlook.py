"""Race outlook: the rule facts (engine.race_outlook) plus a short written summary from fixed rules."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from homecoach.models import Activity, FitnessSnapshot, Race
from homecoach.plan import engine as E

# Consistent training closes roughly 0.5% of race time a week, rarely more than 3% before a race.
GAIN_PER_WEEK, MAX_GAIN = 0.005, 0.03


def _t(s: float | None) -> str:
    if not s:
        return "unknown"
    s = round(s)
    h, rem = divmod(s, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


def _p(s: float | None) -> str:
    return f"{E.fmt_pace(s)} /km" if s else "unknown"


def latest_predictions(db: Db) -> dict | None:
    snap = db.scalar(select(FitnessSnapshot).where(FitnessSnapshot.predictions.is_not(None)).order_by(FitnessSnapshot.day.desc()))
    return snap.predictions if snap else None


def facts_for(db: Db, race: Race, today: date) -> dict:
    acts = db.scalars(select(Activity).where(Activity.local_date >= today - timedelta(days=70)))
    runs = [E.Run(a.local_date, (a.distance_m or 0) / 1000, a.duration_s, a.type, a.sport, a.avg_hr) for a in acts]
    r = E.RaceIn(race.name, race.day, race.distance_km, race.goal_time_s, race.priority, race.id)
    return E.race_outlook(r, runs, today, latest_predictions(db))


def summary(f: dict) -> dict | None:
    """Plain-language reading of the facts: a few sentences and up to three focus points."""
    if not f["goal_time_s"]:
        return None
    goal, est, weeks = f["goal_time_s"], f["estimate_s"], f["weeks_to_go"]
    if not est:
        return {
            "text": "There's no estimate yet: no watch prediction and no run of 3 km or more in the last 8 weeks "
            f"to compare with your goal of {_t(goal)} ({_p(f['goal_pace_s'])}).",
            "focus": ["Record a run of 3 km or more, or sync a watch race prediction, to get an estimate."],
        }
    gap, pct = f["gap_s"], f["gap_pct"]
    source = "your watch's race prediction" if f["predicted_s"] else "your best recent run"
    lines = [f"Based on {source}, you'd run about {_t(est)} ({_p(f['estimate_pace_s'])}) today."]
    reach = min(MAX_GAIN, GAIN_PER_WEEK * max(weeks, 0))
    if gap == 0:
        lines.append(f"That's exactly your goal of {_t(goal)}: on current form the goal is realistic.")
    elif gap < 0:
        lines.append(f"That's {_t(-gap)} faster than your goal of {_t(goal)}: on current form the goal is realistic.")
    else:
        lines.append(f"That's {_t(gap)} ({pct * 100:.1f}%) slower than your goal of {_t(goal)}.")
        if pct <= reach:
            lines.append(f"With {weeks:g} weeks to go, consistent training usually closes a gap of that size.")
        else:
            lines.append(
                f"That's more than training usually closes in {weeks:g} weeks (about {reach * 100:.1f}%); "
                f"a goal around {_t(est * (1 - reach))} looks more realistic."
            )
    focus: list[str] = []
    sh = f["shape"]
    if weeks <= 2:
        focus.append("Taper: keep runs short and relaxed, sleep well and practise race-day fuelling.")
    else:
        if sh["weekly_km"] < sh["weekly_target"] * 0.9:
            focus.append(f"Build weekly volume gradually towards {sh['weekly_target']} km.")
        if sh["longest_km"] < sh["long_target"] * 0.9:
            focus.append(f"Extend the long run towards {sh['long_target']} km.")
        if gap > 0 and f.get("goal_pace_km_4w", 0) < 5:
            focus.append(f"Add time at goal pace ({_p(f['goal_pace_s'])}): aim for 5 km or more a week within key sessions.")
    if not focus:
        focus.append("Keep it consistent: easy volume plus one quality session a week.")
    return {"text": " ".join(lines), "focus": focus[:3]}
