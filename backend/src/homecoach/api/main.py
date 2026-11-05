"""FastAPI app: JSON API under /api and the web UI at /."""

from __future__ import annotations

import base64
import secrets
import threading
from collections import defaultdict
from datetime import date, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from homecoach import __version__
from homecoach.config import get_settings
from homecoach.db import get_session
from homecoach.models import Activity, DailyMetric, FitnessSnapshot, Race, SyncState
from homecoach.plan import engine as E

app = FastAPI(title="homecoach", version=__version__)
Db = Depends(get_session)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    s = get_settings()
    if s.auth_user and s.auth_password and request.url.path != "/api/health":
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, s.auth_user) and secrets.compare_digest(pwd, s.auth_password.get_secret_value())
            except ValueError:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="homecoach"'})
    return await call_next(request)


# ---- helpers ------------------------------------------------------------------------------------


def today() -> date:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def parse_time(s: str | None) -> int | None:
    """'49:00' -> 2940, '3:45:00' -> 13500. Two parts under 10 are read as h:mm."""
    if not s:
        return None
    parts = [int(p) for p in s.strip().split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 3600 + parts[1] * 60 if parts[0] < 10 else parts[0] * 60 + parts[1]
    raise ValueError("use h:mm:ss or mm:ss")


def fmt_time(s: int | None) -> str | None:
    if not s:
        return None
    h, rem = divmod(s, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


def since(days: int | None) -> date:
    return today() - timedelta(days=(days or 36500) - 1)


def threshold_s(db: Session) -> float | None:
    s = get_settings()
    if s.threshold_pace:
        return _pace(s.threshold_pace)
    snap = db.scalar(
        select(FitnessSnapshot).where(FitnessSnapshot.threshold_pace_s.is_not(None)).order_by(FitnessSnapshot.day.desc())
    )
    return float(snap.threshold_pace_s) if snap else None


def _pace(s: str) -> float:
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


def runs_from(acts: list[Activity]) -> list[E.Run]:
    return [E.Run(a.local_date, (a.distance_m or 0) / 1000, a.duration_s, a.type, a.sport, a.avg_hr) for a in acts]


def days_from(rows: list[DailyMetric]) -> list[E.Day]:
    return [E.Day(r.day, r.resting_hr, r.hrv_avg, r.hrv_lo, r.hrv_hi, r.load_ratio, r.sleep_min) for r in rows]


def races_from(rows: list[Race]) -> list[E.RaceIn]:
    return [E.RaceIn(r.name, r.day, r.distance_km, r.goal_time_s, r.priority, r.id) for r in rows]


def jsonable(x):
    if isinstance(x, list):
        return [jsonable(i) for i in x]
    if hasattr(x, "__dataclass_fields__"):
        return jsonable(E.to_dict(x))
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    return x


# ---- meta ---------------------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__}


@app.get("/api/config")
def config(db: Session = Db) -> dict:
    s = get_settings()
    states = {st.key: st.value for st in db.scalars(select(SyncState))}
    return {"athlete_name": s.athlete_name, "timezone": s.timezone, "provider": s.provider, "sync": states, "today": today()}


@app.post("/api/sync")
def trigger_sync(full: bool = False) -> dict:
    from homecoach.collector.main import run_once

    threading.Thread(target=run_once, kwargs={"full": full}, daemon=True).start()
    return {"started": True, "full": full}


# ---- data ---------------------------------------------------------------------------------------


@app.get("/api/daily")
def daily(days: int | None = Query(84, ge=7), db: Session = Db) -> list[dict]:
    rows = db.scalars(select(DailyMetric).where(DailyMetric.day >= since(days)).order_by(DailyMetric.day))
    cols = [c.name for c in DailyMetric.__table__.columns if c.name not in ("raw", "updated_at")]
    return [{c: getattr(r, c) for c in cols} for r in rows]


def _activities(db: Session, days: int | None) -> list[Activity]:
    return list(db.scalars(select(Activity).where(Activity.local_date >= since(days)).order_by(Activity.start_time.desc())))


@app.get("/api/activities")
def activities(days: int | None = Query(84, ge=7), db: Session = Db) -> list[dict]:
    return [
        {
            "id": a.id,
            "date": a.local_date,
            "start_time": a.start_time,
            "sport": a.sport,
            "type": a.type,
            "name": a.name,
            "duration_s": a.duration_s,
            "km": a.km,
            "pace_s": a.pace_s,
            "avg_hr": a.avg_hr,
            "calories": a.calories,
            "training_load": a.training_load,
            "has_fit": bool(a.fit_path),
        }
        for a in _activities(db, days)
    ]


@app.get("/api/weekly")
def weekly(days: int | None = Query(84, ge=7), db: Session = Db) -> list[dict]:
    weeks: dict[date, dict] = defaultdict(
        lambda: {"run_km": 0.0, "run_h": 0.0, "strength_h": 0.0, "walk_h": 0.0, "other_h": 0.0, "sessions": 0, "longest_km": 0.0}
    )
    for a in _activities(db, days):
        w = weeks[E.monday(a.local_date)]
        h = (a.duration_s or 0) / 3600
        w["sessions"] += 1
        if a.type == "Run":
            w["run_km"] += a.km or 0
            w["run_h"] += h
            w["longest_km"] = max(w["longest_km"], a.km or 0)
        elif a.type == "Strength":
            w["strength_h"] += h
        elif a.type == "Walk":
            w["walk_h"] += h
        else:
            w["other_h"] += h
    dm = defaultdict(list)
    for r in db.scalars(select(DailyMetric).where(DailyMetric.day >= since(days))):
        dm[E.monday(r.day)].append(r)
    out = []
    for wk in sorted(set(weeks) | set(dm)):
        w = {k: round(v, 1) if isinstance(v, float) else v for k, v in weeks[wk].items()}
        rows = dm.get(wk, [])
        steps = [r.steps for r in rows if r.steps is not None]
        sleep = [r.sleep_min for r in rows if r.sleep_min]
        rhr = [r.resting_hr for r in rows if r.resting_hr]
        out.append(
            {
                "week": wk,
                **w,
                "train_h": round(w["run_h"] + w["strength_h"] + w["walk_h"] + w["other_h"], 1),
                "avg_steps": round(sum(steps) / len(steps)) if steps else None,
                "avg_sleep_h": round(sum(sleep) / len(sleep) / 60, 1) if sleep else None,
                "avg_rhr": round(sum(rhr) / len(rhr), 1) if rhr else None,
            }
        )
    return out


def _period_stats(db: Session, start: date, end: date) -> dict:
    acts = list(db.scalars(select(Activity).where(Activity.local_date >= start, Activity.local_date <= end)))
    runs = [a for a in acts if a.type == "Run"]
    rows = list(db.scalars(select(DailyMetric).where(DailyMetric.day >= start, DailyMetric.day <= end)))

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 1) if xs else None

    sleep = avg([r.sleep_min for r in rows])
    return {
        "run_km": round(sum(a.km or 0 for a in runs), 1),
        "runs": len(runs),
        "sessions": len(acts),
        "train_h": round(sum(a.duration_s or 0 for a in acts) / 3600, 1),
        "avg_steps": avg([r.steps for r in rows]),
        "days_10k": sum(1 for r in rows if (r.steps or 0) >= 10000),
        "avg_sleep_h": round(sleep / 60, 1) if sleep else None,
        "avg_rhr": avg([r.resting_hr for r in rows]),
        "avg_hrv": avg([r.hrv_avg for r in rows]),
    }


@app.get("/api/summary")
def summary(days: int | None = Query(84, ge=7), db: Session = Db) -> dict:
    """Totals for the period, plus the same totals for the period before it (for change indicators)."""
    end = today()
    n = min(days or 36500, 3650)
    cur = _period_stats(db, end - timedelta(days=n - 1), end)
    prev = _period_stats(db, end - timedelta(days=2 * n - 1), end - timedelta(days=n))
    snap = db.scalar(select(FitnessSnapshot).order_by(FitnessSnapshot.day.desc()))
    th = threshold_s(db)
    return {
        "days": days,
        **cur,
        "previous": prev if any(prev[k] for k in ("run_km", "sessions", "avg_steps")) else None,
        "vo2max": snap.vo2max if snap else None,
        "threshold_pace": E.fmt_pace(th) if th else None,
    }


# ---- races --------------------------------------------------------------------------------------


class RaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    date: date
    distance_km: float = Field(gt=0, le=400)
    goal_time: str | None = None
    priority: Literal["A", "B", "C"] = "A"
    notes: str | None = None

    @field_validator("goal_time")
    @classmethod
    def _goal(cls, v: str | None) -> str | None:
        if v:
            parse_time(v)
        return v or None


def race_out(r: Race) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "date": r.day,
        "distance_km": r.distance_km,
        "goal_time": fmt_time(r.goal_time_s),
        "goal_pace": E.fmt_pace(r.goal_time_s / r.distance_km) if r.goal_time_s else None,
        "priority": r.priority,
        "notes": r.notes,
        "days_to_go": (r.day - today()).days,
    }


@app.get("/api/races")
def list_races(include_past: bool = False, db: Session = Db) -> list[dict]:
    q = select(Race).order_by(Race.day)
    if not include_past:
        q = q.where(Race.day >= today())
    return [race_out(r) for r in db.scalars(q)]


@app.post("/api/races", status_code=201)
def create_race(body: RaceBody, db: Session = Db) -> dict:
    r = Race(
        name=body.name,
        day=body.date,
        distance_km=body.distance_km,
        goal_time_s=parse_time(body.goal_time),
        priority=body.priority,
        notes=body.notes,
    )
    db.add(r)
    db.flush()
    return race_out(r)


@app.put("/api/races/{race_id}")
def update_race(race_id: int, body: RaceBody, db: Session = Db) -> dict:
    r = db.get(Race, race_id)
    if not r:
        raise HTTPException(404, "race not found")
    r.name, r.day, r.distance_km = body.name, body.date, body.distance_km
    r.goal_time_s, r.priority, r.notes = parse_time(body.goal_time), body.priority, body.notes
    return race_out(r)


@app.delete("/api/races/{race_id}", status_code=204)
def delete_race(race_id: int, db: Session = Db) -> None:
    r = db.get(Race, race_id)
    if not r:
        raise HTTPException(404, "race not found")
    db.delete(r)


# ---- race assistant -----------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)


@app.get("/api/assistant")
def assistant_status() -> dict:
    s = get_settings()
    return {"enabled": bool(s.anthropic_api_key), "model": s.assistant_model}


@app.post("/api/assistant/chat")
def assistant_chat(body: ChatBody, db: Session = Db) -> dict:
    from homecoach.assistant import AssistantError, chat

    if body.messages[-1].role != "user":
        raise HTTPException(422, "the last message must be from the user")
    races = [race_out(r) for r in db.scalars(select(Race).where(Race.day >= today()).order_by(Race.day))]
    try:
        return jsonable(chat(get_settings(), [m.model_dump() for m in body.messages], today(), races))
    except AssistantError as e:
        raise HTTPException(503, str(e)) from e


# ---- plan ---------------------------------------------------------------------------------------


@app.get("/api/plan")
def plan(db: Session = Db) -> dict:
    t = today()
    acts = list(db.scalars(select(Activity).where(Activity.local_date >= t - timedelta(days=70))))
    rows = list(db.scalars(select(DailyMetric).where(DailyMetric.day >= t - timedelta(days=35)).order_by(DailyMetric.day)))
    races = races_from(list(db.scalars(select(Race).where(Race.day >= t))))
    runs = runs_from(acts)
    b = E.baseline(runs, days_from(rows), t, threshold_s(db))
    r = E.readiness(b)
    weeks = E.race_plan(races, b)
    tw, done_km, target_km = E.this_week(runs, b, races, r, done=runs)
    tr = E.target_race(races, t)
    return jsonable(
        {
            "athlete_name": get_settings().athlete_name,
            "baseline": b,
            "readiness": r,
            "target_race": tr,
            "this_week": {"sessions": tw, "done_km": done_km, "target_km": target_km},
            "next_week": E.next_week(b, weeks, r),
            "race_plan": weeks,
            "road": E.road(runs, weeks, b),
            "shape": {**E.endurance_shape(runs, t, tr.distance_km), "race": tr.name} if tr else None,
        }
    )


@app.get("/api/fitness")
def fitness(days: int | None = Query(84, ge=7), db: Session = Db) -> list[dict]:
    """Fitness (CTL), fatigue (ATL), form (TSB) and ramp rate from daily training load."""
    end = today()
    start = since(min(days or 3650, 3650))
    acts = db.scalars(select(Activity).where(Activity.local_date >= start - timedelta(days=120)))
    load: dict[date, float] = defaultdict(float)
    for a in acts:
        load[a.local_date] += E.activity_load(a.duration_s, a.avg_hr, a.training_load)
    return jsonable(E.performance(dict(load), start, end))


# ---- web UI -------------------------------------------------------------------------------------

_web = get_settings().web_dir
if (_web / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_web / "assets", check_dir=False), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        f = _web / path
        return FileResponse(f if path and f.is_file() else _web / "index.html")
