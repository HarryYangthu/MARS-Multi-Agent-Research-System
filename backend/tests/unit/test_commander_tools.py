from __future__ import annotations

import json
from pathlib import Path

import pytest
import numpy as np

from app.bridge.commander_session import CommanderSession
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore
from app.storage.data_source_store import DataSourceStore


@pytest.mark.asyncio
async def test_commander_tool_chain_writes_run_event(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    orch = Orchestrator(run_store=store, bus=InProcessEventBus())
    session = CommanderSession(conv_id="conv_test", project="pimc")
    ctx = ToolContext(orchestrator=orch, session=session, run_store=store)

    result = await execute_tool(
        "run.create",
        {"entrypoint": "idea", "user_request": "Draft a literature-only research question; no execution requested.",
         "repository_scope": "not_applicable", "routing_reason": "Literature-only draft, no code task."},
        ctx,
    )

    assert result["ok"] is True
    run = store.get(str(result["run_id"]))
    assert run is not None
    path = run.subdir("events") / "commander_tool_events.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["schema"] == "event.v1"
    assert rows[-1]["kind"] == "commander.tool_completed"
    assert rows[-1]["payload"]["tool"] == "run.create"
    assert rows[-1]["source"]["conversation_id"] == "conv_test"
    assert result['entrypoint'] == 'idea'


@pytest.mark.asyncio
async def test_uninspected_connected_task_is_not_created(tmp_path: Path) -> None:
    store = RunStore(tmp_path / 'runs')
    ctx = ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()),
                      CommanderSession(conv_id='unchecked', project='pimc'), store)
    result = await execute_tool('create_and_start_run', {'entrypoint': 'pipeline', 'user_request': 'Run existing model'}, ctx)
    assert not result['ok'] and result['status'] == 'repository_inspection_required'
    assert ctx.session.linked_run_id is None
    assert not list((tmp_path / 'runs').iterdir())


@pytest.mark.asyncio
async def test_non_code_exemption_requires_a_reason(tmp_path: Path) -> None:
    store = RunStore(tmp_path / 'runs')
    ctx = ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()),
                      CommanderSession(conv_id='unexplained', project='pimc'), store)
    result = await execute_tool('run.create', {'repository_scope': 'not_applicable'}, ctx)
    assert not result['ok'] and ctx.session.linked_run_id is None


@pytest.mark.asyncio
async def test_fresh_execution_without_delivery_fails_before_creating_a_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / 'runs')
    ctx = ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()),
                      CommanderSession(conv_id='no-delivery', project='pimc'), store)
    result = await execute_tool('create_and_start_run', {'entrypoint': 'execution',
        'repository_scope': 'not_applicable', 'routing_reason': 'Admission test only; no process execution.'}, ctx)
    assert not result['ok'] and result['status'] == 'execution_delivery_required'
    assert ctx.session.linked_run_id is None
    assert not list((tmp_path / 'runs').iterdir())


def _dataset(store: DataSourceStore, *, project: str, name: str) -> str:
    source_id, path = store.allocate(original_name=name, project=project)
    np.save(path, np.arange(32, dtype=float))
    store.profile_uploaded_file(source_id=source_id, path=path, project=project,
                               original_name=name, kind="auto")
    return source_id


@pytest.mark.asyncio
async def test_commander_snapshots_saved_project_data_without_rebinding_old_runs(tmp_path: Path) -> None:
    datasets = DataSourceStore(base=tmp_path / "datasets")
    first = _dataset(datasets, project="pimc", name="first.npy")
    datasets.set_default(project="pimc", source_id=first)
    store = RunStore(tmp_path / "runs")
    orch = Orchestrator(run_store=store, bus=InProcessEventBus())
    ctx = ToolContext(orch, CommanderSession(conv_id="data-selection", project="pimc"), store, datasets)
    args = {"entrypoint": "idea", "user_request": "Inspect selected data in a research proposal.",
            "repository_scope": "not_applicable", "routing_reason": "Proposal only, no code modification."}
    created = await execute_tool("run.create", {**args, "task": "first_selection"}, ctx)
    assert created["ok"] is True
    run = orch.session(created["run_id"]).run
    snapshot = (run.root / "input/selected_data_source.json").read_bytes()
    selected = json.loads(snapshot)
    assert selected["id"] == first
    assert Path(selected["stored_path"]).is_file()
    assert json.loads((run.root / "context/selected_data_source.json").read_text()) == selected
    assert first in (run.root / "context/selected_data_source.md").read_text()
    assert orch.session(created["run_id"]).request.data_source == selected

    second = _dataset(datasets, project="pimc", name="second.npy")
    datasets.set_default(project="pimc", source_id=second)
    next_run = await execute_tool("run.create", {**args, "task": "second_selection"}, ctx)
    assert next_run["ok"] is True
    assert orch.session(next_run["run_id"]).request.data_source == datasets.default_profile("pimc")
    assert (run.root / "input/selected_data_source.json").read_bytes() == snapshot


@pytest.mark.asyncio
async def test_commander_does_not_choose_unselected_upload_or_other_project_default(tmp_path: Path) -> None:
    datasets = DataSourceStore(base=tmp_path / "datasets")
    _dataset(datasets, project="pimc", name="uploaded_only.npy")
    other = _dataset(datasets, project="another_project", name="other.npy")
    datasets.set_default(project="another_project", source_id=other)
    store = RunStore(tmp_path / "runs")
    orch = Orchestrator(run_store=store, bus=InProcessEventBus())
    ctx = ToolContext(orch, CommanderSession(conv_id="no-selection", project="pimc"), store, datasets)
    rejected = await execute_tool("run.create", {"entrypoint": "experiment",
        "user_request": "Compare an uploaded dataset.", "repository_scope": "not_applicable",
        "routing_reason": "Check data admission before any stage model or code change."}, ctx)
    assert rejected["ok"] is False
    assert rejected["status"] == "data_selection_required"
    assert ctx.session.linked_run_id is None
    assert not list((tmp_path / "runs").iterdir())
    created = await execute_tool("run.create", {"entrypoint": "idea",
        "standalone": True,
        "user_request": "Draft a literature-only proposal.", "repository_scope": "not_applicable",
        "routing_reason": "Literature-only task, no execution."}, ctx)
    assert created["ok"] is True
    session = orch.session(created["run_id"])
    assert session.request.data_source is None
    assert not (session.run.root / "input/selected_data_source.json").exists()
