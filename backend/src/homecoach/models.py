"""Provider-neutral data model. Every provider maps its data into these tables."""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from homecoach.db import Base


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # "<provider>:<external id>"
    provider: Mapped[str] = mapped_column(String(20), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    sport: Mapped[str] = mapped_column(String(64))  # provider's own sport name
    type: Mapped[str] = mapped_column(String(20), index=True)  # Run | Strength | Walk | Ride | Swim | Other
    name: Mapped[str | None] = mapped_column(String(200))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    local_date: Mapped[date] = mapped_column(Date, index=True)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    distance_m: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[int | None] = mapped_column(Integer)
    max_hr: Mapped[int | None] = mapped_column(Integer)
    calories: Mapped[int | None] = mapped_column(Integer)
    training_load: Mapped[float | None] = mapped_column(Float)
    fit_path: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def km(self) -> float | None:
        return round(self.distance_m / 1000, 2) if self.distance_m else None

    @property
    def pace_s(self) -> float | None:
        if self.distance_m and self.duration_s and self.type in ("Run", "Walk"):
            return self.duration_s / (self.distance_m / 1000)
        return None


class DailyMetric(Base):
    """One row per day. Columns are filled by whichever source has them; missing stays NULL."""

    __tablename__ = "daily_metrics"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    steps: Mapped[int | None] = mapped_column(Integer)
    active_kcal: Mapped[int | None] = mapped_column(Integer)
    exercise_min: Mapped[int | None] = mapped_column(Integer)
    stress_avg: Mapped[int | None] = mapped_column(Integer)
    resting_hr: Mapped[int | None] = mapped_column(Integer)
    hrv_avg: Mapped[float | None] = mapped_column(Float)
    hrv_lo: Mapped[float | None] = mapped_column(Float)
    hrv_hi: Mapped[float | None] = mapped_column(Float)
    training_load: Mapped[float | None] = mapped_column(Float)
    load_short: Mapped[float | None] = mapped_column(Float)
    load_long: Mapped[float | None] = mapped_column(Float)
    load_ratio: Mapped[float | None] = mapped_column(Float)
    sleep_min: Mapped[int | None] = mapped_column(Integer)
    deep_min: Mapped[int | None] = mapped_column(Integer)
    light_min: Mapped[int | None] = mapped_column(Integer)
    rem_min: Mapped[int | None] = mapped_column(Integer)
    awake_min: Mapped[int | None] = mapped_column(Integer)
    sleep_score: Mapped[int | None] = mapped_column(Integer)
    sleep_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FitnessSnapshot(Base):
    __tablename__ = "fitness_snapshots"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    vo2max: Mapped[float | None] = mapped_column(Float)
    threshold_pace_s: Mapped[int | None] = mapped_column(Integer)  # seconds per km
    running_level: Mapped[float | None] = mapped_column(Float)
    predictions: Mapped[dict | None] = mapped_column(JSON)  # {"5k": seconds, ...}


class Race(Base):
    __tablename__ = "races"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    day: Mapped[date] = mapped_column(Date, index=True)
    distance_km: Mapped[float] = mapped_column(Float)
    goal_time_s: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(1), default="A")  # A | B | C
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncState(Base):
    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
