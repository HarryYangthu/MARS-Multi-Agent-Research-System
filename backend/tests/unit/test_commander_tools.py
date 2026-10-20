from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bridge.commander_session import CommanderSession
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.project_scope import ProjectScope, bind_project_scope
from app.storage.run_store import RunStore


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
    bound = (tmp_path / 'bound-project').resolve()
    metadata, snapshot, source = (bound / name for name in ('metadata', 'snapshot', 'source'))
    for directory in (metadata, snapshot, source):
        directory.mkdir(parents=True)
    (metadata / 'project.yaml').write_text('display_name: Source admission test\n')
    (source / 'model.py').write_text('class ExistingModel:\n    pass\n')
    scope = ProjectScope(run_id=bound.name, project='connected-test', task_sha256='0' * 64,
        run_root=bound, metadata_root=metadata, snapshot_root=snapshot, candidate_root=source,
        snapshot_id='authored-admission-scope', readable_files=('model.py',),
        allowed_write_paths=(), protected_paths=())
    ctx = ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()),
                      CommanderSession(conv_id='unchecked', project='connected-test'), store)
    with bind_project_scope(scope):
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
