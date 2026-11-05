from contextlib import contextmanager

import pytest
from sqlalchemy import select

from homecoach import coach, llm
from homecoach.api import main as api
from homecoach.models import CheckIn, CoachNote, CoachSuggestion


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


@pytest.mark.parametrize("ai,failure", [(False, False), (True, False), (True, True)])
def test_full_resync_rebuilds_plan_and_refreshes_coach(scope, monkeypatch, ai, failure):
    calls = []
    monkeypatch.setattr("homecoach.collector.main.run_once", lambda full: calls.append(("sync", full)) or {"days": 12})
    monkeypatch.setattr(llm, "enabled", lambda _: ai)
    compute = api.compute_plan

    def plan(db):
        calls.append(("plan", True))
        return compute(db)

    def review():
        calls.append(("coach", True))
        if failure:
            raise llm.LLMError("model is unavailable")
        return {"suggestions": [1, 2]}

    monkeypatch.setattr(api, "compute_plan", plan)
    monkeypatch.setattr(api, "run_review", review)
    api._resync.clear()
    assert api._resync_lock.acquire(blocking=False)
    api._resync_run()
    result = dict(api._resync)
    assert calls[:2] == [("sync", True), ("plan", True)]
    assert result["stages"]["plan"] == "completed" and result["error"] is None
    assert result["stages"]["coach"] == ("skipped" if not ai else "failed" if failure else "completed")
    assert (("coach", True) in calls) == ai
    assert not result["running"] and result["finished_at"]
    if failure:
        assert "retained" in result["warnings"][0]


@pytest.mark.parametrize("fail", [False, True])
def test_review_replaces_only_pending_suggestions_after_success(scope, monkeypatch, fail):
    with scope() as db:
        rows = [
            CoachSuggestion(day=api.today(), action="skip", reason="old", status=status)
            for status in ("pending", "accepted", "dismissed")
        ]
        db.add_all(rows)
        db.add(CheckIn(day=api.today(), text="keep this check-in"))
        db.commit()
        ids = {x.status: x.id for x in rows}

    def run(_settings, db, *_args):
        new = CoachSuggestion(day=api.today(), action="skip", reason="new", status="pending")
        db.add(new)
        db.add(CoachNote(kind="review", text="new review"))
        db.flush()
        if fail:
            raise llm.LLMError("failed after writing a candidate")
        return {"suggestions": [coach.suggestion_out(new)]}

    monkeypatch.setattr(coach, "run", run)
    if fail:
        with pytest.raises(llm.LLMError):
            api.run_review()
    else:
        api.run_review()
    with scope() as db:
        assert (db.get(CoachSuggestion, ids["pending"]) is not None) == fail
        assert db.get(CoachSuggestion, ids["accepted"]).status == "accepted"
        assert db.get(CoachSuggestion, ids["dismissed"]).status == "dismissed"
        assert len(list(db.scalars(select(CheckIn)))) == 1
        assert len(list(db.scalars(select(CoachNote)))) == (0 if fail else 1)
