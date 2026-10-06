"""Real ledger operations and pure policy checks; no successful model substitutes."""
from __future__ import annotations

import asyncio
import json
import math
import socket
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.harness.agent_loop.executor import budget_message
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.llm.accounting import (
    ModelConcurrencyBusy, ResourceBudgetError, RunModelBudget, guarded_complete, run_resource_scope,
)
from app.harness.llm.openai_provider import CustomEndpointProvider
from app.harness.llm.provider_base import LLMConfig, Message


@pytest.mark.asyncio
async def test_read_only_baseline_blocks_before_any_model_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents.base import RunRequest
    from app.agents.coding.agent import CodingAgent
    from app.harness.project_workspace import open_folder
    from app.settings import reset_settings_cache
    # Environment points to real isolated files; no provider or tool is replaced.
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    reset_settings_cache()
    try:
        project = open_folder(str(tmp_path / 'project'), create=True)
        source = tmp_path / 'baseline'; source.mkdir()
        code = source / 'main.py'; code.write_text('value = 1\n')
        binding = project.metadata_root / 'repo_link.yaml'
        binding.write_text(yaml.safe_dump({'repo_path': str(source), 'read_only': True}))
        agent = CodingAgent()
        assert agent.loop_policy.completion_driven
        request = RunRequest(project=project.name, user_request='Edit code', extra={'run_root': str(tmp_path / 'run')})
        context = await agent.build_context(request)
        with pytest.raises(ValueError, match='编码实验分支未就绪'):
            await agent.run_loop(request, context)
        assert not (tmp_path / 'run/resources/model_budget.v1.json').exists()
        assert code.read_text() == 'value = 1\n'
        binding.write_text(yaml.safe_dump({'repo_path': str(source), 'read_only': False}))
        assert agent.execution_blocker(project.name) == ''  # preflight only, no model invocation
    finally:
        reset_settings_cache()


def policy() -> dict[str, Any]:
    return {'schema': 'runtime.resources.v1', 'currency': 'CNY', 'prices': {}, 'limits': {
        'max_model_requests': 1, 'max_total_tokens': 1, 'max_input_tokens': 1,
        'max_billed_output_tokens': 1, 'max_parallel_model_calls': 1,
        'max_elapsed_seconds': 1, 'max_cost': None,
    }}


def test_completion_mode_removes_local_quotas_without_resetting_counts() -> None:
    p = AgentLoopPolicy(completion_driven=True, max_model_calls=1, max_tool_steps=1, max_validation_repairs=0)
    counts = {'model_requests': 500, 'tool_dispatches': 500, 'validation_repairs': 500}
    assert p.allows_model_calls(500, 3) and p.allows_tool_calls(500, 20)
    assert p.remaining_model_calls(500) is None and p.remaining_tool_calls(500) is None
    text = budget_message(p, counts).content
    assert all(f'"{name}":null' in text for name in ['model_calls', 'tool_calls', 'validation_repairs'])
    assert counts == {'model_requests': 500, 'tool_dispatches': 500, 'validation_repairs': 500}
    assert p.fingerprint_data()['completion_driven'] is True
    assert 'completion_driven' not in AgentLoopPolicy().fingerprint_data()
    with pytest.raises(ValueError):
        AgentLoopPolicy.from_mapping({'completion_driven': 'true'})


def test_existing_exhausted_ledger_is_retained_and_concurrency_still_enforced(tmp_path: Path) -> None:
    budget = RunModelBudget(tmp_path, configuration=policy(), completion_driven=True)
    original = budget.recovery_snapshot(); original['started_at'] = 0
    budget.path.parent.mkdir(parents=True, exist_ok=True)
    budget.path.write_text(json.dumps(original))
    config = LLMConfig(provider='custom', model='ledger-only', max_tokens=32, max_retries=2)
    messages = [Message(role='user', content='Caller-authored accounting input, no model is invoked.')]
    for _ in range(65):
        reservation = budget.reserve(messages, config, {})
        with pytest.raises(ModelConcurrencyBusy):
            budget.reserve(messages, config, {})
        budget.settle(reservation, usage=None, complete=False, outcome='cancelled')
    state = budget.recovery_snapshot()
    assert state['started_at'] == 0 and len(state['requests']) == 65
    assert sum(row['charged_attempts'] for row in state['requests'].values()) == 195
    assert all(row['execution_mode'] == 'completion_driven' for row in state['requests'].values())
    assert all(row['charged_tokens'] == row['reserved_tokens'] > 1 for row in state['requests'].values())
    assert math.isinf(budget.remaining_seconds())
    before = budget.path.read_bytes()
    with pytest.raises(ResourceBudgetError):
        RunModelBudget(tmp_path, configuration=policy(), completion_driven=False).reserve(messages, config, {})
    assert budget.path.read_bytes() == before


def test_completion_scope_does_not_leak_to_other_agents(tmp_path: Path) -> None:
    with run_resource_scope(tmp_path):
        assert not RunModelBudget(tmp_path, configuration=policy()).completion_driven
        with run_resource_scope(tmp_path, completion_driven=True):
            assert RunModelBudget(tmp_path, configuration=policy()).completion_driven
            with run_resource_scope(tmp_path):
                assert not RunModelBudget(tmp_path, configuration=policy()).completion_driven
        assert not RunModelBudget(tmp_path, configuration=policy()).completion_driven


def test_all_roles_count_tokens_without_enforcing_aggregate_token_quota(tmp_path: Path) -> None:
    config = policy()
    config['limits'].update(max_model_requests=10, max_elapsed_seconds=100)
    budget = RunModelBudget(tmp_path, configuration=config, completion_driven=False)
    assert budget.token_mode == 'statistics_only'
    for agent in ('idea', 'experiment', 'coding', 'execution', 'writing', 'commander'):
        reservation = budget.reserve([Message('user', 'Real accounting, no model called')],
            LLMConfig(provider='custom', model='ledger-only', max_tokens=32, max_retries=0), {'agent': agent})
        budget.settle(reservation, usage=None, complete=False, outcome='cancelled')
    state = budget.recovery_snapshot()
    assert len(state['requests']) == 6
    assert sum(row['charged_tokens'] for row in state['requests'].values()) > 1
    assert all(row['token_usage_mode'] == 'statistics_only' for row in state['requests'].values())
    before = budget.path.read_bytes()
    with pytest.raises(ResourceBudgetError, match='token'):
        RunModelBudget(tmp_path, configuration=config, token_mode='limited').reserve(
            [Message('user', 'legacy bounded policy')], LLMConfig(provider='custom', model='ledger-only', max_tokens=8, max_retries=0), {})
    assert budget.path.read_bytes() == before


@pytest.mark.asyncio
async def test_real_connection_failure_keeps_receipt_in_completion_scope(tmp_path: Path) -> None:
    # An actual non-listening socket refuses connections; no server or provider substitute.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        provider = CustomEndpointProvider(api_key='not-a-real-key', base_url=f'http://127.0.0.1:{port}/v1')
        config = LLMConfig(provider='custom', model='connection-refusal', max_tokens=8,
                           max_retries=0, request_timeout_seconds=0.2)
        try:
            with run_resource_scope(tmp_path, completion_driven=True):
                with pytest.raises(Exception):
                    await asyncio.wait_for(guarded_complete(provider, [Message(role='user', content='Connection refusal check')], config), 5)
        finally:
            await provider.close()
    rows = list(RunModelBudget(tmp_path).recovery_snapshot()['requests'].values())
    assert len(rows) == 1 and rows[0]['execution_mode'] == 'completion_driven'
    assert rows[0]['status'] == 'failed' and rows[0]['charged_tokens'] > 0
