"""Real portable lock identities and competing processes; no backend service."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from app.harness.persistence import path_lock
from app.storage.research_creation_store import ResearchCreationStore


ROOT = Path(__file__).resolve().parents[3]


def _environment() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(ROOT / "backend")}


def test_path_lock_keeps_identity_and_excludes_a_real_waiter(tmp_path: Path) -> None:
    lock_path = tmp_path / "shared.lock"
    blocked = tmp_path / "waiter-blocked"
    script = """
import json,sys
from pathlib import Path
from filelock import Timeout
from app.harness.persistence import path_lock
path, blocked = map(Path, sys.argv[1:])
try:
    with path_lock(path, timeout=0):
        raise AssertionError('another process still owns this lock')
except Timeout:
    blocked.write_text('actual acquisition timed out')
with path_lock(path, timeout=20):
    stat = path.stat()
    print(json.dumps({'identity': [stat.st_dev, stat.st_ino]}))
"""
    process: subprocess.Popen[str] | None = None
    try:
        with path_lock(lock_path):
            identity = (lock_path.stat().st_dev, lock_path.stat().st_ino)
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(lock_path), str(blocked)],
                cwd=ROOT, env=_environment(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 15
            while not blocked.exists():
                assert process.poll() is None, process.communicate(timeout=5)
                assert time.monotonic() < deadline, "real contender did not reach the lock"
                time.sleep(0.01)
            assert process.poll() is None
            assert (lock_path.stat().st_dev, lock_path.stat().st_ino) == identity
        assert lock_path.is_file()
        assert (lock_path.stat().st_dev, lock_path.stat().st_ino) == identity
        stdout, stderr = process.communicate(timeout=25)
        assert process.returncode == 0, stderr
        assert tuple(json.loads(stdout)["identity"]) == identity
        assert (lock_path.stat().st_dev, lock_path.stat().st_ino) == identity
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_creation_lease_probe_agrees_across_real_processes_and_keeps_identity(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    store = ResearchCreationStore(runs)
    store.initialize()
    request_id = "portable-lock-request"
    lock_path = store.root / "leases" / (hashlib.sha256(request_id.encode()).hexdigest() + ".lock")
    script = """
import json,sys
from pathlib import Path
from app.storage.research_creation_store import ResearchCreationStore
store = ResearchCreationStore(Path(sys.argv[1]))
active = store.owner_active(sys.argv[2])
with store.lease(sys.argv[2]) as acquired:
    print(json.dumps({'active': active, 'acquired': acquired}))
"""
    with store.lease(request_id) as owned:
        assert owned
        identity = (lock_path.stat().st_dev, lock_path.stat().st_ino)
        result = subprocess.run(
            [sys.executable, "-c", script, str(runs), request_id], cwd=ROOT, env=_environment(),
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"active": True, "acquired": False}
        assert (lock_path.stat().st_dev, lock_path.stat().st_ino) == identity
    assert lock_path.is_file()
    assert (lock_path.stat().st_dev, lock_path.stat().st_ino) == identity
    assert store.owner_active(request_id) is False
    with store.lease(request_id) as reacquired:
        assert reacquired
        assert (lock_path.stat().st_dev, lock_path.stat().st_ino) == identity
