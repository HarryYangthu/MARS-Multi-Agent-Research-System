"""Real source reads, graph persistence and accounting; no Agent execution substitute."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import pytest

from app.bridge.commander_code import routing_conclusion_needs_source
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext as CommanderContext, execute_tool
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.llm.accounting import RunModelBudget
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.harness.tools.code_inspection import read_code_fragment, repo_search_tool
from app.harness.tools.project_repo import ProjectRepo
from app.harness.tools.registry import ToolContext
from app.storage.run_store import RunStore


async def source_receipt(root: Path) -> dict[str, Any]:
    (root / 'libs').mkdir(exist_ok=True)
    (root / 'libs/model.py').write_text('class ExistingModel:\n    taps = 17\n')
    result = await read_code_fragment({'path': 'libs/model.py'},
        ToolContext('source-check', 'pimc', 'commander', project_repo_root=str(root)))
    assert result.ok
    return {'ok': result.ok, 'evidence': [{key: result.output[key]
            for key in ('path', 'sha256', 'line_start', 'line_end')}]}


@pytest.mark.asyncio
async def test_current_empty_search_and_run_status_cannot_finish_route_without_reading(tmp_path: Path) -> None:
    routing = await source_receipt(tmp_path)
    ctx = ToolContext('source-check', 'pimc', 'commander', project_repo_root=str(tmp_path))
    search = await repo_search_tool({'query': 'num_layers'}, ctx)
    assert search.ok and not search.output['matches']
    orch = Orchestrator(run_store=RunStore(tmp_path / 'runs'), bus=InProcessEventBus())
    run = orch.create_session(RunRequest(task='authored-routing-state', project='pimc'))
    session = CommanderSession(conv_id='source-check', project='pimc', linked_run_id=run.run.run_id, active_turn_id='one')
    session.add(ChatMessage(role='tool', content='Actual search', tool_name='code.repo_search',
        tool_result={'ok': search.ok, 'output': search.output}))
    assert not routing_conclusion_needs_source(session)
    status = await execute_tool('get_run_status', {}, CommanderContext(orch, session, orch.run_store))
    session.add(ChatMessage(role='tool', content='Actual status', tool_name='get_run_status', tool_result=status))
    assert routing_conclusion_needs_source(session)
    assert status['entrypoint'] == 'pipeline' and status['task_request'] == ''
    read = await read_code_fragment({'path': 'libs/model.py'}, ctx)
    session.add(ChatMessage(role='tool', content='Actual source', tool_name='code.repo_read_lines',
        tool_result={'ok': read.ok, 'output': read.output}))
    repo = ProjectRepo('pimc', tmp_path, 'local_path', True, ('',), (), ())
    assert not routing_conclusion_needs_source(session, repo=repo)
    assert routing['evidence'][0]['path'] == 'libs/model.py'


@pytest.mark.asyncio
async def test_correct_scope_retains_id_budget_history_and_reloads_new_graph(tmp_path: Path) -> None:
    routing = await source_receipt(tmp_path)
    store = RunStore(tmp_path / 'runs')
    orch = Orchestrator(run_store=store, bus=InProcessEventBus())
    s = orch.create_session(RunRequest(task='early-route', project='pimc', user_request='Design a new model'))
    s.graph.restore_state('idea', NodeState.FAILED)  # Caller-authored admission state, not an executed result.
    orch._persist_state(s, status='failed')
    budget = RunModelBudget(s.run.root)
    reservation = budget.reserve([Message(role='user', content='Accounting test only; no provider called')],
                                LLMConfig(provider='custom', model='accounting-only', max_tokens=8), {})
    budget.settle(reservation, usage=None, complete=False, outcome='cancelled')
    before_budget = budget.path.read_bytes()
    progress = s.run.root / 'idea/progress.jsonl'
    progress.write_text('{"kind":"caller-authored-admission-note"}\n')
    goal = 'Run the existing model; no literature or new architecture requested.'
    result = orch.correct_initial_scope(s.run.run_id, project='pimc', entrypoint='coding',
        user_request=goal, reason='User rejected the original research route', routing=routing)
    assert result['ok'] and result['run_id'] == s.run.run_id
    assert s.graph.state('idea') == s.graph.state('experiment') == NodeState.SKIPPED
    assert s.graph.state('coding') == NodeState.PENDING
    assert budget.path.read_bytes() == before_budget
    assert progress.read_text() == '{"kind":"caller-authored-admission-note"}\n'
    record = next((s.run.root / 'input/scope_corrections').glob('*.json'))
    assert json.loads(record.read_text())['previous_request']['user_request'] == 'Design a new model'
    restored = Orchestrator(run_store=store, bus=InProcessEventBus()).session(s.run.run_id)
    assert restored.request.entrypoint == restored.run.entrypoint == 'pipeline'
    assert restored.request.extra['scope_entrypoint'] == 'coding' and restored.graph.entrypoints == ['coding']
    assert restored.request.user_request == goal and restored.graph.state('idea') == NodeState.SKIPPED


@pytest.mark.asyncio
async def test_approved_or_started_downstream_work_cannot_be_rewritten(tmp_path: Path) -> None:
    routing = await source_receipt(tmp_path)
    orch = Orchestrator(run_store=RunStore(tmp_path / 'runs'), bus=InProcessEventBus())
    s = orch.create_session(RunRequest(task='protect-approved-work', project='pimc'))
    approved = s.run.root / 'idea/idea_proposal.approved.md'
    approved.write_text('Human-authored approval marker for admission test, not an Agent result')
    before = (s.run.root / 'run_state.json').read_bytes()
    result = orch.correct_initial_scope(s.run.run_id, project='pimc', entrypoint='coding',
        user_request='Run existing model', reason='Scope correction', routing=routing)
    assert not result['ok'] and (s.run.root / 'run_state.json').read_bytes() == before
    approved.unlink()
    s.graph.restore_state('coding', NodeState.FAILED)
    assert not orch.correct_initial_scope(s.run.run_id, project='pimc', entrypoint='coding',
        user_request='Run existing model', reason='Scope correction', routing=routing)['ok']


@pytest.mark.asyncio
async def test_pending_model_accounting_cannot_be_bypassed_by_scope_change(tmp_path: Path) -> None:
    routing = await source_receipt(tmp_path)
    orch = Orchestrator(run_store=RunStore(tmp_path / 'runs'), bus=InProcessEventBus())
    s = orch.create_session(RunRequest(task='pending-model-admission', project='pimc'))
    budget = RunModelBudget(s.run.root)
    budget.reserve([Message(role='user', content='Admission accounting only')],
                   LLMConfig(provider='custom', model='not-called', max_tokens=8), {})
    before = budget.path.read_bytes()
    assert not orch.correct_initial_scope(s.run.run_id, project='pimc', entrypoint='coding',
        user_request='Existing model', reason='Correction', routing=routing)['ok']
    assert budget.path.read_bytes() == before


@pytest.mark.asyncio
async def test_unknown_tool_checkpoint_is_not_discarded(tmp_path: Path) -> None:
    from app.bridge.task_runtime import bind_task
    from app.harness.agent_loop.trace import LoopTrace
    routing = await source_receipt(tmp_path)
    orch = Orchestrator(run_store=RunStore(tmp_path / 'runs'), bus=InProcessEventBus())
    s = orch.create_session(RunRequest(task='pending-tool-admission', project='pimc'))
    task = bind_task(s.run, 'idea', goal='Admission fixture only', upstream={}, output_schema='proposal.v1')
    root = s.run.root / 'agent_traces' / task.agent / task.invocation_id
    trace = LoopTrace(root, 'full')
    trace.snapshot({'status': 'interrupted', 'pending': 'tool', 'counts': {}, 'usage': {},
                    'usage_complete': False, 'fingerprint': 'authored-admission-state'})
    before = (root / 'checkpoint.json').read_bytes()
    result = orch.correct_initial_scope(s.run.run_id, project='pimc', entrypoint='coding',
        user_request='Existing model', reason='Correction', routing=routing)
    assert not result['ok'] and '工具' in result['error']
    assert (root / 'checkpoint.json').read_bytes() == before
