"""The Commander exposes connected read tools without preloading implementation."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bridge.commander import _system_prompt
from app.bridge.commander_code import repository_summary, routing_source_evidence
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import TOOLS, ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.tools.config import tool_config
from app.storage.run_store import RunStore


def test_prompt_exposes_task_directed_reads_but_not_code_contents() -> None:
    session = CommanderSession(conv_id="inspection-prompt", project="pimc")
    prompt = _system_prompt(session)
    assert "code.repo_list" in prompt and "code.repo_search" in prompt and "code.repo_read_lines" in prompt
    assert "未知先浏览或搜索" in prompt and "纯问答无需代码核查" in prompt
    assert "class StaticPIMC" not in prompt
    assert '快捷工具未覆盖时' in prompt and '再从Coding创建' in prompt
    assert '不先调研、设计实验或翻阅全仓' in prompt
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


@pytest.mark.asyncio
async def test_routing_source_must_be_read_this_turn_and_still_match_disk(tmp_path: Path) -> None:
    from app.harness.tools.code_inspection import read_code_fragment
    from app.harness.tools.registry import ToolContext as ReadContext
    from app.harness.tools.project_repo import ProjectRepo
    (tmp_path / 'libs').mkdir()
    source = tmp_path / 'libs/model.py'
    source.write_text('class ExistingModel:\n    taps = 17\n')
    repo = ProjectRepo('pimc', tmp_path, 'local_path', True, ('',), (), ())
    session = CommanderSession(conv_id='source-proof', project='pimc', active_turn_id='one')
    result = await read_code_fragment({'path': 'libs/model.py'},
        ReadContext('source-proof', 'pimc', 'commander', project_repo_root=str(tmp_path)))
    assert result.ok
    # Retain the actual reader receipt, not a fabricated tool observation.
    session.add(ChatMessage(role='tool', content='Source inspected', tool_name='code.repo_read_lines',
                tool_result={'ok': result.ok, 'output': result.output}))
    evidence = routing_source_evidence(repo, session)
    assert len(evidence) == 1 and evidence[0]['path'] == 'libs/model.py'
    source.write_text('class ExistingModel:\n    taps = 22\n')
    assert routing_source_evidence(repo, session) == []
    source.write_text('class ExistingModel:\n    taps = 17\n')
    session.active_turn_id = 'two'
    assert routing_source_evidence(repo, session) == []
