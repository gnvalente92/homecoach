"""Seed a database with synthetic demo data (for local UI development, screenshots and the demo video).

Usage: HC_DATABASE_URL=sqlite:///demo.db HC_ATHLETE_NAME=Alex python scripts/seed_demo.py

Everything here is made up: "Alex", a recreational runner building towards a half marathon, with a
tune-up 10K and a parkrun on the way.
"""

import random
from datetime import UTC, date, datetime, timedelta

from homecoach.collector.sync import upsert_activity, upsert_daily, upsert_fitness
from homecoach.db import Base, get_engine, session_scope
from homecoach.models import CheckIn, FoodEntry, NutritionProfile, Race
from homecoach.providers.base import ActivityRecord, DailyRecord, FitnessRecord

random.seed(7)
today = date.today()
DAYS = 400
Base.metadata.create_all(get_engine())


def run(s, d, i, km, pace, name="Morning Run"):
    upsert_activity(
        s,
        ActivityRecord(
            "demo",
            f"run{i}",
            "Outdoor Run",
            datetime(d.year, d.month, d.day, 7, tzinfo=UTC),
            d,
            duration_s=int(km * pace),
            distance_m=round(km, 2) * 1000,
            avg_hr=int(138 + (360 - pace) / 3.5 + random.uniform(-4, 4)),
            calories=int(km * 68),
            name=name,
        ),
    )


with session_scope() as s:
    for i in range(DAYS):
        d = today - timedelta(days=i)
        wd = d.weekday()
        build = max(0.0, 1 - i / 180)  # volume and speed grow over the last 6 months
        if i > 0 and (wd in (1, 3, 6) or (wd == 5 and random.random() > 0.5)) and random.random() > 0.06:
            if wd == 6:
                km, pace, name = 11 + 7 * build + random.uniform(-1, 1.5), random.uniform(335, 360) - 12 * build, "Long Run"
            elif wd == 3 and random.random() > 0.4:
                km, pace, name = 8 + 2 * build, random.uniform(292, 310) - 8 * build, "Tempo"
            else:
                km, pace, name = random.choice([5, 6, 7, 8]) + random.random(), random.uniform(330, 365), "Easy Run"
            run(s, d, i, km, pace, name)
        if i == 0:
            run(s, d, i, 7.4, 338, "Easy Run")  # today's run is done
        if wd in (0, 2) and random.random() > 0.3:
            upsert_activity(
                s,
                ActivityRecord(
                    "demo",
                    f"str{i}",
                    "Strength",
                    datetime(d.year, d.month, d.day, 18, tzinfo=UTC),
                    d,
                    duration_s=random.randint(2400, 4200),
                    avg_hr=100,
                    name="Gym",
                ),
            )
        if wd == 5 and random.random() > 0.5:
            upsert_activity(
                s,
                ActivityRecord(
                    "demo",
                    f"walk{i}",
                    "Walk",
                    datetime(d.year, d.month, d.day, 10, tzinfo=UTC),
                    d,
                    duration_s=random.randint(3600, 9000),
                    distance_m=random.uniform(4000, 11000),
                    avg_hr=95,
                    name="Coastal Walk",
                ),
            )
        sleep = random.randint(400, 545) if i else 392
        bed = datetime(d.year, d.month, d.day, 22, tzinfo=UTC) - timedelta(days=1) + timedelta(minutes=random.randint(0, 95))
        ratio = round(random.uniform(0.85, 1.25), 2) if i else 1.18
        upsert_daily(
            s,
            DailyRecord(
                d,
                {
                    "steps": random.randint(6000, 19000) if wd != 5 else random.randint(14000, 26000),
                    "active_kcal": random.randint(300, 1300),
                    "exercise_min": random.randint(20, 90),
                    "stress_avg": random.randint(18, 40) + (6 if wd in (0, 1) else 0),
                    "resting_hr": random.randint(50, 55) if i else 51,
                    "hrv_avg": random.randint(36, 46) if i else 37,
                    "hrv_lo": 34,
                    "hrv_hi": 46,
                    "load_short": round(55 * ratio, 1) if i < 31 else None,  # COROS shares the last month of load
                    "load_long": 55 if i < 31 else None,
                    "load_ratio": ratio,
                    "sleep_min": sleep,
                    "deep_min": int(sleep * random.uniform(0.13, 0.21)),
                    "rem_min": int(sleep * random.uniform(0.19, 0.27)),
                    "light_min": int(sleep * 0.55),
                    "awake_min": random.randint(4, 30),
                    "sleep_score": max(55, min(97, int(60 + (sleep - 400) / 4 + random.randint(-6, 8)))) if i else 71,
                    "sleep_start": bed,
                },
            ),
        )
    upsert_fitness(
        s,
        FitnessRecord(
            today,
            vo2max=51,
            threshold_pace_s=286,
            running_level=76,
            predictions={"5k": 1395, "10k": 2930, "half": 6540, "marathon": 13860},
        ),
    )
    sat = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
    races = [
        Race(name="Riverside Parkrun", day=sat, distance_km=5, goal_time_s=23 * 60 + 30, priority="C"),
        Race(name="City 10K", day=sat + timedelta(days=22), distance_km=10, goal_time_s=48 * 60 + 30, priority="B"),
        Race(name="Coastal Half Marathon", day=sat + timedelta(days=71), distance_km=21.0975, goal_time_s=105 * 60, priority="A"),
    ]
    s.add_all(races)
    s.add(
        CheckIn(
            day=today - timedelta(days=1),
            feeling="sore",
            until=today + timedelta(days=1),
            text="Left calf a bit tight after the tempo.",
        )
    )
    s.flush()

    from homecoach.collector.sync import set_state

    set_state(s, "provider:demo", {"last_sync": datetime.now().isoformat(), "last_full": datetime.now().isoformat()})

    # nutrition: Alex's profile and what's been eaten so far today (all made up)
    s.add(
        NutritionProfile(
            id=1,
            weight_kg=68,
            height_cm=176,
            birth_date=date(1993, 5, 20),
            sex="male",
            goal="maintain",
            protein_g_per_kg=1.8,
            follow_provider=True,
            source="demo",
        )
    )
    for meal, name, qty, kcal, p, c, f, src in [
        ("breakfast", "Porridge with banana", "60 g oats, 1 banana", 380, 12, 68, 7, "ai"),
        ("breakfast", "Coffee with milk", "1 mug", 45, 3, 4, 2, "ai"),
        ("lunch", "Chicken and rice bowl", "1 bowl", 640, 42, 78, 16, "ai"),
        ("snack", "Greek yoghurt", "150 g", 110, 15, 6, 3, "manual"),
    ]:
        s.add(FoodEntry(day=today, meal=meal, name=name, quantity=qty, kcal=kcal, protein_g=p, carbs_g=c, fat_g=f, source=src))
print("seeded")
