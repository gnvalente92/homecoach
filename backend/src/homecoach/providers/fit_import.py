"""Import .fit files dropped into the inbox folder (export from any watch/app)."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

import fitdecode

from homecoach.providers.base import ActivityRecord

log = logging.getLogger(__name__)


def parse_fit(data: bytes, tz: str = "UTC") -> ActivityRecord | None:
    session: dict = {}
    with fitdecode.FitReader(data) as fit:
        for frame in fit:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "session":
                session = {f.name: f.value for f in frame.fields if f.value is not None}
                break
    start = session.get("start_time")
    if not start:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    local = start.astimezone(ZoneInfo(tz))
    sport = str(session.get("sport", "generic")).replace("_", " ").title()
    sub = session.get("sub_sport")
    if sub and str(sub) not in ("generic", "None"):
        sport = f"{sport} ({str(sub).replace('_', ' ')})"
    return ActivityRecord(
        provider="fit",
        external_id=hashlib.sha1(data).hexdigest()[:20],
        sport=sport,
        start_time=local,
        local_date=local.date(),
        duration_s=int(session.get("total_timer_time") or session.get("total_elapsed_time") or 0) or None,
        distance_m=float(session["total_distance"]) if session.get("total_distance") else None,
        avg_hr=session.get("avg_heart_rate"),
        max_hr=session.get("max_heart_rate"),
        calories=session.get("total_calories"),
        raw={"source": "fit"},
    )


def inbox_files(inbox: Path) -> list[Path]:
    if not inbox.exists():
        return []
    return sorted(p for p in inbox.iterdir() if p.suffix.lower() == ".fit" and p.is_file())
