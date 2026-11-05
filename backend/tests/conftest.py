from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from homecoach.api import main as api
from homecoach.collector.sync import upsert_activity, upsert_daily
from homecoach.db import Base
from homecoach.providers.base import ActivityRecord, DailyRecord


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    def session():
        s = Session()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    api.app.dependency_overrides[api.get_session] = session
    today = api.today()
    with Session() as s:
        for i in range(0, 28, 2):
            d = today - timedelta(days=i + 1)
            start = datetime(d.year, d.month, d.day, 7, tzinfo=UTC)
            upsert_activity(
                s, ActivityRecord("coros", f"r{i}", "Outdoor Run", start, d, duration_s=3600, distance_m=10000, avg_hr=148)
            )
        for i in range(28):
            upsert_daily(
                s,
                DailyRecord(
                    today - timedelta(days=i),
                    {"steps": 12000, "resting_hr": 54, "hrv_avg": 40, "sleep_min": 480, "load_ratio": 1.0},
                ),
            )
        s.commit()
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()
