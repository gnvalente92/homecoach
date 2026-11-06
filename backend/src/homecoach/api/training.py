"""Local goals and training history, using provider-neutral activity data."""

import calendar
from datetime import date, datetime, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from homecoach.config import get_settings
from homecoach.db import get_session
from homecoach.models import Activity, TrainingGoal

router = APIRouter(prefix="/api")
Db = Depends(get_session)
Sport = Literal["All", "Run", "Ride", "Swim", "Strength", "Walk", "Other"]
Period = Literal["weekly", "monthly", "yearly"]
Metric = Literal["distance_km", "duration_h", "sessions"]


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def period_bounds(day: date, period: Period) -> tuple[date, date]:
    """Calendar bounds, with an inclusive start and exclusive end; weeks start on Monday."""
    if period == "weekly":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    if period == "monthly":
        start = day.replace(day=1)
        return start, start + timedelta(days=calendar.monthrange(day.year, day.month)[1])
    return date(day.year, 1, 1), date(day.year + 1, 1, 1)


def totals(db: Session, start: date, end: date, sport: Sport = "All") -> dict:
    query = select(
        func.count(),
        func.sum(Activity.distance_m),
        func.sum(Activity.duration_s),
        func.count(Activity.distance_m),
        func.count(Activity.duration_s),
    ).where(Activity.local_date >= start, Activity.local_date < end)
    if sport != "All":
        query = query.where(Activity.type == sport)
    count, metres, seconds, distances, durations = db.execute(query).one()
    return {
        "sessions": count,
        "distance_km": round((metres or 0) / 1000, 3),
        "duration_h": round((seconds or 0) / 3600, 3),
        "missing_distance": count - distances,
        "missing_duration": count - durations,
    }


class GoalBody(BaseModel):
    period: Period
    sport: Sport
    metric: Metric
    target: float = Field(ge=0.01, le=1_000_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def whole_sessions(self) -> "GoalBody":
        if self.metric == "sessions" and not self.target.is_integer():
            raise ValueError("session targets must be whole numbers")
        return self


def goal_out(goal: TrainingGoal) -> dict:
    return {"id": goal.id, "period": goal.period, "sport": goal.sport, "metric": goal.metric, "target": goal.target}


def save_goal(db: Session, goal: TrainingGoal) -> dict:
    db.add(goal)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "a goal for this period, sport and metric already exists; edit that goal") from e
    return goal_out(goal)


@router.get("/goals")
def goals(db: Session = Db) -> list[dict]:
    day = today()
    out = []
    for goal in db.scalars(select(TrainingGoal).order_by(TrainingGoal.id)):
        start, end = period_bounds(day, cast(Period, goal.period))
        measured = totals(db, start, min(end, day + timedelta(days=1)), cast(Sport, goal.sport))
        value = measured[goal.metric]
        missing = measured.get({"distance_km": "missing_distance", "duration_h": "missing_duration"}.get(goal.metric), 0)
        out.append(
            {
                **goal_out(goal),
                "start": start,
                "end": end - timedelta(days=1),
                "value": value,
                "percent": round(100 * value / goal.target, 1),
                "remaining": round(max(0, goal.target - value), 3),
                "days_left": (end - day).days - 1,
                "missing_sessions": missing,
                "complete": value >= goal.target,
            }
        )
    return out


@router.post("/goals", status_code=201)
def create_goal(body: GoalBody, db: Session = Db) -> dict:
    return save_goal(db, TrainingGoal(**body.model_dump()))


@router.put("/goals/{gid}")
def update_goal(gid: int, body: GoalBody, db: Session = Db) -> dict:
    goal = db.get(TrainingGoal, gid)
    if not goal:
        raise HTTPException(404, "goal not found")
    for key, value in body.model_dump().items():
        setattr(goal, key, value)
    return save_goal(db, goal)


@router.delete("/goals/{gid}", status_code=204)
def delete_goal(gid: int, db: Session = Db) -> None:
    goal = db.get(TrainingGoal, gid)
    if not goal:
        raise HTTPException(404, "goal not found")
    db.delete(goal)


@router.get("/training/log")
def training_log(
    sport: Sport = "All",
    start: date | None = None,
    end: date | None = None,
    q: str = Query("", max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Db,
) -> dict:
    end = min(end or today(), today())
    if start and start > end:
        raise HTTPException(422, "start must be on or before end")
    filters = [Activity.local_date <= end]
    if start:
        filters.append(Activity.local_date >= start)
    if sport != "All":
        filters.append(Activity.type == sport)
    if q.strip():
        filters.append(
            or_(Activity.name.icontains(q.strip(), autoescape=True), Activity.sport.icontains(q.strip(), autoescape=True))
        )
    count = db.scalar(select(func.count()).select_from(Activity).where(*filters))
    rows = db.scalars(
        select(Activity).where(*filters).order_by(Activity.start_time.desc(), Activity.id).limit(limit).offset(offset)
    )
    return {
        "total": count,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": a.id,
                "date": a.local_date,
                "type": a.type,
                "name": a.name or a.sport,
                "km": a.km,
                "duration_s": a.duration_s,
                "pace_s": a.pace_s,
                "avg_hr": a.avg_hr,
            }
            for a in rows
        ],
    }


@router.get("/training/monthly")
def monthly(year: int | None = Query(None, ge=1971, le=9998), sport: Sport = "All", db: Session = Db) -> dict:
    day = today()
    year = year or day.year
    if year > day.year:
        raise HTTPException(422, "year must not be in the future")
    # Before the first recorded activity there is no data, which is not the same as a month of zero training.
    first_query = select(func.min(Activity.local_date))
    if sport != "All":
        first_query = first_query.where(Activity.type == sport)
    first = db.scalar(first_query)
    months = []
    for month in range(1, 13 if year < day.year else day.month + 1):
        start = date(year, month, 1)
        _, end = period_bounds(start, "monthly")
        current = month == day.month and year == day.year
        previous_start = date(year - 1, month, 1)
        _, previous_end = period_bounds(previous_start, "monthly")
        if current:
            end = day + timedelta(days=1)
            previous_end = previous_start + timedelta(days=min(day.day, calendar.monthrange(year - 1, month)[1]))
        months.append(
            {
                "month": start,
                "end": end - timedelta(days=1),
                "partial": current,
                "previous_start": previous_start,
                "previous_end": previous_end - timedelta(days=1),
                "current": totals(db, start, end, sport) if first and end > first else None,
                "previous": totals(db, previous_start, previous_end, sport) if first and previous_end > first else None,
            }
        )
    return {"year": year, "sport": sport, "first_activity": first, "months": months}
