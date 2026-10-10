"""The Commander exposes connected read tools without preloading implementation."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bridge.commander import _system_prompt
from app.bridge.commander_code import repository_summary
from app.bridge.commander_session import CommanderSession
from app.bridge.commander_tools import TOOLS, ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.tools.config import tool_config
from app.storage.run_store import RunStore


def test_prompt_exposes_task_directed_reads_but_not_code_contents() -> None:
    session = CommanderSession(conv_id="inspection-prompt", project="pimc")
    prompt = _system_prompt(session)
    assert "code.repo_list" in prompt and "code.repo_search" in prompt and "code.repo_read_lines" in prompt
    assert "通过 code.repo_list" in prompt and "非代码问题无需读取仓库" in prompt
    assert "class StaticPIMC" not in prompt
    assert "def forward(" not in repository_summary("pimc")
    for name in ("code.repo_list", "code.repo_search", "code.repo_read_lines"):
        assert name in TOOLS
        assert tool_config(name).mutation_level == "read"


@pytest.mark.asyncio
async def test_commander_cannot_override_the_selected_project(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    session = CommanderSession(conv_id="inspection-denial", project="pimc")
    ctx = ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()), session, store)
    for name, args in (("code.repo_list", {"project_repo_root": "/"}),
                       ("code.repo_read_lines", {"path": "libs/model.py", "project": "other"}),
                       ("code.repo_search", {"query": "key", "path": "../"})):
        result = await execute_tool(name, args, ctx)
        assert not result["ok"], name
    assert session.linked_run_id is None
