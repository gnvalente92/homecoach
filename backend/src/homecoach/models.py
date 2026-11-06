"""Provider-neutral data model. Every provider maps its data into these tables."""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
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


class CheckIn(Base):
    """Something the athlete told the coach: how they feel, niggles, travel, schedule."""

    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoachSuggestion(Base):
    """A change to one day of the plan proposed by the coach. Only 'accepted' ones alter the plan."""

    __tablename__ = "coach_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    action: Mapped[str] = mapped_column(String(10))  # replace | shorten | skip | move
    session: Mapped[str | None] = mapped_column(String(80))
    km: Mapped[float | None] = mapped_column(Float)
    pace: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)
    move_to: Mapped[date | None] = mapped_column(Date)
    duration_min: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="chat")  # chat | review
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending | accepted | dismissed | superseded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoachNote(Base):
    __tablename__ = "coach_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10))  # review | chat
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RaceInsight(Base):
    """The coach's reading of the race outlook (one per race per day; regenerated on demand)."""

    __tablename__ = "race_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_id: Mapped[int] = mapped_column(Integer, ForeignKey("races.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    text: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrainingGoal(Base):
    """A recurring calendar goal, independent of the coach's running-volume plan."""

    __tablename__ = "training_goals"
    __table_args__ = (UniqueConstraint("period", "sport", "metric", name="uq_training_goal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String(10))
    sport: Mapped[str] = mapped_column(String(20))
    metric: Mapped[str] = mapped_column(String(20))
    target: Mapped[float] = mapped_column(Float)


class NutritionProfile(Base):
    """Body data for nutrition targets (one row). Filled from the watch provider unless edited by hand."""

    __tablename__ = "nutrition_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    height_cm: Mapped[float | None] = mapped_column(Float)
    birth_date: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(10))
    goal: Mapped[str] = mapped_column(String(10), default="maintain")
    protein_g_per_kg: Mapped[float] = mapped_column(Float, default=1.8)
    follow_provider: Mapped[bool] = mapped_column(Boolean, default=True)  # keep weight etc. in sync with the watch
    source: Mapped[str | None] = mapped_column(String(20))  # coros | manual
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FoodEntry(Base):
    """Something eaten, with estimated energy and macros (from the food chat or typed in)."""

    __tablename__ = "food_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    meal: Mapped[str] = mapped_column(String(10))  # breakfast | lunch | dinner | snack
    name: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[str | None] = mapped_column(String(60))
    kcal: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float] = mapped_column(Float)
    carbs_g: Mapped[float] = mapped_column(Float)
    fat_g: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(10), default="manual")  # ai | manual
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
