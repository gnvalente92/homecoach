"""Idempotent sync of provider data into the local database."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from homecoach.config import Settings
from homecoach.models import Activity, DailyMetric, FitnessSnapshot, SyncState
from homecoach.providers.base import ActivityRecord, DailyRecord, FitnessRecord, Provider
from homecoach.providers.fit_import import inbox_files, parse_fit

log = logging.getLogger(__name__)


def upsert_activity(s: Session, a: ActivityRecord) -> bool:
    """Insert or update. Returns True when the row is new. FIT imports that duplicate an
    existing activity (same start within 2 minutes) are skipped."""
    existing = s.get(Activity, a.id)
    if existing is None and a.provider == "fit":
        dup = s.scalar(
            select(Activity.id).where(
                Activity.start_time.between(a.start_time - timedelta(minutes=2), a.start_time + timedelta(minutes=2))
            )
        )
        if dup:
            return False
    row = existing or Activity(id=a.id)
    for f in ("provider", "external_id", "sport", "type", "name", "start_time", "local_date", "duration_s",
              "distance_m", "avg_hr", "max_hr", "calories", "training_load", "raw"):
        setattr(row, f, getattr(a, f))
    s.add(row)
    return existing is None


def upsert_daily(s: Session, d: DailyRecord) -> None:
    row = s.get(DailyMetric, d.day) or DailyMetric(day=d.day)
    for k, v in d.values.items():
        if v is not None and hasattr(row, k):  # never overwrite known values with NULL
            setattr(row, k, v)
    s.add(row)


def upsert_fitness(s: Session, f: FitnessRecord) -> None:
    row = s.get(FitnessSnapshot, f.day) or FitnessSnapshot(day=f.day)
    for k in ("vo2max", "threshold_pace_s", "running_level", "predictions"):
        v = getattr(f, k)
        if v is not None:
            setattr(row, k, v)
    s.add(row)


def get_state(s: Session, key: str) -> dict[str, Any]:
    st = s.get(SyncState, key)
    return dict(st.value) if st else {}


def set_state(s: Session, key: str, value: dict[str, Any]) -> None:
    st = s.get(SyncState, key) or SyncState(key=key, value={})
    st.value = value
    s.add(st)


def sync_provider(s: Session, provider: Provider, settings: Settings, *, full: bool = False) -> dict[str, int]:
    """Incremental by default: re-reads the last few days (late uploads, recalculated metrics).
    Full: re-reads the whole backfill window."""
    today = date.today()
    state = get_state(s, f"provider:{provider.name}")
    first_run = "last_sync" not in state
    lookback = settings.backfill_days if (full or first_run) else 3
    start = today - timedelta(days=lookback)
    stats = {"activities_new": 0, "activities_seen": 0, "days": 0, "fit_downloaded": 0}

    acts = provider.fetch_activities(start, today)
    for a in acts:
        stats["activities_seen"] += 1
        if upsert_activity(s, a):
            stats["activities_new"] += 1
        if settings.download_fit:
            stats["fit_downloaded"] += _ensure_fit(s, provider, a, settings.fit_dir)
    s.flush()

    for d in provider.fetch_daily(start, today):
        upsert_daily(s, d)
        stats["days"] += 1

    try:
        f = provider.fetch_fitness()
        if f:
            upsert_fitness(s, f)
    except Exception as e:  # fitness is a nice-to-have; never fail the sync for it
        log.warning("fitness fetch failed: %s", e)

    set_state(s, f"provider:{provider.name}", {"last_sync": datetime.now().isoformat(), "last_full": (
        datetime.now().isoformat() if (full or first_run) else state.get("last_full"))})
    return stats


def _ensure_fit(s: Session, provider: Provider, a: ActivityRecord, fit_dir: Path) -> int:
    row = s.get(Activity, a.id)
    if row is None or row.fit_path:
        return 0
    try:
        data = provider.download_fit(a)
    except Exception as e:
        log.info("FIT download failed for %s: %s", a.id, e)
        return 0
    if not data:
        return 0
    path = fit_dir / a.local_date.strftime("%Y/%m") / f"{a.provider}_{a.external_id}.fit"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    row.fit_path = str(path)
    return 1


def import_fit_inbox(s: Session, settings: Settings) -> int:
    n = 0
    for p in inbox_files(settings.fit_inbox):
        data = p.read_bytes()
        try:
            rec = parse_fit(data, settings.timezone)
        except Exception as e:
            log.warning("could not parse %s: %s", p.name, e)
            continue
        if rec and upsert_activity(s, rec):
            dest = settings.fit_dir / rec.local_date.strftime("%Y/%m") / f"fit_{rec.external_id}.fit"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            s.flush()
            row = s.get(Activity, rec.id)
            if row:
                row.fit_path = str(dest)
            n += 1
        p.unlink()
    return n
