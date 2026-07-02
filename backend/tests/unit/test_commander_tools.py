from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bridge.commander_session import CommanderSession
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore


@pytest.mark.asyncio
async def test_commander_tool_chain_writes_run_event(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    orch = Orchestrator(run_store=store, bus=InProcessEventBus())
    session = CommanderSession(conv_id="conv_test", project="pimc")
    ctx = ToolContext(orchestrator=orch, session=session, run_store=store)

    result = await execute_tool(
        "run.create",
        {"entrypoint": "pipeline", "user_request": "run a mock PIMC ablation"},
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
