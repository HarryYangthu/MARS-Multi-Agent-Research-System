"""Real ledger operations and pure policy checks; no successful model substitutes."""
from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.harness.agent_loop.executor import LoopInput, NativeAgentLoop, budget_message
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.llm.accounting import (
    ResourceBudgetError, RunModelBudget, guarded_complete, run_resource_scope,
)
from app.harness.llm.openai_provider import CustomEndpointProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.schema.validator import validate_document
from app.harness.tools.registry import ToolContext, ToolRegistry


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


@pytest.mark.parametrize('completion_driven', [False, True])
def test_coding_and_execution_share_request_limit_without_reset(tmp_path: Path, completion_driven: bool) -> None:
    configuration = policy()
    configuration['limits'].update(max_elapsed_seconds=100)
    messages = [Message('user', 'Accounting only; no provider invoked.')]
    config = LLMConfig(provider='custom', model='ledger-only', max_tokens=32, max_retries=0)
    coding = RunModelBudget(tmp_path, configuration=configuration, completion_driven=completion_driven)
    reservation = coding.reserve(messages, config, {'agent': 'coding'})
    coding.settle(reservation, usage=None, complete=False, outcome='cancelled')
    before = coding.path.read_bytes()
    for mode in (False, True):
        with pytest.raises(ResourceBudgetError, match='model-request budget'):
            RunModelBudget(tmp_path, configuration=configuration, completion_driven=mode).reserve(
                messages, config, {'agent': 'execution'})
        assert coding.path.read_bytes() == before


@pytest.mark.parametrize('limit,error', [('max_total_tokens', 'total-token'),
    ('max_input_tokens', 'input-token'), ('max_billed_output_tokens', 'output-token'),
    ('max_cost', 'explicit prices'), ('max_elapsed_seconds', 'elapsed-time')])
def test_completion_mode_cannot_bypass_run_limit(tmp_path: Path, limit: str, error: str) -> None:
    configuration = policy()
    configuration['limits'].update(max_model_requests=10, max_total_tokens=100000,
        max_input_tokens=100000, max_billed_output_tokens=100000, max_elapsed_seconds=100)
    configuration['limits'][limit] = 1
    budget = RunModelBudget(tmp_path, configuration=configuration, completion_driven=True, token_mode='limited')
    state = budget.recovery_snapshot()
    state['started_at'] = 0 if limit == 'max_elapsed_seconds' else state['started_at']
    budget.path.parent.mkdir(parents=True, exist_ok=True)
    budget.path.write_text(json.dumps(state))
    before = budget.path.read_bytes()
    with pytest.raises(ResourceBudgetError, match=error):
        budget.reserve([Message('user', 'Quota arithmetic only')],
            LLMConfig(provider='custom', model='ledger-only', max_tokens=32, max_retries=0), {})
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


async def validate_run_log(text: str, observations: list[dict[str, Any]]) -> list[str]:
    return [error.message for error in validate_document(text, expected_schema='run_log.v1').errors]


@pytest.mark.asyncio
async def test_native_loop_reports_budget_block_without_sending_or_refunding(tmp_path: Path) -> None:
    budget = RunModelBudget(tmp_path)
    limit = budget.configuration['limits']['max_model_requests']
    assert type(limit) is int
    for _ in range(limit):
        reservation = budget.reserve([Message('user', 'Admission arithmetic only')],
            LLMConfig(provider='custom', model='ledger-only', max_tokens=8, max_retries=0), {})
        budget.settle(reservation, usage=None, complete=False, outcome='cancelled')
    before = budget.path.read_bytes()
    with socket.socket() as unavailable:
        unavailable.bind(('127.0.0.1', 0))
        provider = CustomEndpointProvider(api_key='local-failure-only',
            base_url=f'http://127.0.0.1:{unavailable.getsockname()[1]}/v1')
        request = LoopInput(messages=[Message('user', 'Budget should stop before the provider')],
            provider=provider, config=LLMConfig(provider='custom', model='not-called', max_tokens=8, max_retries=2),
            registry=ToolRegistry(), tools=(), tool_context=ToolContext(run_id='quota-check', project='pimc',
                agent='execution', extra={'run_root': str(tmp_path)}),
            policy=AgentLoopPolicy(completion_driven=True), trace_root=tmp_path / 'trace', validate=validate_run_log)
        try:
            result = await asyncio.wait_for(NativeAgentLoop().run(request), 5)
        finally:
            await provider.close()
    assert result.status == 'budget_exhausted' and 'model-request budget' in result.resource_error
    assert result.counts['sdk_attempts'] == result.counts['model_responses'] == result.counts['tool_dispatches'] == 0
    checkpoint = json.loads((tmp_path / 'trace/checkpoint.json').read_text())
    assert checkpoint['resource_error'] == result.resource_error and checkpoint['pending'] is None
    error = ResourceBudgetError(result.resource_error)
    assert error.reason == {'code': 'resource_budget_blocked', 'request_sent': False}
    assert budget.path.read_bytes() == before
