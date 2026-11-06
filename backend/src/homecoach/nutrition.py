"""Nutrition service: body profile, the food log and daily targets from training (database side).

The rules live in plan/nutrition.py. Here: which workouts count for which day (recorded activities for past
days and today, the training plan for what's still ahead), usual steps, races, and what was eaten.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from homecoach.models import Activity, DailyMetric, FoodEntry, NutritionProfile, Race
from homecoach.plan import nutrition as N

REQUIRED = ("weight_kg", "height_cm", "birth_date", "sex")
KIND = {"Run": "run", "Strength": "strength", "Ride": "ride", "Swim": "swim", "Walk": "walk"}
HARD_WORDS = ("key", "tempo", "interval", "threshold", "race pace", "race-pace", "hill", "fartlek")


def get_profile(db: Db) -> NutritionProfile | None:
    return db.get(NutritionProfile, 1)


def missing(row: NutritionProfile | None) -> list[str]:
    return [f for f in REQUIRED if row is None or getattr(row, f) in (None, "")]


def to_engine(row: NutritionProfile) -> N.Profile:
    return N.Profile(row.weight_kg, row.height_cm, row.birth_date, row.sex, row.goal or "maintain", row.protein_g_per_kg or 1.8)


def update_from_provider(db: Db, data: dict, source: str = "coros") -> bool:
    """Store body data from the watch. Hand-edited values win unless the profile follows the provider."""
    fields = {k: v for k, v in data.items() if k in REQUIRED and v not in (None, "")}
    if not fields:
        return False
    row = get_profile(db)
    if row is None:
        row = NutritionProfile(id=1, goal="maintain", protein_g_per_kg=1.8, follow_provider=True)
        db.add(row)
    elif not row.follow_provider:
        return False
    changed = False
    for k, v in fields.items():
        if getattr(row, k) != v:
            setattr(row, k, v)
            changed = True
    if changed or row.source is None:
        row.source = source
    db.flush()
    return changed


def profile_out(row: NutritionProfile | None) -> dict:
    if row is None:
        return {"missing": list(REQUIRED)}
    return {
        "weight_kg": row.weight_kg,
        "height_cm": row.height_cm,
        "birth_date": row.birth_date,
        "sex": row.sex,
        "goal": row.goal,
        "protein_g_per_kg": row.protein_g_per_kg,
        "follow_provider": row.follow_provider,
        "source": row.source,
        "updated_at": row.updated_at,
        "missing": missing(row),
    }


def _activity_workout(a: Activity) -> N.Workout:
    minutes = (a.duration_s or 0) / 60
    km = (a.distance_m or 0) / 1000
    return N.Workout(
        kind=KIND.get(a.type, "other"),
        minutes=minutes,
        km=km,
        kcal=a.calories,
        long=a.type == "Run" and (km >= 16 or minutes >= 90),
        name=a.name or a.sport or a.type,
    )


def _planned_workout(s) -> N.Workout | None:
    name = s.session or ""
    low = name.lower()
    if s.status != "Planned" or "race" in low.split(":")[0]:  # races are counted from the race calendar
        return None
    if name == "Strength":
        return N.Workout("strength", minutes=s.duration_min or 45, planned=True, name=name)
    if not s.km:
        return None
    return N.Workout("run", km=s.km, hard=any(w in low for w in HARD_WORDS), long="long" in low, planned=True, name=name)


def workouts_by_day(db: Db, plan: dict, start: date, end: date, today: date) -> dict[date, list[N.Workout]]:
    out: dict[date, list[N.Workout]] = {start + timedelta(days=i): [] for i in range((end - start).days + 1)}
    acts = db.scalars(select(Activity).where(Activity.local_date >= start, Activity.local_date <= min(end, today)))
    for a in acts:
        out[a.local_date].append(_activity_workout(a))
    for s in [*plan["this_week"]["sessions"], *plan["next_week"]]:
        if today <= s.day <= end and s.day >= start and (w := _planned_workout(s)):
            out[s.day].append(w)
    return out


def avg_steps(db: Db, today: date) -> float | None:
    v = db.scalar(
        select(func.avg(DailyMetric.steps)).where(
            DailyMetric.day >= today - timedelta(days=28), DailyMetric.day < today, DailyMetric.steps.is_not(None)
        )
    )
    return float(v) if v else None


def eaten(db: Db, start: date, end: date) -> dict[date, dict]:
    rows = db.execute(
        select(
            FoodEntry.day,
            func.sum(FoodEntry.kcal),
            func.sum(FoodEntry.protein_g),
            func.sum(FoodEntry.carbs_g),
            func.sum(FoodEntry.fat_g),
            func.count(),
        )
        .where(FoodEntry.day >= start, FoodEntry.day <= end)
        .group_by(FoodEntry.day)
    )
    return {
        d: {"kcal": round(k or 0), "protein_g": round(p or 0), "carbs_g": round(c or 0), "fat_g": round(f or 0), "entries": n}
        for d, k, p, c, f, n in rows
    }


def week_targets(db: Db, plan: dict, start: date, end: date, today: date) -> list[dict]:
    row = get_profile(db)
    if missing(row):
        return []
    prof = to_engine(row)
    races = [N.RaceDay(r.day, r.distance_km, r.priority, r.name) for r in db.scalars(select(Race).where(Race.day >= start))]
    race_on = {r.day: r for r in races}
    steps = avg_steps(db, today)
    food = eaten(db, start, end)
    out = []
    for d, ws in workouts_by_day(db, plan, start, end, today).items():
        t = N.day_targets(prof, d, ws, races, steps)
        out.append(
            {
                **t.__dict__,
                "sessions": [
                    {"name": w.name, "kind": w.kind, "planned": w.planned, "km": round(w.km, 1), "minutes": round(w.minutes)}
                    for w in ws
                    if w.kind != "walk"
                ]
                + (
                    [
                        {
                            "name": f"Race: {race_on[d].name}",
                            "kind": "race",
                            "planned": d >= today,
                            "km": round(race_on[d].distance_km, 1),
                            "minutes": 0,
                        }
                    ]
                    if d in race_on
                    else []
                ),
                "eaten": food.get(d),
            }
        )
    return out
