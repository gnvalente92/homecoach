import gzip
import os
import socket
import subprocess
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from homecoach import websearch
from homecoach.api import main as api
from homecoach.api.static import mount_web
from homecoach.config import Settings
from homecoach.models import CoachSuggestion
from homecoach.plan import engine as E


def test_static_files_stay_inside_build(tmp_path):
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("app")
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    (root / "linked.txt").symlink_to(outside)
    app = FastAPI()
    mount_web(app, root)
    with TestClient(app) as client:
        assert client.get("/plan").text == "app"
        for path in ("/..%2fprivate.txt", "/linked.txt", "/api/missing"):
            assert client.get(path).status_code == 404


@pytest.mark.parametrize("user,password", [("athlete", ""), ("", "secret")])
def test_partial_auth_fails_closed(user, password):
    with pytest.raises(ValidationError, match="set both"):
        Settings(_env_file=None, auth_user=user, auth_password=password)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "169.254.169.254", "192.168.1.1", "10.0.0.1"])
def test_page_reader_rejects_private_addresses(host, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [(0, 0, 0, "", (host, 443))])
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("private request was sent"))) as client,
        pytest.raises(ValueError, match="public internet"),
    ):
        websearch.fetch_page("https://race.example/", client)


def test_page_reader_checks_redirects_and_body_limit(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, *_a, **_k: [(0, 0, 0, "", (host if host == "127.0.0.1" else "93.184.216.34", 443))]
    )
    seen = []

    def redirect(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    with httpx.Client(transport=httpx.MockTransport(redirect)) as client, pytest.raises(ValueError, match="public internet"):
        websearch.fetch_page("https://race.example", client)
    assert seen == ["race.example"]
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 100))) as client:
        monkeypatch.setattr(websearch, "MAX_PAGE_BYTES", 50)
        with pytest.raises(ValueError, match="too large"):
            websearch.fetch_page("https://race.example", client)


def test_page_reader_pins_public_address_and_tls_name(monkeypatch):
    monkeypatch.setattr(websearch, "_public_address", lambda _: "93.184.216.34")
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, text="<p>Race details</p>")

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    assert websearch.fetch_page("https://race.example/event") == "Race details"
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "race.example"
    assert seen[0].extensions["sni_hostname"] == "race.example"


@pytest.mark.parametrize("failure", ["dump", "gzip", None])
def test_backup_never_publishes_failed_dump_or_rotates_old_backups(tmp_path, failure):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dump = bin_dir / "pg_dump"
    dump.write_text(
        '#!/bin/sh\nwhile [ "$1" != "-f" ]; do shift; done\nprintf "synthetic SQL" > "$2"\nexit '
        + ("1" if failure == "dump" else "0")
        + "\n"
    )
    dump.chmod(0o755)
    if failure == "gzip":
        compressor = bin_dir / "gzip"
        compressor.write_text("#!/bin/sh\nexit 1\n")
        compressor.chmod(0o755)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / "homecoach-2000-01-01.sql.gz"
    old.write_bytes(b"keep")
    os.utime(old, (0, 0))
    script = Path(__file__).resolve().parents[2] / "scripts/backup.sh"
    result = subprocess.run(
        ["/bin/sh", str(script)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "BACKUP_DIR": str(backup_dir),
            "BACKUP_ONCE": "1",
            "KEEP_DAYS": "14",
        },
        capture_output=True,
    )
    published = list(backup_dir.glob("homecoach-*.sql.gz"))
    if failure:
        assert result.returncode != 0
        assert published == [old]
    else:
        assert result.returncode == 0
        assert not old.exists()
        assert gzip.decompress(published[0].read_bytes()) == b"synthetic SQL"
    assert not list(backup_dir.glob(".homecoach-*"))


def test_accepting_separate_suggestions_rechecks_combined_volume(client, monkeypatch):
    monday = E.next_monday(api.today())
    week = [E.Session(monday + timedelta(days=d), "Easy run", km) for d, km in [(1, 8), (3, 8), (6, 14)]]
    baseline = E.baseline([], [], api.today())
    baseline.longest_km = 14

    def plan(db):
        changes = api.accepted_adjustments(db, monday)
        return {
            "baseline": baseline,
            "this_week": {"sessions": [], "target_km": 0},
            "next_week": E.apply_adjustments(week, changes),
            "next_week_target_km": 30,
        }

    monkeypatch.setattr(api, "compute_plan", plan)
    factory = api.app.dependency_overrides[api.get_session]
    with next(factory()) as db:
        rows = [
            CoachSuggestion(day=monday + timedelta(days=d), action="replace", session="Easy run", km=12, reason="test")
            for d in (1, 3)
        ]
        db.add_all(rows)
        db.commit()
        ids = [x.id for x in rows]
    assert client.post(f"/api/coach/suggestions/{ids[0]}", json={"status": "accepted"}).status_code == 200
    rejected = client.post(f"/api/coach/suggestions/{ids[1]}", json={"status": "accepted"})
    assert rejected.status_code == 409 and "safe limit" in rejected.json()["detail"]
    assert client.post(f"/api/coach/suggestions/{ids[0]}", json={"status": "accepted"}).status_code == 200


def test_missing_and_stale_metrics_do_not_claim_readiness():
    today = api.today()
    for days in ([], [E.Day(today - timedelta(days=10), resting_hr=54, hrv_avg=40, load_ratio=1.0, sleep_min=480)]):
        result = E.readiness(E.baseline([], days, today))
        assert result.status == "Unknown"
        assert result.score is None and result.zone is None and result.factor == 1.0


def test_shorten_cannot_lengthen_and_move_cannot_overwrite_completed_run():
    today = api.today()
    week = [E.Session(today, "Easy run", 6), E.Session(today + timedelta(days=1), "Run", 8, status="Done")]
    assert E.check_adjustment(E.Adjustment(today, "shorten", km=8), week, 30, 14, today)
    assert E.check_adjustment(E.Adjustment(today, "replace", km=float("nan")), week, 30, 14, today)
    assert E.check_adjustment(E.Adjustment(today, "move", move_to=today), week, 30, 14, today)
    assert E.check_adjustment(E.Adjustment(today, "move", move_to=today + timedelta(days=1)), week, 30, 14, today)
