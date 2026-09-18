"""Raw loop events cross the real bridge progress adapter without affecting work."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.base import RunRequest
from app.agents.experiment.agent import ExperimentAgent
from app.bridge.agent_progress import build_agent_progress_sink
from app.storage.run_store import RunStore


@pytest.mark.asyncio
async def test_raw_loop_events_become_valid_durable_public_progress(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="progress contract", project="regression")
    request = RunRequest(project=run.project, user_request="Plan an experiment",
        progress_sink=build_agent_progress_sink(run=run, node_key="experiment"))
    emit = ExperimentAgent().loop_progress_sink(request, "invocation-1")
    assert emit is not None
    for kind in ("started", "action", "observation", "candidate", "validation", "review_unit", "review_format_repaired", "finished"):
        await emit({"kind": kind, "phase": "act"})
    rows = [json.loads(line) for line in (run.root / "events/agent_events.jsonl").read_text().splitlines()]
    assert len(rows) == 8
    assert all(row["message"] and row["invocation"] == "invocation-1" and row["agent"] == "experiment" for row in rows)


@pytest.mark.asyncio
async def test_actual_progress_storage_failure_does_not_fail_agent(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="progress storage failure", project="regression")
    (run.root / "events/agent_events.jsonl").mkdir()
    request = RunRequest(project=run.project, user_request="Plan an experiment",
        progress_sink=build_agent_progress_sink(run=run, node_key="experiment"))
    emit = ExperimentAgent().loop_progress_sink(request, "invocation-2")
    assert emit is not None
    await emit({"kind": "started", "phase": "act"})
    assert (run.root / "events/agent_events.jsonl").is_dir()
