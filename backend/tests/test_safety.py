import gzip
import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from homecoach.api import main as api
from homecoach.api.static import mount_web
from homecoach.config import Settings
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
        page = client.get("/plan")
        assert page.text == "app" and page.headers["cache-control"] == "no-cache"  # updates show without a hard refresh
        for path in ("/..%2fprivate.txt", "/linked.txt", "/api/missing"):
            assert client.get(path).status_code == 404


@pytest.mark.parametrize("user,password", [("athlete", ""), ("", "secret")])
def test_partial_auth_fails_closed(user, password):
    with pytest.raises(ValidationError, match="set both"):
        Settings(_env_file=None, auth_user=user, auth_password=password)


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


def test_missing_and_stale_metrics_do_not_claim_readiness():
    today = api.today()
    for days in ([], [E.Day(today - timedelta(days=10), resting_hr=54, hrv_avg=40, load_ratio=1.0, sleep_min=480)]):
        result = E.readiness(E.baseline([], days, today))
        assert result.status == "Unknown"
        assert result.score is None and result.zone is None and result.factor == 1.0
