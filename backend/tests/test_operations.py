"""Operational paths: the FIT inbox, the collector's schedule, the CLI and the Claude API client. Synthetic data only."""

import json
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from homecoach import cli, llm
from homecoach.api import main as api
from homecoach.collector import main as collector
from homecoach.collector.sync import import_fit_inbox
from homecoach.db import Base
from homecoach.models import Activity
from homecoach.providers.fit_import import parse_fit

FIT = Path(__file__).parent / "fixtures" / "synthetic_run.fit"


# ---- FIT inbox -------------------------------------------------------------------------------------


def test_parse_fit_reads_the_session():
    rec = parse_fit(FIT.read_bytes(), "Europe/Lisbon")
    assert rec is not None
    assert (rec.provider, rec.type, rec.sport) == ("fit", "Run", "Running (trail)")
    assert rec.local_date == date(2026, 10, 18) and rec.start_time.hour == 8  # 07:30 UTC is 08:30 in Lisbon (summer)
    assert (rec.duration_s, rec.distance_m, rec.avg_hr, rec.max_hr, rec.calories) == (3000, 10000.0, 148, 171, 680)


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    s = api.get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    s.fit_inbox.mkdir(parents=True)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as db:
        yield s, db
    engine.dispose()


def test_inbox_imports_archives_and_skips_duplicates(inbox):
    s, db = inbox
    (s.fit_inbox / "run.fit").write_bytes(FIT.read_bytes())
    (s.fit_inbox / "notes.txt").write_text("not a FIT file")
    assert import_fit_inbox(db, s) == 1
    row = db.get(Activity, parse_fit(FIT.read_bytes()).id)
    assert row is not None and Path(row.fit_path).read_bytes() == FIT.read_bytes()
    assert Path(row.fit_path).is_relative_to(s.fit_dir / "2026" / "10")
    assert sorted(p.name for p in s.fit_inbox.iterdir()) == ["notes.txt"]  # imported files leave the inbox
    (s.fit_inbox / "again.fit").write_bytes(FIT.read_bytes())
    assert import_fit_inbox(db, s) == 0 and not (s.fit_inbox / "again.fit").exists()  # duplicate: dropped
    assert db.query(Activity).count() == 1


def test_unreadable_fit_files_move_to_failed(inbox):
    s, db = inbox
    (s.fit_inbox / "broken.fit").write_bytes(b"not really a fit file")
    assert import_fit_inbox(db, s) == 0
    assert (s.fit_inbox / "failed" / "broken.fit").exists() and db.query(Activity).count() == 0


# ---- collector schedule ----------------------------------------------------------------------------


class FakeScheduler:
    def __init__(self, timezone):
        self.timezone, self.jobs, self.started = timezone, {}, False

    def add_job(self, fn, trigger, **kw):
        self.jobs[kw["id"]] = (fn, trigger, kw)

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        pass


def test_collector_schedules_every_job_and_syncs_once_at_start(monkeypatch, tmp_path):
    s = api.get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    made, runs = [], []
    monkeypatch.setattr(collector, "BlockingScheduler", lambda timezone: made.append(FakeScheduler(timezone)) or made[-1])
    monkeypatch.setattr(collector, "run_once", lambda full=False: runs.append(full))
    monkeypatch.setattr(collector.signal, "signal", lambda *_: None)
    collector.main()
    sched = made[0]
    assert sched.started and runs == [False] and (tmp_path / ".collector-heartbeat").exists()
    jobs = sched.jobs
    assert set(jobs) == {"heartbeat", "incremental", "full", "coach-review"}
    assert jobs["incremental"][2]["minutes"] == s.sync_interval_minutes and jobs["incremental"][2]["args"] == [False]
    assert jobs["full"][2]["hour"] == s.full_sync_hour and jobs["full"][2]["args"] == [True]
    assert (jobs["coach-review"][2]["day_of_week"], jobs["coach-review"][2]["hour"]) == (s.coach_review_dow, s.coach_review_hour)
    assert all(j[2].get("max_instances", 1) == 1 for j in jobs.values())  # syncs never overlap


def test_a_failing_sync_never_stops_the_collector(monkeypatch, tmp_path):
    monkeypatch.setattr(api.get_settings(), "data_dir", tmp_path)

    def boom(full=False):
        raise RuntimeError("provider down")

    monkeypatch.setattr(collector, "run_once", boom)
    collector._safe(False)  # logs, doesn't raise
    assert (tmp_path / ".collector-heartbeat").exists()


def test_weekly_review_runs_only_with_ai_and_after_a_sync(monkeypatch):
    calls = []
    monkeypatch.setattr(collector, "run_once", lambda full=False: calls.append("sync"))
    monkeypatch.setattr(api, "run_review", lambda: calls.append("review") or {"suggestions": []})
    monkeypatch.setattr(collector, "_outlook_target_race", lambda: calls.append("outlook"))
    collector._weekly_review()
    assert calls == []  # AI off: nothing to do
    monkeypatch.setattr(api.get_settings(), "llm_provider", "openai")
    collector._weekly_review()
    assert calls == ["sync", "review", "outlook"]


# ---- CLI ---------------------------------------------------------------------------------------------


def test_cli_migrate_creates_the_schema(monkeypatch, tmp_path):
    from homecoach.config import get_settings

    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("HC_DATABASE_URL", url)
    get_settings.cache_clear()  # settings are cached per process; re-read the environment
    try:
        monkeypatch.setattr(sys, "argv", ["homecoach", "migrate"])
        cli.main()
        engine = create_engine(url)
        tables = set(inspect(engine).get_table_names())
        engine.dispose()
        assert {"activities", "daily_metrics", "races", "nutrition_profile", "food_entries", "alembic_version"} <= tables
    finally:
        monkeypatch.delenv("HC_DATABASE_URL")
        get_settings.cache_clear()


def test_cli_sync_runs_one_sync(monkeypatch):
    seen = []
    monkeypatch.setattr(collector, "run_once", lambda full=False: seen.append(full))
    for argv, expect in ((["homecoach", "sync"], False), (["homecoach", "sync", "--full"], True)):
        monkeypatch.setattr(sys, "argv", argv)
        cli.main()
        assert seen[-1] is expect
    monkeypatch.setattr(sys, "argv", ["homecoach"])
    with pytest.raises(SystemExit):
        cli.main()  # a command is required


# ---- Claude API path in the LLM client ---------------------------------------------------------------


@respx.mock
def test_anthropic_tool_loop(monkeypatch):
    s = api.get_settings()
    monkeypatch.setattr(s, "llm_provider", "anthropic")
    monkeypatch.setattr(s, "llm_model", "claude-test")
    from pydantic import SecretStr

    monkeypatch.setattr(s, "llm_api_key", SecretStr("test-key"))
    tool_use = {
        "content": [
            {"type": "text", "text": "Let me save it."},
            {"type": "tool_use", "id": "t1", "name": "save", "input": {"n": 7}},
        ]
    }
    final = {"content": [{"type": "text", "text": "<think>hidden</think>Saved 7."}]}
    route = respx.post(llm.ANTHROPIC_URL).mock(side_effect=[httpx.Response(200, json=tool_use), httpx.Response(200, json=final)])
    got = []
    tool = llm.Tool(
        "save",
        "Save a number",
        {"type": "object", "properties": {"n": {"type": "integer"}}},
        lambda a: got.append(a["n"]) or "saved",
    )
    res = llm.run(s, "SYS", [{"role": "user", "content": "save 7"}], [tool])
    assert res.text == "Saved 7." and got == [7]
    first, second = (json.loads(c.request.content) for c in route.calls)
    assert route.calls[0].request.headers["x-api-key"] == "test-key"
    assert first["system"] == "SYS" and first["tools"][0]["input_schema"]["properties"]["n"]["type"] == "integer"
    assert second["messages"][-1]["content"][0] == {"type": "tool_result", "tool_use_id": "t1", "content": "saved"}


@respx.mock
def test_llm_errors_are_reported_not_raised_raw(monkeypatch):
    s = api.get_settings()
    monkeypatch.setattr(s, "llm_provider", "openai")
    monkeypatch.setattr(s, "llm_base_url", "http://llm.test/v1")
    respx.post("http://llm.test/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(llm.LLMError, match="Couldn't reach the LLM"):
        llm.run(s, "SYS", [{"role": "user", "content": "hi"}], [])
    monkeypatch.setattr(s, "llm_provider", "none")
    with pytest.raises(llm.LLMError, match="No LLM is configured"):
        llm.run(s, "SYS", [{"role": "user", "content": "hi"}], [])
