"""Collector worker: incremental sync every N minutes, full re-sync nightly, FIT inbox watcher."""

from __future__ import annotations

import logging
import signal
from typing import cast

from apscheduler.schedulers.blocking import BlockingScheduler

from homecoach.collector.sync import import_fit_inbox, sync_provider
from homecoach.config import get_settings
from homecoach.db import session_scope
from homecoach.providers import get_provider

log = logging.getLogger("homecoach.collector")


# Bumped when a fix needs one more full read: v2 = report text decoding (sleep score, bedtime, HRV, load, activities)
BACKFILL_KEY = "coros-mcp-backfill-v2"


def reset_web_api_units(s) -> None:
    """The web-API provider stored training load in other units (hundreds to thousands) than the official
    COROS connection (tens), and COROS only returns the last 31 days of load. Mixed, the chart jumps and the
    numbers mean nothing, so the old values are cleared once; the official connection fills them from here on."""
    from sqlalchemy import update

    from homecoach.models import DailyMetric

    s.execute(update(DailyMetric).values(load_short=None, load_long=None))


def run_once(full: bool = False) -> dict | None:
    from datetime import UTC, datetime

    from homecoach.collector.sync import get_state, set_state
    from homecoach.locking import execution_lock

    with execution_lock(get_settings().data_dir / ".sync.lock") as acquired:
        if not acquired:
            return {"mode": "skipped", "reason": "another sync is running"}
        attempted = datetime.now(UTC).isoformat()
        try:
            stats = _run_once(full)
        except Exception as e:
            with session_scope() as db:
                previous = get_state(db, "collector-status") or {}
                set_state(db, "collector-status", {**previous, "last_attempt": attempted, "error": type(e).__name__})
            raise
        with session_scope() as db:
            set_state(
                db,
                "collector-status",
                {
                    "last_attempt": attempted,
                    "last_success": datetime.now(UTC).isoformat(),
                    "error": None,
                    "provider_synced": stats is not None,
                },
            )
        return stats


def _run_once(full: bool = False) -> dict | None:
    """One sync. Returns its stats (None when there's nothing to sync from)."""
    settings = get_settings()
    with session_scope() as s:
        n = import_fit_inbox(s, settings)
        if n:
            log.info("imported %d FIT files from inbox", n)
    if settings.provider == "none":
        return None
    with session_scope() as s:
        if settings.provider == "coros-mcp":
            from homecoach import coros_auth
            from homecoach.mcp_client import AuthRequired
            from homecoach.providers.coros_mcp import CorosMCPProvider

            try:
                provider = cast(CorosMCPProvider, get_provider(settings, s))
            except AuthRequired as e:
                log.warning("%s", e)
                return None
            from homecoach.collector.sync import get_state, set_state

            # the first sync through the official connection re-reads everything, even if the old web-API
            # provider already synced (it shares the provider name, so its "last sync" would make this incremental)
            first = not get_state(s, BACKFILL_KEY)
            original_tokens = dict(provider.s.state.get("tokens") or {})
            if first:
                reset_web_api_units(s)  # same transaction: rolled back if the sync below fails
            try:
                stats = sync_provider(s, provider, settings, full=full or first)
                if full or first:
                    set_state(s, BACKFILL_KEY, {"done": True})
            except Exception:
                # Activity writes roll back, but a rotated refresh token must survive that rollback.
                s.rollback()
                st = provider.s.state
                if st.pop("changed", False) and coros_auth.load(s).get("tokens") == original_tokens:
                    coros_auth.save(s, st)
                    s.commit()
                raise
            else:
                sync_profile(s, provider)
                st = provider.s.state
                if st.pop("changed", False) and coros_auth.load(s).get("tokens") == original_tokens:
                    coros_auth.save(s, st)
        else:
            web_provider = get_provider(settings)
            if web_provider is None:
                return None
            stats = sync_provider(s, web_provider, settings, full=full)
    log.info("sync %s done: %s", stats.get("mode", "incremental"), stats)
    return stats


def sync_profile(s, provider) -> None:
    """Body data (weight, height, birthday, sex) for nutrition targets; never fails the sync."""
    fetch = getattr(provider, "fetch_profile", None)
    if not fetch:
        return
    from homecoach import nutrition

    try:
        with s.begin_nested():
            if nutrition.update_from_provider(s, fetch()):
                log.info("nutrition profile updated from %s", provider.name)
    except Exception as e:
        log.warning("couldn't read the profile from %s: %s", provider.name, e)


def _heartbeat() -> None:
    try:
        p = get_settings().data_dir / ".collector-heartbeat"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    except OSError as e:
        log.warning("could not write heartbeat: %s", e)


def _safe(full: bool) -> None:
    _heartbeat()  # alive, even if the provider is failing
    try:
        run_once(full)
    except Exception:
        log.exception("sync failed; will retry on the next run")
    _heartbeat()


def _outlook_target_race() -> None:
    """Refresh the AI race outlook for the target race (the one the plan is built around)."""
    from sqlalchemy import select

    from homecoach import outlook
    from homecoach.api.main import today
    from homecoach.db import session_scope
    from homecoach.models import Race
    from homecoach.plan import engine as E

    try:
        with session_scope() as db:
            rows = list(db.scalars(select(Race).where(Race.day >= today())))
            tr = E.target_race([E.RaceIn(r.name, r.day, r.distance_km, r.goal_time_s, r.priority, r.id) for r in rows], today())
            race = next((r for r in rows if tr and r.id == tr.id), None)
            if race and race.goal_time_s:
                outlook.interpret(get_settings(), db, race, today(), force=True)
                log.info("race outlook refreshed for %s", race.name)
    except Exception:
        log.exception("race outlook failed")


def _weekly_review() -> None:
    from homecoach import llm

    if not llm.enabled(get_settings()):
        return
    try:
        run_once(False)  # make sure the review sees today's workouts
        from homecoach.api.main import run_review

        out = run_review()
        log.info("weekly coach review done: %d suggestion(s)", len(out["suggestions"]))
        _outlook_target_race()
    except Exception:
        log.exception("weekly coach review failed")


def main() -> None:
    settings = get_settings()
    sched = BlockingScheduler(timezone=settings.timezone)
    sched.add_job(_heartbeat, "interval", minutes=5, id="heartbeat")
    sched.add_job(
        _safe, "interval", minutes=settings.sync_interval_minutes, args=[False], id="incremental", max_instances=1, coalesce=True
    )
    sched.add_job(_safe, "cron", hour=settings.full_sync_hour, minute=17, args=[True], id="full", max_instances=1, coalesce=True)
    sched.add_job(
        _weekly_review,
        "cron",
        day_of_week=settings.coach_review_dow,
        hour=settings.coach_review_hour,
        minute=7,
        id="coach-review",
        max_instances=1,
        coalesce=True,
    )
    signal.signal(signal.SIGTERM, lambda *_: sched.shutdown(wait=False))
    log.info("collector started: every %d min, full sync at %02d:17", settings.sync_interval_minutes, settings.full_sync_hour)
    _safe(False)  # first run immediately (does a backfill when the database is empty)
    sched.start()
