"""COROS through the official COROS MCP server (mcp.coros.com).

The MCP tools answer with human-readable text reports, so this module parses those reports. Each parser
is tolerant: unknown lines are ignored, and missing fields stay None. The tool names and arguments
come from the server's own tool list (checked with `homecoach coros-check`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homecoach.mcp_client import Session
from homecoach.providers.base import ActivityRecord, DailyRecord, FitnessRecord

log = logging.getLogger(__name__)

# Candidate tool names, in order of preference (the server's tools/list decides).
TOOLS = {
    "activities": ["querySportRecords"],
    "daily": ["queryDailyHealthData"],
    "sleep": ["querySleepOverview", "querySleepData"],
    "rhr": ["queryRestingHeartRate"],
    "hrv": ["querySleepHrv"],
    "load": ["queryTrainingLoadAssessment"],
    "fitness": ["queryFitnessAssessmentOverview"],
    "fit_urls": ["queryActivityFitFileDownloadUrls"],
}


WINDOW = 90  # days per request for date-range tools


# ---- small helpers -------------------------------------------------------------------------------


def mins(s: str | None) -> int | None:
    """'8h 39min' / '47 min' / '1h 0min' -> minutes."""
    if not s:
        return None
    h = re.search(r"(\d+)\s*h", s)
    m = re.search(r"(\d+)\s*min", s)
    if not h and not m:
        return None
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def num(pattern: str, text: str, cast: Callable = float) -> Any:
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return cast(m.group(1).replace(",", ""))
    except ValueError:
        return None


def hms(s: str | None) -> int | None:
    """'1:12:18' / '49:34' -> seconds."""
    if not s:
        return None
    parts = [int(p) for p in s.split(":") if p.isdigit()]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return None


# ---- parsers (pure functions, tested with synthetic reports) -------------------------------------


def parse_activities(text: str, tz: str = "UTC") -> list[ActivityRecord]:
    out = []
    zone = ZoneInfo(tz)
    for chunk in re.split(r"\n\s*\d+\.\s+(?=[^\n]*—\s*\d{4}-\d{2}-\d{2})", "\n" + text)[1:]:
        head = re.match(r"([^\n]*?)\s*—\s*(\d{4}-\d{2}-\d{2})", chunk)
        label = num(r"LabelId:\s*(\d+)", chunk, str)
        if not head or not label:
            continue
        sport, day = head.group(1).strip(), date.fromisoformat(head.group(2))
        ts = num(r"startTimestamp=(\d+)", chunk, int)
        start = datetime.fromtimestamp(ts, tz=UTC).astimezone(zone) if ts else datetime(day.year, day.month, day.day, tzinfo=zone)
        km = num(r"Distance:\s*([\d.]+)\s*km", chunk)
        loc = re.search(r"Location:\s*([^\n]+)", chunk)
        out.append(
            ActivityRecord(
                provider="coros",
                external_id=label,
                sport=sport,
                start_time=start,
                local_date=day,
                duration_s=hms(num(r"Duration:\s*([\d:]+)", chunk, str)),
                distance_m=km * 1000 if km else None,
                avg_hr=num(r"Avg HR:\s*(\d+)", chunk, int),
                calories=num(r"Calories:\s*([\d,]+)\s*kcal", chunk, int),
                name=loc.group(1).strip() if loc else None,
                raw={"sportTypeCode": num(r"SportType:\s*(\d+)", chunk, int), "source": "coros-mcp"},
            )
        )
    return out


def parse_daily(text: str) -> dict[date, dict]:
    out: dict[date, dict] = {}
    parts = re.split(r"---\s*(\d{8})\s*---", text)
    for i in range(1, len(parts), 2):
        d, b = datetime.strptime(parts[i], "%Y%m%d").date(), parts[i + 1]

        def g(p: str, b: str = b) -> str | None:
            m = re.search(p, b)
            return m.group(1).strip() if m else None

        v = {
            "steps": num(r"Steps:\s*([\d,]+)", b, int),
            "active_kcal": num(r"Calories:\s*([\d,]+)\s*kcal", b, int),
            "exercise_min": mins(g(r"Exercise:\s*([^\n|]+)")),
            "stress_avg": num(r"Stress:\s*Avg\s*(\d+)", b, int),
            "sleep_min": mins(g(r"Total:\s*([^|\n]+)")),
            "deep_min": mins(g(r"Deep:\s*([^|\n]+)")),
            "light_min": mins(g(r"Light:\s*([^|\n]+)")),
            "rem_min": mins(g(r"REM:\s*([^|\n]+)")),
            "awake_min": mins(g(r"Awake:\s*([^|\n]+)")),
            "sleep_score": num(r"Sleep Summary:\s*\(Score:\s*(\d+)\)", b, int),
        }
        if v["steps"] == 0 and not v["active_kcal"]:  # watch not worn yet
            v["steps"] = None
        out[d] = {k: x for k, x in v.items() if x is not None}
    return out


def parse_sleep(text: str, tz: str = "UTC") -> dict[date, dict]:
    out: dict[date, dict] = {}
    zone = ZoneInfo(tz)
    for chunk in re.split(r"\n(?=\d{4}-\d{2}-\d{2}\s*\n)", "\n" + text):
        m = re.match(r"\s*(\d{4}-\d{2}-\d{2})\s*\n", chunk)
        if not m:
            continue
        v: dict[str, Any] = {}
        if (sc := num(r"Sleep Score:\s*(\d+)", chunk, int)) is not None:
            v["sleep_score"] = sc
        if (asleep := re.search(r"Main Sleep \(asleep\):\s*([^\n]+)", chunk)) and mins(asleep.group(1)):
            v["sleep_min"] = mins(asleep.group(1))
        if w := re.search(r"Main Sleep Window:\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2})", chunk):
            d0 = date.fromisoformat(w.group(1))
            v["sleep_start"] = datetime(d0.year, d0.month, d0.day, int(w.group(2)), int(w.group(3)), tzinfo=zone)
        if v:
            out[date.fromisoformat(m.group(1))] = v
    return out


def parse_rhr(text: str) -> dict[date, dict]:
    return {date.fromisoformat(d): {"resting_hr": int(v)} for d, v in re.findall(r"(\d{4}-\d{2}-\d{2}):\s*(\d+)\s*bpm", text)}


def parse_hrv(text: str) -> dict[date, dict]:
    out = {}
    pat = r"(\d{4}-\d{2}-\d{2}):\s*\n\s*HRV Avg:\s*(\d+)\s*ms[^\n]*\n\s*Normal Range:\s*(\d+)\s*-\s*(\d+)\s*ms"
    for d, h, lo, hi in re.findall(pat, text):
        out[date.fromisoformat(d)] = {"hrv_avg": float(h), "hrv_lo": float(lo), "hrv_hi": float(hi)}
    return out


def parse_load(text: str) -> dict[date, dict]:
    out = {}
    pat = (
        r"(\d{4}-\d{2}-\d{2})\s*\nComment:[^\n]*\nShort-Term Load:\s*([\d.]+)\nLong-Term Load:\s*([\d.]+)\nLoad Ratio:\s*([\d.]+)"
    )
    for d, st, lt, ratio in re.findall(pat, text):
        out[date.fromisoformat(d)] = {"load_short": float(st), "load_long": float(lt), "load_ratio": float(ratio)}
    return out


def parse_fitness(text: str, today: date) -> FitnessRecord | None:
    vo2 = num(r"VO2max:\s*([\d.]+)", text)
    if vo2 is None:
        return None
    th = num(r"Threshold Pace:\s*([\d:]+)", text, str)
    preds = {}
    for key, label in (("5k", r"5 km"), ("10k", r"10 km"), ("half", r"Half Marathon"), ("marathon", r"(?<!Half )Marathon")):
        s = num(label + r" Prediction:\s*([\d:]+)", text, str)
        if s and hms(s):
            preds[key] = hms(s)
    return FitnessRecord(
        day=today,
        vo2max=vo2,
        threshold_pace_s=hms(th) if th else None,
        running_level=num(r"Running Level:\s*([\d.]+)", text),
        predictions=preds or None,
    )


# ---- provider -----------------------------------------------------------------------------------


class CorosMCPProvider:
    name = "coros"  # same ids as the web-API provider, so switching doesn't duplicate activities

    def __init__(self, session: Session, tz: str = "UTC"):
        self.s = session
        self.tz = tz

    def _tool(self, kind: str) -> str | None:
        if not self.s.tools:
            self.s.list_tools()
        return next((n for n in TOOLS[kind] if n in self.s.tools), None)

    def _args(self, tool: str, start: date, end: date) -> dict:
        """Build arguments from the tool's input schema (date range or a number of days)."""
        props = (self.s.tools.get(tool, {}).get("inputSchema") or {}).get("properties", {})
        a: dict[str, Any] = {}
        if "startDate" in props:
            a["startDate"], a["endDate"] = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        elif "days" in props:
            a["days"] = min(365, (end - start).days + 1)
        if "limit" in props:
            a["limit"] = 1000
        return a

    def _text(self, kind: str, start: date, end: date) -> str:
        """The report text for a date range. Date-range tools are called in WINDOW-day pieces (long ranges
        can be refused or truncated); 'last N days' tools are called once (they count back from today)."""
        tool = self._tool(kind)
        if not tool:
            log.info("COROS MCP has no %s tool; skipping", kind)
            return ""
        props = (self.s.tools.get(tool, {}).get("inputSchema") or {}).get("properties", {})
        if "startDate" not in props or (end - start).days < WINDOW:
            return self.s.call(tool, self._args(tool, start, end))
        parts, a = [], start
        while a <= end:
            b = min(end, a + timedelta(days=WINDOW - 1))
            parts.append(self.s.call(tool, self._args(tool, a, b)))
            a = b + timedelta(days=1)
        return "\n\n".join(parts)

    def fetch_activities(self, start: date, end: date) -> list[ActivityRecord]:
        return parse_activities(self._text("activities", start, end), self.tz)

    def fetch_daily(self, start: date, end: date) -> list[DailyRecord]:
        merged: dict[date, dict] = {}
        for kind, parse in (
            ("daily", parse_daily),
            ("rhr", parse_rhr),
            ("load", parse_load),
            ("hrv", parse_hrv),
            ("sleep", lambda t: parse_sleep(t, self.tz)),
        ):
            try:
                rows = parse(self._text(kind, start, end))
            except Exception as e:  # one failing report shouldn't stop the rest
                log.warning("COROS %s failed: %s", kind, e)
                continue
            for d, v in rows.items():
                if start <= d <= end:
                    merged.setdefault(d, {}).update(v)
        return [DailyRecord(d, v) for d, v in sorted(merged.items())]

    def fetch_fitness(self) -> FitnessRecord | None:
        today = date.today()
        return parse_fitness(self._text("fitness", today - timedelta(days=7), today), today)

    def download_fit(self, activity: ActivityRecord) -> bytes | None:
        return None  # FIT downloads via MCP are not wired up yet; activity summaries are complete without them
