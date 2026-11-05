from datetime import date

import httpx
import respx

from homecoach.providers.coros import CorosProvider

BASE = "https://teameuapi.coros.com"
OK = {"result": "0000", "message": "OK"}


@respx.mock
def test_login_activities_daily_and_token_refresh():
    login = respx.post(f"{BASE}/account/login").mock(return_value=httpx.Response(200, json={**OK, "data": {"accessToken": "t1"}}))
    respx.get(f"{BASE}/activity/query").mock(
        side_effect=[
            httpx.Response(200, json={"result": "1019", "message": "access token invalid"}),  # expired -> re-login
            httpx.Response(
                200,
                json={
                    **OK,
                    "data": {
                        "totalPage": 1,
                        "dataList": [
                            {
                                "labelId": "481501418031710308",
                                "sportType": 100,
                                "name": "Algés Run",
                                "startTime": 1793728157,
                                "startTimezone": 0,
                                "totalTime": 4338,
                                "distance": 12130,
                                "avgHr": 151,
                                "calorie": 942000,
                                "trainingLoad": 95,
                            },
                            {"labelId": "481474070095888484", "sportType": 402, "startTime": 1793619509, "totalTime": 2974},
                            {"sportType": 100},  # malformed: skipped
                        ],
                    },
                },
            ),
        ]
    )
    respx.get(f"{BASE}/analyse/query").mock(
        return_value=httpx.Response(
            200,
            json={
                **OK,
                "data": {
                    "dayList": [
                        {"happenDay": 20261104, "rhr": 55, "avgSleepHrv": 37, "trainingLoadRatio": 1.06, "t7d": 62, "t28d": 58},
                        {"happenDay": 20261105, "rhr": 54},
                    ]
                },
            },
        )
    )

    p = CorosProvider("me@example.com", "secret", "eu")
    acts = p.fetch_activities(date(2026, 11, 1), date(2026, 11, 5))
    assert login.call_count == 2
    assert [a.type for a in acts] == ["Run", "Strength"]
    run = acts[0]
    assert run.id == "coros:481501418031710308" and run.distance_m == 12130 and run.calories == 942
    assert run.local_date == date(2026, 11, 3)

    days = p.fetch_daily(date(2026, 11, 4), date(2026, 11, 5))
    assert days[0].values == {"resting_hr": 55, "hrv_avg": 37.0, "load_short": 62.0, "load_long": 58.0, "load_ratio": 1.06}
    assert days[1].values == {"resting_hr": 54}
