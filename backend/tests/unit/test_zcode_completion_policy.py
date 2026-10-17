"""Real files, Git, MCP and durable admission; no model or service substitutes."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.coding.agent import CodingAgent
from app.bridge.coding_approval import coding_candidate_errors
from app.bridge.research_branch import research_branch_scope
from app.harness.agent_loop.executor import LoopInput
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import LoopTrace
from app.harness.agent_loop.zcode.config import ZCodeConfig, runtime_environment
from app.harness.agent_loop.zcode.executor import ZCodeLoopExecutor
from app.harness.agent_loop.zcode.gateway import ZCodeGateway
from app.harness.agent_loop.zcode.limits import invocation_limits
from app.harness.agent_loop.zcode.protocol import ZCodeClient
from app.harness.llm.accounting import RunModelBudget
from app.harness.llm.openai_provider import LocalVllmProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.project_workspace import open_folder
from app.harness.tools.git_branch import git
from app.harness.tools.project_repo import load_project_repo
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import repo_root, reset_settings_cache
from app.storage.run_store import RunHandle


def test_completion_policy_uses_no_invocation_count_or_wall_time_limit() -> None:
    config = ZCodeConfig.load()
    original = config.__dict__.copy()
    limits = invocation_limits(config, AgentLoopPolicy(completion_driven=True, max_model_calls=1,
        max_tool_steps=1, max_validation_repairs=0, max_active_seconds=1))
    assert limits.model_calls is limits.tool_calls is limits.validation_repairs is limits.active_seconds is None
    assert limits.allows_model_call(500) and limits.allows_tool_call(500)
    assert limits.allows_validation_repairs(500)
    assert limits.remaining_active_seconds(900, 100) is None
    assert config.__dict__ == original  # User configuration and usage are not reset.


def test_bounded_policy_retains_explicit_config_and_agent_limits() -> None:
    config = replace(ZCodeConfig.load(), max_model_calls=24, max_tool_calls=40,
                     max_validation_repairs=2, active_timeout_seconds=900)
    limits = invocation_limits(config, AgentLoopPolicy(max_model_calls=36, max_tool_steps=50))
    assert limits.allows_model_call(23) and not limits.allows_model_call(24)
    assert limits.allows_tool_call(39) and not limits.allows_tool_call(40)
    assert limits.allows_validation_repairs(2) and not limits.allows_validation_repairs(3)
    assert limits.remaining_active_seconds(900) == 0
    assert limits.remaining_active_seconds(20, 30) == 850
    narrower = invocation_limits(config, AgentLoopPolicy(max_model_calls=3, max_tool_steps=4))
    assert not narrower.allows_model_call(3) and not narrower.allows_tool_call(4)
    assert invocation_limits(config, AgentLoopPolicy(max_model_calls=None)).model_calls == 24


def test_actual_exhausted_archive_can_use_completion_policy_without_erasing_usage() -> None:
    configured = os.environ.get('MARS_ZCODE_LIMIT_TRACE_ROOT')
    if not configured:
        pytest.skip('requires an explicitly selected genuine ZCode checkpoint')
    path = Path(configured) / 'checkpoint.json'
    before = path.read_bytes()
    state = json.loads(before)
    assert state['backend'] == 'zcode' and state['status'] == 'budget_exhausted'
    assert state['error'] == 'ZCode model-call limit reached'
    used = state['counts']['model_requests']
    assert type(used) is int and used > 0
    assert invocation_limits(ZCodeConfig.load(), AgentLoopPolicy(completion_driven=True)).allows_model_call(used)
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(before).digest()


@pytest.fixture
def research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[RunHandle, Path]]:
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    reset_settings_cache()
    project = open_folder(str(tmp_path / 'project'), create=True)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'main.py').write_text('VALUE = 1\n')
    (source / 'baseline').mkdir()
    (source / 'baseline/reference.py').write_text('REFERENCE = 1\n')
    git(source, 'init', '-b', 'baseline')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Actual baseline')
    (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({'repo_path': str(source),
        'read_only': True, 'allowed_paths': ['main.py', 'baseline/'], 'protected_paths': ['baseline/']}))
    root = tmp_path / 'run'
    root.mkdir()
    run = RunHandle('completion_boundary', root, project.name, 'Actual admission only', 'coding', '2026-10-14T00:00:00Z')
    try:
        yield run, source
    finally:
        reset_settings_cache()


def request_for(run: RunHandle, *, completion_driven: bool) -> LoopInput:
    agent = CodingAgent()
    submission = RunRequest(project=run.project, user_request='Verify this real code change',
        candidate_validator=lambda text: coding_candidate_errors(run, text))

    async def validate(text: str, history: list[dict[str, Any]]) -> list[str]:
        return await agent.validate_candidate(submission, text, history)

    return LoopInput(messages=[Message('user', 'Actual host boundary check; no model requested.')],
        provider=LocalVllmProvider(base_url='http://127.0.0.1:1'),
        config=LLMConfig('local_vllm', 'unavailable', max_tokens=16, max_retries=0),
        registry=get_registry(), tool_context=ToolContext(run.run_id, run.project, 'coding', extra={'run_root': str(run.root)}),
        tools=('code.repo_reader', 'code.write_file'),
        policy=AgentLoopPolicy(completion_driven=completion_driven, max_model_calls=1, max_tool_steps=1),
        trace_root=run.root / 'trace', validate=validate,
        final_schema=json.loads((repo_root() / 'backend/app/harness/schema/schemas/code_spec.v1.json').read_text()))


@pytest.mark.asyncio
@pytest.mark.parametrize('completion_driven', [False, True])
async def test_real_mcp_validation_repairs_and_writes_obey_selected_mode(
    research: tuple[RunHandle, Path], completion_driven: bool,
) -> None:
    run, source = research
    request = request_for(run, completion_driven=completion_driven)
    config = replace(ZCodeConfig.load(), max_tool_calls=1, max_validation_repairs=1)
    state = ZCodeLoopExecutor(config)._state(request, 'actual-boundary')
    gateway = ZCodeGateway(request, config, 'private-boundary-token', state, LoopTrace(request.trace_root, 'full'))
    with research_branch_scope(run, 'coding'):
        work = load_project_repo(run.project).root
        try:
            for _ in range(3 if completion_driven else 2):
                denied = await gateway.call_tool('mars_submit_document', {'metadata': {}, 'body': 'Human-authored invalid schema.'})
                assert denied['isError']
            if not completion_driven:
                assert state['status'] == 'validation_failed' and gateway.done.is_set()
                assert state['counts']['tool_dispatches'] == 0
                return
            assert state['status'] == 'running' and state['counts']['validation_repairs'] == 3
            for _ in range(3):
                read = await gateway.call_tool('mars_code__repo_reader', {'path': 'main.py'})
                assert not read['isError']
            written = await gateway.call_tool('mars_code__write_file', {'path': 'main.py', 'content': 'VALUE = 2\n'})
            assert not written['isError'] and (work / 'main.py').read_text() == 'VALUE = 2\n'
            metadata = {'schema': 'code_spec.v1', 'agent': 'coding', 'project': run.project, 'target_lang': 'python',
                'baseline_compat': {'preserved': True}, 'files_changed': [{'path': 'main.py', 'type': 'modified'}]}
            accepted = await gateway.call_tool('mars_submit_document', {'metadata': metadata,
                'body': 'Human-authored specification; actual governed file writes verified, no scientific result asserted.'})
            assert not accepted['isError'] and '"review_required":true' in accepted['content'][0]['text']
            assert state['status'] == 'passed' and state['counts']['tool_dispatches'] == 4
            assert state['counts']['model_requests'] == 0
            assert not (run.root / 'coding/code_spec.approved.md').exists()
            assert not (run.root / 'execution/jobs').exists()
            assert (source / 'main.py').read_text() == 'VALUE = 1\n'
        finally:
            await gateway.close()
            await request.provider.close()


@pytest.mark.asyncio
async def test_completion_mode_keeps_real_protected_file_gate(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    request = request_for(run, completion_driven=True)
    config = replace(ZCodeConfig.load(), max_tool_calls=1)
    state = ZCodeLoopExecutor(config)._state(request, 'protected-boundary')
    gateway = ZCodeGateway(request, config, 'private-token', state, LoopTrace(request.trace_root, 'full'))
    with research_branch_scope(run, 'coding'):
        work = load_project_repo(run.project).root
        try:
            for _ in range(3):
                assert not (await gateway.call_tool('mars_code__repo_reader', {'path': 'main.py'}))['isError']
            denied = await gateway.call_tool('mars_code__write_file', {'path': 'baseline/reference.py', 'content': 'REFERENCE = 9\n'})
            assert denied['isError'] and state['counts']['tool_dispatches'] == 4
            assert (work / 'baseline/reference.py').read_text() == (source / 'baseline/reference.py').read_text() == 'REFERENCE = 1\n'
            assert state['status'] == 'blocked'
        finally:
            await gateway.close()
            await request.provider.close()


@pytest.mark.asyncio
async def test_completion_mode_keeps_actual_cumulative_model_admission(tmp_path: Path) -> None:
    root = tmp_path / 'run'
    policy = RunModelBudget(root).configuration
    policy['limits']['max_model_requests'] = 1
    resource_dir = root / 'resources'
    resource_dir.mkdir(parents=True)
    (resource_dir / 'model_budget.policy.yaml').write_text(yaml.safe_dump(policy))
    budget = RunModelBudget(root)
    config = LLMConfig('local_vllm', 'unavailable', max_tokens=16, max_retries=0)
    reservation = budget.reserve([Message('user', 'Actual reservation, no provider request.')], config, {})
    budget.settle(reservation, usage=None, complete=False, outcome='cancelled')
    before = budget.path.read_bytes()

    async def validate(text: str, history: list[dict[str, Any]]) -> list[str]:
        from app.harness.schema.validator import validate_document
        return [str(error) for error in validate_document(text, expected_schema='code_spec.v1').errors]

    request = LoopInput(messages=[Message('user', 'Must be blocked before forwarding.')],
        provider=LocalVllmProvider(base_url='http://127.0.0.1:1'), config=config, registry=get_registry(),
        tool_context=ToolContext('quota', 'unbound', 'coding', extra={'run_root': str(root)}), tools=(),
        policy=AgentLoopPolicy(completion_driven=True), trace_root=root / 'trace', validate=validate)
    zconfig = ZCodeConfig.load()
    state = ZCodeLoopExecutor(zconfig)._state(request, 'quota-boundary')
    gateway = ZCodeGateway(request, zconfig, 'private-budget-token', state, LoopTrace(request.trace_root, 'full'))
    await gateway.start()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(gateway.endpoint + '/v1/chat/completions',
                headers={'Authorization': 'Bearer private-budget-token'},
                json={'messages': [{'role': 'user', 'content': 'Not admitted.'}]})
        assert response.status_code == 429 and response.json()['error']['request_sent'] is False
        assert response.json()['error']['type'] == 'mars_resource_budget'
        assert state['status'] == 'blocked' and state['counts']['model_responses'] == state['counts']['sdk_attempts'] == 0
        assert budget.path.read_bytes() == before
    finally:
        await gateway.close()
        await request.provider.close()


@pytest.mark.asyncio
async def test_completion_wait_can_be_cancelled_and_real_zcode_process_is_reaped(tmp_path: Path) -> None:
    config = ZCodeConfig.load()
    try:
        command = config.resolve_command()
    except ValueError:
        pytest.skip('requires the actual official ZCode runtime')
    limits = invocation_limits(config, AgentLoopPolicy(completion_driven=True))
    client = ZCodeClient(command=command, cwd=tmp_path, env=runtime_environment(tmp_path / 'runtime', provider={}),
        timeout=config.rpc_timeout_seconds, max_line_bytes=config.max_protocol_line_bytes)
    await client.start()  # Only real runtime capabilities; no model or training call.
    assert client.process is not None and client.process.returncode is None
    waiting = asyncio.create_task(asyncio.wait_for(asyncio.Event().wait(), limits.remaining_active_seconds(900)))
    try:
        await asyncio.sleep(0)
        assert not waiting.done()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    finally:
        await client.close()
    assert client.process.returncode is not None
    assert not client.pending
