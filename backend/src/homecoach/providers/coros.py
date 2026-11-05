"""COROS Training Hub web API client.

This API is UNOFFICIAL and undocumented: it is the API the training.coros.com web app uses,
reverse-engineered by community projects. It can change without notice. Every response field is
read defensively and the raw payload is stored, so a field rename degrades data rather than
crashing the collector.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import httpx

from homecoach.config import Settings
from homecoach.providers.base import ActivityRecord, DailyRecord, FitnessRecord

log = logging.getLogger(__name__)

BASE_URLS = {
    "eu": "https://teameuapi.coros.com",
    "en": "https://teamapi.coros.com",
    "cn": "https://teamcnapi.coros.com",
}

# COROS numeric sport types -> readable names (subset; unknown codes fall back to "Sport <code>")
SPORT_NAMES = {
    100: "Outdoor Run", 101: "Indoor Run", 102: "Trail Run", 103: "Track Run", 104: "Hike",
    105: "Mountain Climb", 200: "Outdoor Bike", 201: "Indoor Bike", 203: "Gravel Bike",
    204: "Mountain Bike", 300: "Pool Swim", 301: "Open Water Swim", 400: "Gym Cardio",
    401: "GPS Cardio", 402: "Strength", 900: "Walk", 901: "Jump Rope", 903: "Elliptical",
    904: "Yoga", 1200: "Hybrid Fitness",
}


class CorosError(RuntimeError):
    pass


class CorosProvider:
    name = "coros"

    def __init__(self, email: str, password: str, region: str = "eu", client: httpx.Client | None = None):
        self._email = email
        self._pwd_md5 = hashlib.md5(password.encode()).hexdigest()  # noqa: S324 - required by the API
        self._base = BASE_URLS[region]
        self._client = client or httpx.Client(timeout=30)
        self._token: str | None = None

    @classmethod
    def from_settings(cls, s: Settings) -> CorosProvider:
        if not s.coros_email or not s.coros_password:
            raise CorosError("HC_COROS_EMAIL and HC_COROS_PASSWORD must be set for the COROS provider")
        return cls(s.coros_email, s.coros_password.get_secret_value(), s.coros_region)

    # ---- transport -------------------------------------------------------------------------

    def login(self) -> None:
        r = self._client.post(
            f"{self._base}/account/login",
            json={"account": self._email, "accountType": 2, "pwd": self._pwd_md5},
        )
        data = self._unwrap(r)
        token = data.get("accessToken") if isinstance(data, dict) else None
        if not token:
            raise CorosError("login succeeded but no accessToken was returned")
        self._token = token

    def _request(self, method: str, path: str, *, retry: bool = True, **kw: Any) -> Any:
        if not self._token:
            self.login()
        r = self._client.request(method, f"{self._base}{path}", headers={"accesstoken": self._token or ""}, **kw)
        try:
            return self._unwrap(r)
        except (CorosError, httpx.HTTPStatusError) as e:
            # Expired tokens come back as 401 or a non-"0000" result mentioning the token: log in again once.
            if retry and (r.status_code == 401 or "token" in str(e).lower()):
                self._token = None
                return self._request(method, path, retry=False, **kw)
            raise

    @staticmethod
    def _unwrap(r: httpx.Response) -> Any:
        r.raise_for_status()
        body = r.json()
        if str(body.get("result")) != "0000":
            raise CorosError(f"COROS API error {body.get('apiCode') or body.get('result')}: {body.get('message')}")
        return body.get("data")

    # ---- activities ------------------------------------------------------------------------

    def fetch_activities(self, start: date, end: date) -> list[ActivityRecord]:
        out: list[ActivityRecord] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                "/activity/query",
                params={
                    "size": 100,
                    "pageNumber": page,
                    "startDay": start.strftime("%Y%m%d"),
                    "endDay": end.strftime("%Y%m%d"),
                    "modeList": "",
                },
            ) or {}
            items = data.get("dataList") or []
            out.extend(a for a in (self._activity(i) for i in items) if a)
            total_pages = int(data.get("totalPage") or 1)
            if page >= total_pages or not items:
                return out
            page += 1

    def _activity(self, i: dict[str, Any]) -> ActivityRecord | None:
        label = i.get("labelId")
        ts = i.get("startTime")
        if not label or not ts:
            return None
        # startTimezone is reported in 15-minute units on the web API.
        tz_units = i.get("startTimezone", i.get("timezone", 0)) or 0
        tz = timezone(timedelta(minutes=int(tz_units) * 15))
        start = datetime.fromtimestamp(int(ts), tz=UTC).astimezone(tz)
        sport_code = int(i.get("sportType") or 0)
        sport = SPORT_NAMES.get(sport_code, f"Sport {sport_code}")
        return ActivityRecord(
            provider=self.name,
            external_id=str(label),
            sport=sport,
            name=i.get("name"),
            start_time=start,
            local_date=start.date(),
            duration_s=_int(i.get("totalTime") or i.get("workoutTime")),
            distance_m=_float(i.get("distance")),
            avg_hr=_int(i.get("avgHr") or i.get("avgHeartRate")),
            max_hr=_int(i.get("maxHr")),
            calories=_kcal(i.get("calorie") if i.get("calorie") is not None else i.get("calories")),
            training_load=_float(i.get("trainingLoad")),
            raw={**i, "sportTypeCode": sport_code},
        )

    def download_fit(self, activity: ActivityRecord) -> bytes | None:
        params = {"labelId": activity.external_id, "sportType": activity.raw.get("sportTypeCode", 100), "fileType": 4}
        data = None
        for method in ("POST", "GET"):  # community clients disagree on the verb; try both
            try:
                data = self._request(method, "/activity/detail/download", params=params)
                break
            except (CorosError, httpx.HTTPStatusError) as e:
                log.debug("FIT download via %s failed: %s", method, e)
        url = (data or {}).get("fileUrl") if isinstance(data, dict) else None
        if not url:
            return None
        r = self._client.get(url)
        r.raise_for_status()
        return r.content

    # ---- daily analysis (resting HR, HRV, training load) -----------------------------------

    def fetch_daily(self, start: date, end: date) -> list[DailyRecord]:
        out: dict[date, DailyRecord] = {}
        cur = start
        while cur <= end:  # the analyse endpoint is queried in 4-week windows
            chunk_end = min(end, cur + timedelta(days=27))
            data = self._request(
                "GET", "/analyse/query", params={"startDay": cur.strftime("%Y%m%d"), "endDay": chunk_end.strftime("%Y%m%d")}
            ) or {}
            for d in data.get("dayList") or []:
                rec = self._day(d)
                if rec:
                    out[rec.day] = rec
            cur = chunk_end + timedelta(days=1)
        return sorted(out.values(), key=lambda r: r.day)

    @staticmethod
    def _day(d: dict[str, Any]) -> DailyRecord | None:
        hd = d.get("happenDay")
        if not hd:
            return None
        day = datetime.strptime(str(hd), "%Y%m%d").date()
        v: dict[str, Any] = {
            "resting_hr": _int(d.get("rhr")),
            "hrv_avg": _float(d.get("avgSleepHrv")),
            "training_load": _float(d.get("trainingLoad")),
            "load_short": _float(d.get("t7d") or d.get("ati")),
            "load_long": _float(d.get("t28d") or d.get("cti")),
            "load_ratio": _float(d.get("trainingLoadRatio")),
        }
        base, sd = _float(d.get("sleepHrvBase")), _float(d.get("sleepHrvSd"))
        if base is not None and sd is not None:
            v["hrv_lo"], v["hrv_hi"] = round(base - sd, 1), round(base + sd, 1)
        return DailyRecord(day=day, values={k: x for k, x in v.items() if x is not None})

    def fetch_fitness(self) -> FitnessRecord | None:
        today = date.today()
        data = self._request(
            "GET", "/analyse/query",
            params={"startDay": (today - timedelta(days=6)).strftime("%Y%m%d"), "endDay": today.strftime("%Y%m%d")},
        ) or {}
        s = data.get("summaryInfo") or {}
        vo2 = _float(s.get("vo2max") or s.get("vo2Max"))
        # lthr pace is reported as seconds per km on some accounts; validate the range.
        pace = _int(s.get("lthrPace") or s.get("ltsp"))
        if pace is not None and not 150 <= pace <= 600:
            pace = None
        level = _float(s.get("staminaLevel") or s.get("runningLevel"))
        if vo2 is None and pace is None and level is None:
            return None
        return FitnessRecord(day=today, vo2max=vo2, threshold_pace_s=pace, running_level=level)


def _int(x: Any) -> int | None:
    try:
        return None if x in (None, "") else int(float(x))
    except (TypeError, ValueError):
        return None


def _float(x: Any) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def _kcal(x: Any) -> int | None:
    v = _float(x)
    if v is None:
        return None
    return int(v / 1000) if v > 20000 else int(v)  # web API reports small calories on some endpoints
