"""FastAPI app: JSON API under /api and the web UI at /."""

from __future__ import annotations

import base64
import html
import logging
import secrets
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from homecoach import __version__
from homecoach.config import get_settings
from homecoach.db import get_session
from homecoach.models import (
    Activity,
    CheckIn,
    CoachNote,
    CoachSuggestion,
    DailyMetric,
    FitnessSnapshot,
    Race,
    RaceInsight,
    SyncState,
)
from homecoach.plan import engine as E

log = logging.getLogger(__name__)

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
                ok = secrets.compare_digest(user.encode(), s.auth_user.encode()) and secrets.compare_digest(
                    pwd.encode(), s.auth_password.get_secret_value().encode()
                )
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
def health(db: Session = Db) -> dict:
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(503, "database unavailable") from e
    return {"ok": True, "version": __version__}


@app.get("/api/config")
def config(db: Session = Db) -> dict:
    s = get_settings()
    from homecoach.coros_auth import KEY as COROS_AUTH_KEY

    # sync progress only: the COROS connection (tokens) lives in the same table and must never leave the server
    states = {st.key: st.value for st in db.scalars(select(SyncState).where(SyncState.key != COROS_AUTH_KEY))}
    return {"athlete_name": s.athlete_name, "timezone": s.timezone, "provider": s.provider, "sync": states, "today": today()}


# ---- full resync (button in the header) ----------------------------------------------------------

_resync: dict = {"running": False}
_resync_lock = threading.Lock()


def _resync_run() -> None:
    from homecoach import llm, outlook
    from homecoach.collector.main import run_once
    from homecoach.db import session_scope

    try:
        _resync.update(stages={k: "pending" for k in ("sync", "plan", "coach", "outlooks")}, warnings=[])
        _resync.update(step="Re-reading your whole history from COROS")
        _resync["stages"]["sync"] = "running"
        stats = run_once(full=True)
        if stats and stats.get("mode") == "skipped":
            raise RuntimeError("Another sync is running; wait for it to finish before requesting a full resync.")
        _resync["stats"] = stats
        _resync["stages"]["sync"] = "completed"
        _resync.update(step="Rebuilding the training plan")
        _resync["stages"]["plan"] = "running"
        with session_scope() as db:
            compute_plan(db)
        _resync["stages"]["plan"] = "completed"
        s = get_settings()
        if llm.enabled(s):
            _resync.update(step="Reviewing the updated plan and refreshing coach recommendations")
            _resync["stages"]["coach"] = "running"
            try:
                review = run_review()
                _resync["coach_suggestions"] = len(review["suggestions"])
                _resync["stages"]["coach"] = "completed"
            except Exception:
                log.exception("coach refresh during full resync failed")
                _resync["stages"]["coach"] = "failed"
                _resync["warnings"].append("Coach recommendations could not be refreshed; previous suggestions were retained.")
            _resync["stages"]["outlooks"] = "running"
            failed_outlooks = 0
            with session_scope() as db:
                races = [r for r in db.scalars(select(Race).where(Race.day >= today())) if r.goal_time_s]
                for i, r in enumerate(races, 1):
                    _resync.update(step=f"Updating the race outlook ({i}/{len(races)}): {r.name}")
                    try:
                        outlook.interpret(s, db, r, today(), force=True)
                        db.commit()
                    except Exception as e:  # one race failing shouldn't stop the rest
                        db.rollback()
                        failed_outlooks += 1
                        log.warning("race outlook for %s failed: %s", r.name, e)
            _resync["stages"]["outlooks"] = "failed" if failed_outlooks else "completed"
            if failed_outlooks:
                _resync["warnings"].append(f"{failed_outlooks} race outlook(s) could not be refreshed.")
        else:
            _resync["stages"].update(coach="skipped", outlooks="skipped")
            _resync["warnings"].append(
                "AI is disabled: the rule-based plan was rebuilt; coach recommendations and AI outlooks were skipped."
            )
        _resync.update(error=None)
    except Exception as e:
        log.exception("full resync failed")
        _resync.update(error=str(e))
        for stage, status in _resync.get("stages", {}).items():
            if status == "running":
                _resync["stages"][stage] = "failed"
            elif status == "pending":
                _resync["stages"][stage] = "skipped"
    finally:
        _resync.update(running=False, step=None, finished_at=datetime.now().isoformat(timespec="seconds"))
        _resync_lock.release()


@app.post("/api/resync", status_code=202)
def resync() -> dict:
    """Re-read everything from the provider, then refresh race outlooks. The plan is computed from the data on
    every request, so it updates by itself once the data is in."""
    if not _resync_lock.acquire(blocking=False):
        return {**_resync, "already_running": True}
    _resync.clear()
    _resync.update(running=True, started_at=datetime.now().isoformat(timespec="seconds"), step="Starting", error=None)
    threading.Thread(target=_resync_run, daemon=True).start()
    return dict(_resync)


@app.get("/api/resync")
def resync_status() -> dict:
    return dict(_resync)


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
        "predictions": _predictions(db),
        "threshold_pace": E.fmt_pace(th) if th else None,
    }


def _predictions(db: Session) -> dict | None:
    from homecoach.outlook import latest_predictions

    p = latest_predictions(db)
    return {k: fmt_time(v) for k, v in p.items()} if p else None


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
    db.execute(delete(RaceInsight).where(RaceInsight.race_id == race_id))
    db.delete(r)


# ---- race outlook -------------------------------------------------------------------------------


def outlook_out(db: Session, r: Race) -> dict:
    from homecoach import outlook

    f = jsonable(outlook.facts_for(db, r, today()))
    ins = outlook.latest(db, r.id)
    f["insight"] = {"text": ins.text, "day": ins.day, "today": ins.day == today()} if ins else None
    return f


@app.get("/api/races/outlook")
def races_outlook(db: Session = Db) -> dict:
    from homecoach import llm

    races = db.scalars(select(Race).where(Race.day >= today()).order_by(Race.day))
    return {"llm": llm.enabled(get_settings()), "races": [outlook_out(db, r) for r in races]}


class OutlookBody(BaseModel):
    force: bool = False


@app.post("/api/races/{race_id}/outlook")
def race_outlook_ai(race_id: int, body: OutlookBody | None = None, db: Session = Db) -> dict:
    """Ask the local AI to interpret the outlook (cached for the day unless force)."""
    from homecoach import llm, outlook

    s = get_settings()
    r = db.get(Race, race_id)
    if not r:
        raise HTTPException(404, "race not found")
    if not llm.enabled(s):
        raise HTTPException(409, "the local AI is off (set HC_LLM_PROVIDER)")
    if not r.goal_time_s:
        raise HTTPException(400, "set a goal time for this race first")
    try:
        outlook.interpret(s, db, r, today(), force=bool(body and body.force))
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from e
    db.commit()
    return outlook_out(db, r)


# ---- race assistant -----------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)


@app.get("/api/assistant")
def assistant_status() -> dict:
    from homecoach import llm

    s = get_settings()
    return {"enabled": llm.enabled(s), "provider": s.llm_provider, "model": s.llm_model}


@app.post("/api/assistant/chat")
def assistant_chat(body: ChatBody, db: Session = Db) -> dict:
    from homecoach.assistant import chat
    from homecoach.llm import LLMError

    if body.messages[-1].role != "user":
        raise HTTPException(422, "the last message must be from the user")
    races = [race_out(r) for r in db.scalars(select(Race).where(Race.day >= today()).order_by(Race.day))]
    try:
        return jsonable(chat(get_settings(), [m.model_dump() for m in body.messages], today(), races))
    except LLMError as e:
        raise HTTPException(503, str(e)) from e


# ---- COROS connection (official MCP) -------------------------------------------------------------

FALLBACK_REDIRECT = "http://localhost/homecoach-coros-callback"


class ConnectBody(BaseModel):
    paste: bool = False


class PasteBody(BaseModel):
    url: str = Field(max_length=4000)


def _start_first_sync() -> None:
    from homecoach.collector.main import run_once

    threading.Thread(target=run_once, kwargs={"full": False}, daemon=True).start()


@app.get("/api/coros")
def coros_status(db: Session = Db) -> dict:
    from homecoach import coros_auth

    return {"provider": get_settings().provider, **coros_auth.status(db)}


@app.post("/api/coros/connect")
def coros_connect(body: ConnectBody, request: Request, db: Session = Db) -> dict:
    from homecoach import coros_auth
    from homecoach.mcp_client import MCPError

    redirect = str(request.base_url).rstrip("/") + "/api/coros/callback"
    try:
        url, paste = coros_auth.start(get_settings(), db, redirect, FALLBACK_REDIRECT, use_fallback=body.paste)
    except (MCPError, httpx.HTTPError) as e:
        db.rollback()
        raise HTTPException(502, f"Couldn't start the COROS login: {e}") from e
    return {"url": url, "paste": paste}


@app.get("/api/coros/callback", include_in_schema=False)
def coros_callback(db: Session = Db, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    from fastapi.responses import HTMLResponse, RedirectResponse

    from homecoach import coros_auth
    from homecoach.mcp_client import MCPError

    if error or not code:
        msg = error_description or error or "no authorization code returned"
        return HTMLResponse(
            f"<p>COROS login didn't complete: {html.escape(msg)}</p><p><a href='/'>Back to homecoach</a></p>", 400
        )
    try:
        coros_auth.finish(db, code, state)
    except (MCPError, httpx.HTTPError) as e:
        return HTMLResponse(f"<p>{html.escape(str(e))}</p><p><a href='/'>Back to homecoach</a></p>", 400)
    db.commit()
    _start_first_sync()
    return RedirectResponse("/#today", status_code=303)


@app.post("/api/coros/paste")
def coros_paste(body: PasteBody, db: Session = Db) -> dict:
    from homecoach import coros_auth
    from homecoach.mcp_client import MCPError

    try:
        coros_auth.finish_from_pasted_url(db, body.url)
    except (MCPError, httpx.HTTPError) as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    _start_first_sync()
    return coros_auth.status(db)


@app.post("/api/coros/disconnect")
def coros_disconnect(db: Session = Db) -> dict:
    from homecoach import coros_auth

    coros_auth.disconnect(db)
    return coros_auth.status(db)


# ---- coach ------------------------------------------------------------------------------------

_review_lock = threading.Lock()


def run_review(source: str = "review") -> dict:
    """Weekly review (also used by the collector). Uses its own DB session."""
    from homecoach import coach
    from homecoach.db import session_scope
    from homecoach.locking import execution_lock

    with _review_lock, execution_lock(get_settings().data_dir / ".coach-review.lock") as acquired, session_scope() as db:
        if not acquired:
            raise RuntimeError("another coach review is running")
        pending = list(db.scalars(select(CoachSuggestion.id).where(CoachSuggestion.status == "pending")))
        result = coach.run(
            get_settings(), db, jsonable(compute_plan(db)), today(), [{"role": "user", "content": coach.REVIEW_PROMPT}], source
        )
        # A failed model call rolls back this transaction and retains the old recommendations.
        if pending:
            db.execute(delete(CoachSuggestion).where(CoachSuggestion.id.in_(pending), CoachSuggestion.status == "pending"))
        return result


@app.get("/api/coach")
def coach_state(db: Session = Db) -> dict:
    from homecoach import coach, llm

    s = get_settings()
    notes = db.scalars(select(CoachNote).order_by(CoachNote.created_at.desc()).limit(5))
    sugg = db.scalars(
        select(CoachSuggestion)
        .where(CoachSuggestion.day >= E.monday(today()))
        .order_by(CoachSuggestion.created_at.desc())
        .limit(30)
    )
    checks = db.scalars(select(CheckIn).order_by(CheckIn.created_at.desc()).limit(10))
    return jsonable(
        {
            "enabled": llm.enabled(s),
            "provider": s.llm_provider,
            "model": s.llm_model,
            "review_running": _review_lock.locked(),
            "notes": [{"id": n.id, "kind": n.kind, "text": n.text, "created_at": n.created_at} for n in notes],
            "suggestions": [coach.suggestion_out(x) for x in sugg],
            "checkins": [{"id": c.id, "day": c.day, "text": c.text} for c in checks],
        }
    )


@app.post("/api/coach/chat")
def coach_chat(body: ChatBody, db: Session = Db) -> dict:
    from homecoach import coach
    from homecoach.llm import LLMError

    if body.messages[-1].role != "user":
        raise HTTPException(422, "the last message must be from the user")
    db.add(CheckIn(day=today(), text=body.messages[-1].content.strip()))  # the coach remembers what you tell it
    db.commit()  # keep the check-in even if the model call fails
    try:
        out = coach.run(get_settings(), db, jsonable(compute_plan(db)), today(), [m.model_dump() for m in body.messages], "chat")
    except LLMError as e:
        raise HTTPException(503, str(e)) from e
    return jsonable(out)


@app.post("/api/coach/review", status_code=202)
def coach_review() -> dict:
    from homecoach import llm

    if not llm.enabled(get_settings()):
        raise HTTPException(503, "No LLM is configured: set HC_LLM_PROVIDER in .env.")
    if _review_lock.locked():
        return {"started": False, "running": True}

    def go() -> None:
        try:
            run_review()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("coach review failed")

    threading.Thread(target=go, daemon=True).start()
    return {"started": True}


class SuggestionStatus(BaseModel):
    status: Literal["accepted", "dismissed", "pending"]


@app.post("/api/coach/suggestions/{sid}")
def set_suggestion(sid: int, body: SuggestionStatus, db: Session = Db) -> dict:
    from homecoach import coach

    # Serialise decisions across web workers until the dependency commits this transaction.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(8742001)"))
    x = db.get(CoachSuggestion, sid)
    if not x:
        raise HTTPException(404, "suggestion not found")
    if body.status == "accepted" and x.status != "accepted":
        p = compute_plan(db)
        b = p["baseline"]
        if not E.monday(today()) <= x.day < b.next_monday + timedelta(days=7):
            raise HTTPException(409, "suggestion is outside this week and next week; request a new review")
        week = p["this_week"]["sessions"] if x.day < b.next_monday else p["next_week"]
        target = p["this_week"]["target_km"] if x.day < b.next_monday else p["next_week_target_km"]
        a = E.Adjustment(x.day, x.action, x.session, x.km, x.pace, x.detail, x.move_to, x.reason, x.duration_min)
        why = E.check_adjustment(a, week, target, b.longest_km, today())
        if why:
            raise HTTPException(409, why)
    x.status = body.status
    return jsonable(coach.suggestion_out(x))


@app.delete("/api/coach/checkins/{cid}", status_code=204)
def delete_checkin(cid: int, db: Session = Db) -> None:
    x = db.get(CheckIn, cid)
    if not x:
        raise HTTPException(404, "check-in not found")
    db.delete(x)


# ---- plan ---------------------------------------------------------------------------------------


@app.get("/api/plan")
def plan(db: Session = Db) -> dict:
    return jsonable(compute_plan(db))


def accepted_adjustments(db: Session, start: date) -> list[E.Adjustment]:
    rows = db.scalars(
        select(CoachSuggestion)
        .where(CoachSuggestion.status == "accepted", CoachSuggestion.day >= start)
        .order_by(CoachSuggestion.created_at, CoachSuggestion.id)
    )
    return [E.Adjustment(x.day, x.action, x.session, x.km, x.pace, x.detail, x.move_to, x.reason, x.duration_min) for x in rows]


def compute_plan(db: Session) -> dict:
    """The rule plan for this and next week with accepted coach changes applied (shared by the API and the coach)."""
    t = today()
    acts = list(db.scalars(select(Activity).where(Activity.local_date >= t - timedelta(days=70))))
    rows = list(db.scalars(select(DailyMetric).where(DailyMetric.day >= t - timedelta(days=35)).order_by(DailyMetric.day)))
    races = races_from(list(db.scalars(select(Race).where(Race.day >= t))))
    runs = runs_from(acts)
    b = E.baseline(runs, days_from(rows), t, threshold_s(db))
    r = E.readiness(b)
    weeks = E.race_plan(races, b)
    tw, done_km, target_km = E.this_week(runs, b, races, r, done=runs)
    nw = E.next_week(b, weeks, r)
    next_target = round(sum(x.km for x in nw if x.session != "Strength"), 1)
    adj = accepted_adjustments(db, E.monday(t))
    inactive = []
    for a in adj:
        if a.day < t or a.day >= b.next_monday + timedelta(days=7):
            continue
        current, target = (tw, target_km) if a.day < b.next_monday else (nw, next_target)
        why = E.check_adjustment(a, current, target, b.longest_km, t)
        if why:
            inactive.append({"day": a.day, "reason": why})
        elif a.day < b.next_monday:
            tw = E.apply_adjustments(tw, [a])
        else:
            nw = E.apply_adjustments(nw, [a])
    tr = E.target_race(races, t)
    return {
        "athlete_name": get_settings().athlete_name,
        "baseline": b,
        "readiness": r,
        "target_race": tr,
        "this_week": {"sessions": tw, "done_km": done_km, "target_km": target_km},
        "next_week": nw,
        "next_week_target_km": next_target,
        "inactive_suggestions": inactive,
        "race_plan": weeks,
        "road": E.road(runs, weeks, b),
        "shape": {**E.endurance_shape(runs, t, tr.distance_km), "race": tr.name} if tr else None,
    }


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

from homecoach.api.nutrition import router as nutrition_router  # noqa: E402
from homecoach.api.static import mount_web  # noqa: E402
from homecoach.api.training import router as training_router  # noqa: E402

app.include_router(training_router)
app.include_router(nutrition_router)
mount_web(app, get_settings().web_dir)
