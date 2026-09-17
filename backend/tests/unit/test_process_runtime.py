"""Real processes verify cleanup and environment isolation; no service doubles."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from app.execution.adapters.process import ProcessAdapter
from app.harness.tools.process_runtime import communicate_process, start_process, terminate_process_tree


@pytest.mark.asyncio
async def test_process_receives_scoped_environment_and_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-worker")
    monkeypatch.setenv("PYTHONSTARTUP", str(tmp_path / "untrusted.py"))
    script = "import json,os; print(json.dumps({'secret': 'OPENAI_API_KEY' in os.environ, 'startup': 'PYTHONSTARTUP' in os.environ, 'cwd': os.getcwd()}))"
    process = await start_process([sys.executable, "-c", script], cwd=tmp_path)
    stdout, _ = await communicate_process(process, timeout=5)
    assert process.returncode == 0
    assert json.loads(stdout) == {"secret": False, "startup": False, "cwd": str(tmp_path)}


@pytest.mark.skipif(os.name != "posix" or not Path("/proc").exists(), reason="Linux process-tree assertions")
@pytest.mark.parametrize("cancel", [True, False])
@pytest.mark.asyncio
async def test_cancellation_and_timeout_kill_actual_descendant(cancel: bool) -> None:
    script = (
        "import json,os,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(json.dumps({'parent':os.getpid(),'child':child.pid}), flush=True); time.sleep(60)"
    )
    process = await start_process([sys.executable, "-c", script])
    assert process.stdout is not None
    try:
        ids = json.loads(await asyncio.wait_for(process.stdout.readline(), timeout=5))
        operation = asyncio.create_task(communicate_process(process, timeout=30 if cancel else 0.05))
        if cancel:
            await asyncio.sleep(0.02)
            operation.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await operation
        assert process.returncode is not None
        child_state = Path(f"/proc/{ids['child']}/stat")
        # An orphaned zombie can await PID 1 reaping, but cannot execute or hold resources.
        for _ in range(50):
            if not child_state.exists() or child_state.read_text().split(") ", 1)[1].startswith("Z "):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("descendant remained running after the parent operation stopped")
    finally:
        await terminate_process_tree(process)


def test_requested_os_isolation_fails_before_launch() -> None:
    with pytest.raises(ValueError, match="OS isolation backend unavailable"):
        ProcessAdapter(name="requires-container", argv=(sys.executable,), require_isolation=True)


@pytest.mark.asyncio
async def test_unknown_backend_cannot_fall_back_to_local(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="OS isolation backend unavailable"):
        await start_process([sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"], backend="container")
    assert not marker.exists()
