"""Provider-neutral records. New providers (Garmin, Strava, ...) only need to produce these."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol


def classify_sport(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("run", "jog", "trail", "track", "treadmill")):
        return "Run"
    if any(k in n for k in ("strength", "gym", "weight", "training")):
        return "Strength"
    if any(k in n for k in ("walk", "hike", "hiking")):
        return "Walk"
    if any(k in n for k in ("bike", "cycl", "ride", "biking")):
        return "Ride"
    if "swim" in n:
        return "Swim"
    return "Other"


@dataclass
class ActivityRecord:
    provider: str
    external_id: str
    sport: str
    start_time: datetime  # timezone-aware
    local_date: date
    duration_s: int | None = None
    distance_m: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    calories: int | None = None
    training_load: float | None = None
    name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    type: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = classify_sport(self.sport)

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.external_id}"


@dataclass
class DailyRecord:
    day: date
    values: dict[str, Any]  # column name -> value, only for columns this source knows


@dataclass
class FitnessRecord:
    day: date
    vo2max: float | None = None
    threshold_pace_s: int | None = None
    running_level: float | None = None
    predictions: dict[str, int] | None = None


class Provider(Protocol):
    name: str

    def fetch_activities(self, start: date, end: date) -> list[ActivityRecord]: ...

    def fetch_daily(self, start: date, end: date) -> list[DailyRecord]: ...

    def fetch_fitness(self) -> FitnessRecord | None: ...

    def download_fit(self, activity: ActivityRecord) -> bytes | None: ...
