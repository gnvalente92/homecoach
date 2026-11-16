from contextlib import contextmanager

import pytest

from homecoach.api import main as api


@pytest.fixture
def scope(client, monkeypatch, tmp_path):
    factory = api.app.dependency_overrides[api.get_session]

    @contextmanager
    def scope():
        gen = factory()
        db = next(gen)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            gen.close()

    monkeypatch.setattr("homecoach.db.session_scope", scope)
    monkeypatch.setattr(api.get_settings(), "data_dir", tmp_path)
    return scope


def test_full_resync_syncs_then_rebuilds_the_plan(scope, monkeypatch):
    calls = []
    monkeypatch.setattr("homecoach.collector.main.run_once", lambda full: calls.append(("sync", full)) or {"days": 12})
    compute = api.compute_plan

    def plan(db):
        calls.append(("plan", True))
        return compute(db)

    monkeypatch.setattr(api, "compute_plan", plan)
    api._resync.clear()
    assert api._resync_lock.acquire(blocking=False)
    api._resync_run()
    result = dict(api._resync)
    assert calls == [("sync", True), ("plan", True)]
    assert result["stages"] == {"sync": "completed", "plan": "completed"} and result["error"] is None
    assert not result["running"] and result["finished_at"]


def test_full_resync_reports_a_skipped_sync(scope, monkeypatch):
    monkeypatch.setattr("homecoach.collector.main.run_once", lambda full: {"mode": "skipped"})
    api._resync.clear()
    assert api._resync_lock.acquire(blocking=False)
    api._resync_run()
    assert "Another sync is running" in api._resync["error"]
    assert api._resync["stages"] == {"sync": "failed", "plan": "skipped"}
