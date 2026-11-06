"""Coach: an LLM on top of the rule engine.

The rules decide the backbone (phases, volume, taper). The coach reads how the training is actually
going (recent weeks, this week so far, readiness, check-ins) and proposes changes to specific days.
Every proposal goes through engine.check_adjustment (volume and long-run limits). Rejected ones are sent
back to the model to revise. Proposals are stored as 'pending' and only change the plan once accepted.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from homecoach import llm
from homecoach.config import Settings
from homecoach.llm import Tool, ToolRejected
from homecoach.models import Activity, CheckIn, CoachNote, CoachSuggestion
from homecoach.plan import engine as E

ADJUST_SCHEMA = {
    "type": "object",
    "properties": {
        "day": {"type": "string", "description": "YYYY-MM-DD of the planned day to change"},
        "action": {"type": "string", "enum": ["replace", "shorten", "skip", "move", "strength"]},
        "duration_min": {
            "type": "integer",
            "description": "strength: 1–60 minutes to add/update, 0 to remove; replace with session 'Strength': 15–60",
        },
        "session": {
            "type": "string",
            "description": (
                "replace: session name, e.g. 'Easy run', 'Tempo run', 'Long run', or 'Strength' to swap the run for gym"
            ),
        },
        "km": {"type": "number", "description": "replace/shorten: distance in km"},
        "pace": {"type": "string", "description": "replace: target pace, e.g. '5:54–6:17 /km'"},
        "detail": {
            "type": "string",
            "description": "Workout description; for strength, include exercises and easy-to-follow instructions",
        },
        "move_to": {"type": "string", "description": "move: new YYYY-MM-DD in the same week"},
        "reason": {"type": "string", "description": "One sentence the athlete will read: why this change"},
    },
    "required": ["day", "action", "reason"],
}


def _fmt_sessions(items: list[dict]) -> str:
    return (
        "\n".join(
            f"- {x['day']} ({date.fromisoformat(str(x['day'])):%a}) [{x['status']}{', adjusted' if x.get('adjusted') else ''}] "
            f"{x['session']}{f' {x["km"]} km' if x.get('km') else ''}{f' @ {x["pace"]}' if x.get('pace') else ''}"
            f"{f' {x["duration_min"]} min' if x.get('duration_min') else ''}"
            f"{f' — {x["detail"]}' if x.get('detail') else ''}"
            for x in items
        )
        or "- (nothing)"
    )


def context(db: Db, plan: dict, today: date) -> str:
    """Everything the coach should know, as compact text."""
    r, b, tr = plan["readiness"], plan["baseline"], plan["target_race"]
    weeks = [w for w in plan["road"] if w["kind"] == "Actual"]
    recent = db.scalars(select(Activity).where(Activity.local_date >= today - timedelta(days=14)).order_by(Activity.start_time))
    acts = (
        "\n".join(
            f"- {a.local_date} {a.type} {f'{a.km} km' if a.km else ''} {f'{a.duration_s // 60} min' if a.duration_s else ''}"
            f"{f' pace {E.fmt_pace(a.pace_s)}/km' if a.pace_s and a.type == 'Run' else ''}{f' HR {a.avg_hr}' if a.avg_hr else ''}"
            for a in recent
        )
        or "- (none)"
    )
    notes = db.scalars(select(CheckIn).where(CheckIn.day >= today - timedelta(days=21)).order_by(CheckIn.created_at))
    checkins = "\n".join(f"- {c.day}: {c.text}" for c in notes) or "- (none)"
    first = (plan["race_plan"] or [None])[0]
    race = (
        f"{tr['name']} on {tr['day']} ({tr['distance_km']} km, priority {tr['priority']}"
        f"{', goal ' + str(tr['goal_time_s'] // 60) + ' min' if tr.get('goal_time_s') else ''}). "
        f"Next week is '{first['phase']}', {first['weeks_to_go']} week(s) to go."
        if tr and first
        else "No race in the calendar: the plan just maintains fitness."
    )
    contrib = "; ".join(f"{c['name']} {c['value']} (normal {c['baseline']})" for c in r.get("contributors", []))
    return f"""Today: {today.isoformat()} ({today:%A}).
Target race: {race}
Readiness: {r["status"]} (score {r.get("score")}, {r.get("zone")}). {contrib}. Reasons: {"; ".join(r["reasons"])}.
Threshold pace: {E.fmt_pace(b["threshold_s"])}/km.
Last 4 weeks: {b["weekly_km"]} km/week, {b["runs_per_week"]} runs/week, longest {b["longest_km"]} km.

Weekly running km, oldest to newest (last row = this week so far):
{chr(10).join(f"- week of {w['week']}: {w['total_km']} km, longest {w['long_km']} km" for w in weeks)}

Workouts, last 14 days:
{acts}

THIS WEEK (target ~{plan["this_week"]["target_km"]} km, {plan["this_week"]["done_km"]} km done):
{_fmt_sessions(plan["this_week"]["sessions"])}

NEXT WEEK (rule plan ~{plan.get("next_week_target_km")} km):
{_fmt_sessions(plan["next_week"])}

Athlete check-ins (last 3 weeks):
{checkins}"""


SYSTEM = """You are {name}'s running coach inside a training app. A rule engine already built the plan (phases,
volume, taper) and you only adjust specific days, based on how training is really going.

Look at: whether recent weeks hit the plan, missed or extra runs, long-run progression, readiness and the
athlete's check-ins (soreness, illness, travel, schedule, how sessions felt).

To change the plan, call propose_adjustment once per day you want to change. Pick the action that matches
what should happen:
- skip: no run that day (rest). Not for moving a run: the run is gone.
- move: the run happens on another day of the same week instead (set move_to).
- shorten: same run, fewer km.
- replace: a different run that day (session name and km are required).
- replace with session 'Strength' (duration_min, detail): swap that day's run for a gym session.
- strength: add or update a gym session next to the day's run (duration_min=0 removes a planned one).
One change per day: a newly accepted change for a day replaces the older one. The app enforces limits; if
a call is rejected, read why and propose something smaller or don't change that day. The athlete accepts
or dismisses each change. Don't re-propose changes that are already marked adjusted.

Review strength training as well as running. Use action='strength' to add or update a strength session on
a date, with duration_min and a concrete, conservative session description (exercises and simple instructions).
This keeps any run on that day; to do gym instead of the run, use replace with session 'Strength'.
Use duration_min=0 to remove a planned strength session. Consider recorded
strength workouts, experience, equipment mentioned in check-ins, soreness, recovery and proximity to races.
Ask about missing experience or equipment rather than assuming heavy weights or advanced exercises.
The app limits suggestions to two strength sessions per week and 60 minutes per session, and rejects race-day
strength additions. These are application limits, not a personalised prescription. Do not prescribe maximal
lifts or treat pain. Reduce or omit strength when the check-ins or recovery signals call for it.

Be conservative: with pain or illness, reduce or rest and suggest seeing a professional if it persists.
Never increase the week beyond the plan. When nothing needs changing, say so and don't call the tool.

Finish with a short message to the athlete (max 4 sentences, plain words, no headings): what you noticed
and what you changed, if anything.

{context}"""

REVIEW_PROMPT = "It's the end of the week. Review how this week went against the plan and adjust next week if needed."


def run(s: Settings, db: Db, plan: dict, today: date, messages: list[dict[str, str]], source: str, client=None) -> dict[str, Any]:
    weeks = {
        "this": ([_to_session(x) for x in plan["this_week"]["sessions"]], plan["this_week"]["target_km"]),
        "next": ([_to_session(x) for x in plan["next_week"]], plan.get("next_week_target_km") or 0),
    }
    longest = plan["baseline"]["longest_km"]
    created: list[CoachSuggestion] = []

    def propose(args: dict) -> str:
        try:
            day = date.fromisoformat(str(args.get("day")))
            move_to = date.fromisoformat(str(args["move_to"])) if args.get("move_to") else None
        except ValueError as e:
            raise ToolRejected("dates must be YYYY-MM-DD") from e
        next_monday = E.next_monday(today)
        if not E.monday(today) <= day < next_monday + timedelta(days=7):
            raise ToolRejected("you can only change days in this week or next week")
        which = "this" if day < next_monday else "next"
        a = E.Adjustment(
            day,
            str(args.get("action")),
            args.get("session"),
            _num(args.get("km")),
            args.get("pace"),
            args.get("detail"),
            move_to,
            str(args.get("reason") or "").strip(),
            args.get("duration_min"),
        )
        if not a.reason:
            raise ToolRejected("give a one-sentence reason")
        items, target = weeks[which]
        why = E.check_adjustment(a, items, target, longest, today)
        if why:
            raise ToolRejected(why)
        weeks[which] = (E.apply_adjustments(items, [a]), target)  # later proposals see this one
        row = CoachSuggestion(
            day=a.day,
            action=a.action,
            session=a.session,
            km=a.km,
            pace=a.pace,
            detail=a.detail,
            move_to=a.move_to,
            duration_min=a.duration_min,
            reason=a.reason,
            source=source,
        )
        db.add(row)
        created.append(row)
        return "Saved as a suggestion for the athlete to accept or dismiss."

    tool = Tool(
        "propose_adjustment", "Propose a change to one planned day (the athlete accepts or dismisses it).", ADJUST_SCHEMA, propose
    )
    system = SYSTEM.format(name=s.athlete_name, context=context(db, plan, today))
    res = llm.run(s, system, messages, [tool], max_rounds=10, client=client)
    db.add(CoachNote(kind=source, text=res.text))
    db.flush()
    return {"reply": res.text, "suggestions": [suggestion_out(x) for x in created]}


def _num(x: Any) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def _to_session(x: dict) -> E.Session:
    return E.Session(
        date.fromisoformat(str(x["day"])),
        x["session"],
        x.get("km") or 0,
        x.get("pace") or "",
        x.get("detail") or "",
        x.get("status") or "Planned",
        x.get("adjusted", False),
        x.get("reason", ""),
        x.get("duration_min"),
    )


def suggestion_out(x: CoachSuggestion) -> dict:
    return {
        "id": x.id,
        "day": x.day,
        "action": x.action,
        "session": x.session,
        "km": x.km,
        "pace": x.pace,
        "detail": x.detail,
        "move_to": x.move_to,
        "duration_min": x.duration_min,
        "reason": x.reason,
        "source": x.source,
        "status": x.status,
        "created_at": x.created_at,
    }
