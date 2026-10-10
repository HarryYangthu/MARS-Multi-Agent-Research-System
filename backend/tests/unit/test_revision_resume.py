"""Original-feedback restoration against unmodified actual traces."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.bridge.revision_resume import resume_revision_reason
from app.storage.run_store import RunStore
from tests.unit.test_run_recovery import checkpoint, session_at


def test_regular_interrupted_task_does_not_gain_revision_feedback(tmp_path: Path) -> None:
    _, session = session_at(tmp_path)
    checkpoint(session)
    from app.bridge.task_runtime import resumable_task
    task = resumable_task(session.run, "idea")
    assert resume_revision_reason(session.run, "idea", task.invocation_id) == ""
    with pytest.raises(ValueError, match="another invocation"):
        resume_revision_reason(session.run, "idea", "different-invocation")


def test_actual_interrupted_revision_restores_exact_original_feedback() -> None:
    configured = os.environ.get("MARS_TEST_REVISION_ARCHIVE_ROOT")
    if not configured:
        pytest.skip("requires an actual interrupted revision; no substitute model trace is generated")
    root = Path(configured).resolve()
    run = RunStore(root.parent).get(root.name)
    assert run is not None
    from app.bridge.task_runtime import resumable_task
    task = resumable_task(run, "idea")
    assert "human_revision_request" in task.required_context_refs
    reason = resume_revision_reason(run, "idea", task.invocation_id)
    assert reason.strip()
    assert "保留" in reason
