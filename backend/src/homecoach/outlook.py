"""Race outlook: the rule facts (engine.race_outlook) plus a short interpretation from the local AI.

The model only explains: it gets the computed facts and is asked to say, in plain words, whether the goal
pace looks realistic and what to focus on. It changes nothing.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from homecoach import llm
from homecoach.config import Settings
from homecoach.models import Activity, FitnessSnapshot, Race, RaceInsight
from homecoach.plan import engine as E

SYSTEM = """You are a running coach talking to {name}. Below are facts computed from their watch data about
one race and their goal time. Explain whether the goal pace looks realistic right now, in plain words.

Rules:
- Use only these facts. Don't invent numbers. Times are h:mm:ss or m:ss, paces are min:sec per km.
- The estimate comes from the watch's race prediction when there is one, otherwise from the best recent run.
- "km at goal pace" counts runs whose average pace was at or near goal pace in the last 4 weeks.
- Endurance shape: 100% means weekly volume and long runs are enough for this distance.
- Consider the weeks left: a few weeks can close a gap of 1-3%, rarely more.
- Answer with 3 to 5 sentences, then a line "Focus:" followed by 2 or 3 short bullet points starting with "- ".
- No headings, no preamble, no sign-off.

Facts:
{facts}"""


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


def describe(f: dict) -> str:
    """The facts as short lines for the prompt (and for reading the stored record)."""
    lines = [
        f"Race: {f['race']}, {f['distance_km']:g} km on {f['day']} ({f['weeks_to_go']:g} weeks to go), priority {f['priority']}",
        f"Goal: {_t(f['goal_time_s'])} = {_p(f['goal_pace_s'])}",
    ]
    if f["predicted_s"]:
        lines.append(
            f"Watch prediction for this distance: {_t(f['predicted_s'])} ({_p(f['predicted_s'] / f['distance_km'])}),"
            f" scaled from the {f['predicted_from']} prediction"
        )
    if b := f["best_effort"]:
        lines.append(
            f"Best recent run (8 weeks): {b['km']:g} km in {_t(b['time_s'])} on {b['day']} ({_p(b['pace_s'])}),"
            f" equivalent to {_t(b['equivalent_s'])} for the race"
        )
    if f["gap_s"] is not None:
        sign = "slower" if f["gap_s"] > 0 else "faster"
        lines.append(
            f"Current estimate vs goal: {_t(abs(f['gap_s']))} {sign} ({abs(f['gap_pct']) * 100:.1f}%), verdict: {f['verdict']}"
        )
    if "goal_pace_km_4w" in f:
        lines.append(
            f"Last 4 weeks: {f['runs_4w']} runs, {f['goal_pace_km_4w']:g} km at or near goal pace,"
            f" fastest average pace {_p(f['fastest_pace_4w_s'])}"
        )
    sh = f["shape"]
    lines.append(
        f"Endurance shape: {sh['pct']}% (weekly {sh['weekly_km']:g} of {sh['weekly_target']} km,"
        f" longest run {sh['longest_km']:g} of {sh['long_target']} km)"
    )
    return "\n".join(lines)


def latest(db: Db, race_id: int) -> RaceInsight | None:
    return db.scalar(
        select(RaceInsight).where(RaceInsight.race_id == race_id).order_by(RaceInsight.created_at.desc(), RaceInsight.id.desc())
    )


def interpret(s: Settings, db: Db, race: Race, today: date, force: bool = False, client=None) -> RaceInsight:
    """Today's interpretation for a race (cached per day unless force)."""
    old = latest(db, race.id)
    if old and old.day == today and not force:
        return old
    f = facts_for(db, race, today)
    if not race.goal_time_s:
        raise ValueError("set a goal time for this race first")
    res = llm.run(
        s,
        SYSTEM.format(name=s.athlete_name, facts=describe(f)),
        [{"role": "user", "content": "Am I on track for my goal pace?"}],
        [],
        max_rounds=1,
        client=client,
    )
    row = RaceInsight(race_id=race.id, day=today, text=res.text.strip(), facts=_json(f))
    db.add(row)
    db.flush()
    return row


def _json(x):
    if isinstance(x, dict):
        return {k: _json(v) for k, v in x.items()}
    if isinstance(x, date):
        return x.isoformat()
    return x
