"""Collector worker: incremental sync every N minutes, full re-sync nightly, FIT inbox watcher."""

from __future__ import annotations

import logging
import signal

from apscheduler.schedulers.blocking import BlockingScheduler

from homecoach.collector.sync import import_fit_inbox, sync_provider
from homecoach.config import get_settings
from homecoach.db import session_scope
from homecoach.providers import get_provider

log = logging.getLogger("homecoach.collector")


def run_once(full: bool = False) -> None:
    settings = get_settings()
    provider = get_provider(settings)
    with session_scope() as s:
        n = import_fit_inbox(s, settings)
        if n:
            log.info("imported %d FIT files from inbox", n)
    if provider is None:
        return
    with session_scope() as s:
        stats = sync_provider(s, provider, settings, full=full)
    log.info("sync %s done: %s", stats.pop("mode", "incremental"), stats)


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


def main() -> None:
    settings = get_settings()
    sched = BlockingScheduler(timezone=settings.timezone)
    sched.add_job(_heartbeat, "interval", minutes=5, id="heartbeat")
    sched.add_job(
        _safe, "interval", minutes=settings.sync_interval_minutes, args=[False], id="incremental", max_instances=1, coalesce=True
    )
    sched.add_job(_safe, "cron", hour=settings.full_sync_hour, minute=17, args=[True], id="full", max_instances=1, coalesce=True)
    signal.signal(signal.SIGTERM, lambda *_: sched.shutdown(wait=False))
    log.info("collector started: every %d min, full sync at %02d:17", settings.sync_interval_minutes, settings.full_sync_hour)
    _safe(False)  # first run immediately (does a backfill when the database is empty)
    sched.start()
