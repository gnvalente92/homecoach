import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from homecoach import coros_auth
from homecoach.api import main as api
from homecoach.collector import main as collector
from homecoach.locking import execution_lock
from homecoach.models import CheckIn


def test_execution_lock_is_shared_between_processes_and_released(tmp_path):
    path = tmp_path / "sync.lock"
    code = (
        "import sys\nfrom pathlib import Path\nfrom homecoach.locking import execution_lock\n"
        "with execution_lock(Path(sys.argv[1])) as acquired: print(acquired)"
    )
    with execution_lock(path) as acquired:
        assert acquired
        other = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=True)
        assert other.stdout.strip() == "False"
    with execution_lock(path) as acquired:
        assert acquired


@pytest.mark.parametrize("disconnect", [False, True])
def test_token_rotation_survives_failed_sync_without_restoring_disconnected_account(client, monkeypatch, tmp_path, disconnect):
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

    old = {"tokens": {"access_token": "old", "refresh_token": "old-refresh"}}
    with scope() as db:
        coros_auth.save(db, old)
    state = {"tokens": dict(old["tokens"])}
    provider = SimpleNamespace(s=SimpleNamespace(state=state))
    monkeypatch.setattr(collector, "session_scope", scope)
    monkeypatch.setattr(collector, "import_fit_inbox", lambda *_: 0)
    monkeypatch.setattr(collector, "reset_web_api_units", lambda *_: None)
    monkeypatch.setattr(collector, "get_provider", lambda *_: provider)
    monkeypatch.setattr(api.get_settings(), "provider", "coros-mcp")
    monkeypatch.setattr(api.get_settings(), "data_dir", tmp_path)

    def fail(db, *_args, **_kwargs):
        state.update(tokens={"access_token": "new", "refresh_token": "new-refresh"}, changed=True)
        if disconnect:
            with scope() as other:
                coros_auth.disconnect(other)
        db.add(CheckIn(day=api.today(), text="must roll back"))
        db.flush()
        raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr(collector, "sync_provider", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        collector.run_once()
    with scope() as db:
        saved = coros_auth.load(db)
        assert saved.get("tokens", {}).get("access_token") == (None if disconnect else "new")
        assert not list(db.scalars(select(CheckIn)))
    assert client.get("/api/config").json()["sync"]["collector-status"]["error"] == "RuntimeError"
