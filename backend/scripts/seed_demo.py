"""Seed a database with synthetic demo data (for local UI development and screenshots).

Usage: HC_DATABASE_URL=sqlite:///demo.db python scripts/seed_demo.py
"""

import random
from datetime import UTC, date, datetime, timedelta

from homecoach.collector.sync import upsert_activity, upsert_daily, upsert_fitness
from homecoach.db import Base, get_engine, session_scope
from homecoach.models import Race
from homecoach.providers.base import ActivityRecord, DailyRecord, FitnessRecord

random.seed(7)
today = date.today()
Base.metadata.create_all(get_engine())
with session_scope() as s:
    for i in range(180):
        d = today - timedelta(days=i)
        wd = d.weekday()
        if wd in (1, 3, 6) and random.random() > 0.15:
            km = 21 if (wd == 6 and i % 28 == 0) else random.choice([5, 6, 8, 10, 12]) + random.random()
            pace = random.uniform(330, 370) if km > 8 else random.uniform(300, 360)
            upsert_activity(
                s,
                ActivityRecord(
                    "demo",
                    f"run{i}",
                    "Outdoor Run",
                    datetime(d.year, d.month, d.day, 7, tzinfo=UTC),
                    d,
                    duration_s=int(km * pace),
                    distance_m=km * 1000,
                    avg_hr=int(140 + (360 - pace) / 4),
                    name="Morning Run",
                ),
            )
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
                ),
            )
        sleep = random.randint(390, 560)
        upsert_daily(
            s,
            DailyRecord(
                d,
                {
                    "steps": random.randint(3000, 26000),
                    "active_kcal": random.randint(200, 1600),
                    "stress_avg": random.randint(18, 42),
                    "resting_hr": random.randint(51, 58),
                    "hrv_avg": random.randint(34, 46),
                    "hrv_lo": 34,
                    "hrv_hi": 44,
                    "load_short": 50 + 20 * random.random(),
                    "load_long": 55,
                    "load_ratio": round(random.uniform(0.8, 1.3), 2),
                    "sleep_min": sleep,
                    "deep_min": int(sleep * 0.17),
                    "rem_min": int(sleep * 0.23),
                    "light_min": int(sleep * 0.56),
                    "awake_min": random.randint(4, 30),
                    "sleep_score": random.randint(65, 95),
                    "sleep_start": datetime(d.year, d.month, d.day, 22, random.randint(0, 59), tzinfo=UTC) - timedelta(days=1),
                },
            ),
        )
    upsert_fitness(s, FitnessRecord(today, vo2max=52, threshold_pace_s=290, running_level=78))
    s.add(Race(name="Corrida Sporting", day=today + timedelta(days=10), distance_km=10, goal_time_s=49 * 60, priority="A"))
print("seeded")
