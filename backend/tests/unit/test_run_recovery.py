"""Recovery admission against real state files; no model or tool substitutes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

import pytest
import yaml

from app.agents.idea.agent import IdeaAgent
from app.agents.coding.agent import CodingAgent
from app.agents.execution.agent import ExecutionAgent
from app.agents.writing.agent import WritingAgent
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.bridge.run_recovery import recover_run, recovery_status
from app.bridge.task_runtime import bind_task
from app.harness.agent_loop.trace import LoopTrace
from app.harness.llm.accounting import RunModelBudget
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.storage.run_store import RunStore


def session_at(root: Path) -> tuple[Orchestrator, RunSession]:
    registry = AgentRegistry()
    registry.register('idea', IdeaAgent())
    orch = Orchestrator(run_store=RunStore(root), registry=registry, bus=InProcessEventBus())
    session = orch.create_session(RunRequest(task='recovery-admission', project='pimc',
                                          entrypoint='idea', standalone=True))
    session.graph.restore_state('idea', NodeState.FAILED)
    return orch, session


def checkpoint(session: RunSession, *, pending: str | None = None) -> Path:
    task = bind_task(session.run, 'idea', goal='admission only', upstream={}, output_schema='proposal.v1')
    root = session.run.root / 'agent_traces' / task.agent / task.invocation_id
    trace = LoopTrace(root, 'full')
    trace.emit('admission_fixture', {})
    # Caller-authored interrupted state; not a claim of successful agent execution.
    trace.snapshot({'status': 'interrupted', 'counts': {}, 'usage': {}, 'usage_complete': False,
                    'fingerprint': 'admission-only', 'pending': pending})
    return root / 'checkpoint.json'


def test_failed_stage_without_checkpoint_can_retry_in_same_run(tmp_path: Path) -> None:
    orch, session = session_at(tmp_path)
    before = sorted(tmp_path.iterdir())
    view = recovery_status(orch, session.run.run_id, project='pimc')
    assert view['actions'] == [{'action': 'retry', 'node': 'idea', 'label': '重试当前阶段'}]
    assert view['token'] and view['run_id'] == session.run.run_id
    assert sorted(tmp_path.iterdir()) == before
    with pytest.raises(ValueError, match='项目'):
        recovery_status(orch, session.run.run_id, project='different')


def test_checkpoint_resume_and_unknown_tool_refusal(tmp_path: Path) -> None:
    orch, session = session_at(tmp_path)
    path = checkpoint(session)
    view = recovery_status(orch, session.run.run_id, project='pimc')
    assert [action['action'] for action in view['actions']] == ['resume', 'retry']
    state = json.loads(path.read_text())
    state['pending'] = 'tool'
    path.write_text(json.dumps(state))
    blocked = recovery_status(orch, session.run.run_id, project='pimc')
    assert not blocked['actions'] and '工具' in blocked['message']
    assert path.read_text() == json.dumps(state)


@pytest.mark.asyncio
async def test_stale_token_cannot_start_or_create_work(tmp_path: Path) -> None:
    orch, session = session_at(tmp_path)
    view = recovery_status(orch, session.run.run_id, project='pimc')
    checkpoint(session)  # a changed contract invalidates the previously offered action
    result = await recover_run(orch, session.run.run_id, project='pimc', action='retry',
                               node='idea', token=view['token'])
    assert result['status'] == 'stale_recovery' and not result['ok']
    assert orch.owned_tasks.active(session.run.run_id) is None
    assert session.graph.state('idea') == NodeState.FAILED


@pytest.mark.asyncio
async def test_running_owner_deduplicates_recovery(tmp_path: Path) -> None:
    orch, session = session_at(tmp_path)
    release = asyncio.Event()
    async def wait_for_release() -> None:
        await release.wait()
    assert orch.owned_tasks.spawn(session.run.run_id, 'synchronization', wait_for_release, finished=lambda: None)
    result = await recover_run(orch, session.run.run_id, project='pimc', action='retry',
                               node='idea', token='obsolete')
    assert result['status'] == 'already_running'
    owner = orch.owned_tasks.active(session.run.run_id)
    assert owner is not None
    release.set()
    await owner


def test_historical_failed_attempt_does_not_offer_retry(tmp_path: Path) -> None:
    orch, session = session_at(tmp_path)
    session.graph.add_node('idea_attempt_2')
    session.graph.restore_state('idea_attempt_2', NodeState.DONE)
    view = recovery_status(orch, session.run.run_id, project='pimc')
    assert view['status'] == 'idle' and not view['actions']


def test_unowned_running_stage_cannot_be_restarted_without_checkpoint(tmp_path: Path) -> None:
    orch, session = session_at(tmp_path)
    session.graph.restore_state('idea', NodeState.RUNNING)
    view = recovery_status(orch, session.run.run_id, project='pimc')
    assert view['status'] == 'blocked' and not view['actions']


def test_unknown_model_reservation_is_not_replayed_or_refunded(tmp_path: Path) -> None:
    orch, session = session_at(tmp_path)
    budget = RunModelBudget(session.run.root)
    reservation = budget.reserve([Message(role='user', content='Admission arithmetic only.')],
                                 LLMConfig(provider='custom', model='no-provider-called', max_tokens=8), {})
    before = budget.path.read_bytes()
    view = recovery_status(orch, session.run.run_id, project='pimc')
    assert view['status'] == 'blocked' and '模型' in view['message']
    assert budget.path.read_bytes() == before
    budget.settle(reservation, usage=None, complete=False, outcome='cancelled')
    before = budget.path.read_bytes()
    assert recovery_status(orch, session.run.run_id, project='pimc')['status'] == 'recoverable'
    assert budget.path.read_bytes() == before


def test_token_usage_is_statistics_only_but_corrupt_ledger_blocks_recovery(tmp_path: Path) -> None:
    orch, session = session_at(tmp_path)
    budget = RunModelBudget(session.run.root)
    reservation = budget.reserve([Message(role='user', content='Accounting only.')],
                                 LLMConfig(provider='custom', model='no-provider-called', max_tokens=8), {})
    budget.settle(reservation, usage=None, complete=False, outcome='cancelled')
    state = json.loads(budget.path.read_text())
    row = state['requests'][reservation.request_id]
    row['charged_tokens'] = state['configuration']['limits']['max_total_tokens']
    # Legacy rows carry no component split; the entire charge remains conservative.
    for key in ('charged_input_tokens', 'charged_output_tokens', 'reserved_input_tokens', 'reserved_output_tokens'):
        row.pop(key, None)
    budget.path.write_text(json.dumps(state))
    before = budget.path.read_bytes()
    view = recovery_status(orch, session.run.run_id, project='pimc')
    assert view['status'] == 'recoverable' and view['actions']
    assert budget.path.read_bytes() == before
    budget.path.write_text('[]')
    assert recovery_status(orch, session.run.run_id, project='pimc')['status'] == 'blocked'


def test_expired_execution_window_offers_explicit_retry_only(tmp_path: Path) -> None:
    orch, session = session_at(tmp_path)
    checkpoint(session)
    budget = RunModelBudget(session.run.root)
    state = budget.recovery_snapshot()
    state['started_at'] = 0
    budget.path.parent.mkdir(parents=True, exist_ok=True)
    budget.path.write_text(json.dumps(state))
    before = budget.path.read_bytes()
    view = recovery_status(orch, session.run.run_id, project='pimc')
    assert [action['action'] for action in view['actions']] == ['retry']
    assert budget.path.read_bytes() == before


def test_commander_recovery_tools_are_configured() -> None:
    from app.bridge.commander_tools import TOOLS
    root = Path(__file__).resolve().parents[3]
    agents = yaml.safe_load((root / 'configs/agents.yaml').read_text())
    tools = yaml.safe_load((root / 'configs/tools.yaml').read_text())
    for name in ('run.recovery_status', 'run.recover'):
        assert name in TOOLS and name in agents['commander']['tools']
        assert tools['tools'][name]['enabled'] and tools['tools'][name]['bridge_only']


@pytest.mark.parametrize('stage', ['coding', 'execution', 'writing'])
def test_retry_reservation_and_completion_mode_use_same_budget_after_restart(
    tmp_path: Path, stage: Literal['coding', 'execution', 'writing'],
) -> None:
    registry = AgentRegistry()
    for agent_type in (CodingAgent, ExecutionAgent, WritingAgent):
        agent = agent_type()
        registry.register(agent.name, agent)
    store = RunStore(tmp_path)
    orch = Orchestrator(run_store=store, registry=registry, bus=InProcessEventBus())
    session = orch.create_session(RunRequest(task='shared-budget-admission', project='pimc',
                                          entrypoint=stage, standalone=True))
    session.graph.restore_state(stage, NodeState.FAILED)
    orch._persist_state(session, status='failed')
    budget = RunModelBudget(session.run.root, completion_driven=True)
    limit = budget.configuration['limits']['max_model_requests']
    assert type(limit) is int
    # Use real reservations/cancellations to leave one slot. No model is called.
    for _ in range(limit - 1):
        reservation = budget.reserve([Message('user', 'Accounting admission only')],
            LLMConfig(provider='custom', model='ledger-only', max_tokens=8, max_retries=0), {})
        budget.settle(reservation, usage=None, complete=False, outcome='cancelled')
    before = budget.path.read_bytes()
    for instance in (orch, Orchestrator(run_store=store, registry=registry, bus=InProcessEventBus())):
        view = recovery_status(instance, session.run.run_id, project='pimc')
        assert view['status'] == 'blocked' and not view['actions']
        assert f'已计入 {limit - 1} 次' in view['message'] and '预留 3 次' in view['message']
        assert instance.owned_tasks.active(session.run.run_id) is None
        assert budget.path.read_bytes() == before
