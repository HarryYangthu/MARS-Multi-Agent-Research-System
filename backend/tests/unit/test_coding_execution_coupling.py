"""Graph/receipt contract tests and real failed subprocess evidence; no model doubles."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest
import yaml

from app.bridge.coding_execution import append_repair, repaired_failure, verified_failures, scientific_inputs
from app.bridge.workflow_service import build_pipeline
from app.execution.batch_runner import BatchConfig, run_batch
from app.execution.simulation_runner import JobSpec
from app.harness.agent_loop.zcode.continuation import safe_trace
from app.harness.runtime.state_machine import NodeState
from app.harness.tools.execution.local_command import command_supports_project, LocalCommandJob, _registered_command
from app.harness.tools.execution.handoff import execution_delivery_context
from app.settings import repo_root, reset_settings_cache
from app.storage.run_store import RunHandle


def test_nonstreaming_zcode_request_drops_stream_only_options() -> None:
    from app.harness.agent_loop.zcode.gateway import completion_payload
    from app.harness.llm.provider_base import LLMConfig
    original = {'stream': True, 'stream_options': {'include_usage': True},
                'max_completion_tokens': 8192, 'messages': [{'role': 'user', 'content': 'test'}]}
    result = completion_payload(original, LLMConfig(provider='deepseek', model='selected', max_tokens=16384))
    assert result['stream'] is False and 'stream_options' not in result
    assert 'max_completion_tokens' not in result and result['max_tokens'] == 16384
    assert result['messages'] == original['messages'] and original['stream'] is True


def test_failed_execution_blocks_report_until_its_repair_really_completes() -> None:
    graph = build_pipeline('coding')
    graph.restore_state('coding', NodeState.DONE)
    graph.restore_state('execution', NodeState.FAILED)
    record = {'source_node': 'execution', 'target_node': 'coding_attempt_2', 'next_attempt': 2,
              'failure_signature': 'failure-one'}
    append_repair(graph, record)
    assert graph.ready_nodes() == ['coding_attempt_2']
    assert graph.state('execution') == NodeState.FAILED
    assert not repaired_failure(graph, 'execution')
    from app.harness.runtime.run_graph import RunGraph
    graph = RunGraph.from_dict(json.loads(json.dumps(graph.to_dict())))
    assert graph.ready_nodes() == ['coding_attempt_2']
    with pytest.raises(ValueError, match='already scheduled'):
        append_repair(graph, record)
    graph.restore_state('coding_attempt_2', NodeState.DONE)
    assert graph.ready_nodes() == ['execution_attempt_2']
    graph.restore_state('execution_attempt_2', NodeState.FAILED)
    assert not repaired_failure(graph, 'execution')
    append_repair(graph, {**record, 'source_node': 'execution_attempt_2',
        'target_node': 'coding_attempt_3', 'next_attempt': 3})
    graph.restore_state('coding_attempt_3', NodeState.DONE)
    graph.restore_state('execution_attempt_3', NodeState.DONE)
    assert graph.ready_nodes() == ['writing']
    assert repaired_failure(graph, 'execution') and repaired_failure(graph, 'execution_attempt_2')
    assert graph.state('execution') == NodeState.FAILED


@pytest.mark.parametrize('reference', ['../another/checkpoint', '/outside/trace',
                                     'agent_traces/writing/session', 'agent_traces/coding/one/nested'])
def test_continuation_cannot_read_another_stage_or_external_session(tmp_path: Path, reference: str) -> None:
    with pytest.raises(ValueError):
        safe_trace(tmp_path, reference)


@pytest.mark.parametrize('tamper_hash', [True, False])
def test_forged_or_incompatible_continuation_is_rejected(tmp_path: Path, tamper_hash: bool) -> None:
    from app.harness.agent_loop.zcode.continuation import continue_state
    trace = tmp_path / 'agent_traces/coding/untrusted'
    trace.mkdir(parents=True)
    # Authored hostile checkpoint; rejection must precede any runtime/model call.
    payload = json.dumps({'backend': 'zcode', 'status': 'passed', 'pending': None,
        'session_id': 'sess_not-a-real-session', 'continuation_compatibility': 'changed-permissions'}).encode()
    (trace / 'checkpoint.json').write_bytes(payload)
    link = {'trace_ref': 'agent_traces/coding/untrusted',
            'checkpoint_sha256': '0' * 64 if tamper_hash else hashlib.sha256(payload).hexdigest()}
    with pytest.raises(ValueError, match='source changed|compatible session'):
        continue_state(tmp_path, link, 'original-permissions')


def test_pending_repair_recovery_from_real_acceptance_archive(tmp_path: Path) -> None:
    from dataclasses import replace
    from app.bridge.coding_execution import restore_pending_repairs
    from app.storage.run_store import RunStore
    configured = os.environ.get('MARS_REAL_COUPLED_RUN_ROOT')
    if not configured:
        pytest.skip('requires a genuine completed coupling acceptance archive')
    source = Path(configured)
    run = RunStore(source.parent).get(source.name)
    assert run is not None
    # Reconstruct only the journal crash boundary from actual saved evidence.
    # No model, tool or job success is fabricated or replayed by this test.
    copied = tmp_path / 'recovery'
    shutil.copytree(source, copied, ignore=shutil.ignore_patterns('zcode_runtime'))
    run = replace(run, root=copied)
    candidate = json.loads((copied / 'coding_execution/candidates/coding.json').read_text(encoding='utf-8'))
    (copied / 'coding/code_spec.approved.md').write_bytes((copied / candidate['artifact_ref']).read_bytes())
    graph = build_pipeline('coding')
    graph.restore_state('coding', NodeState.DONE)
    graph.restore_state('execution', NodeState.FAILED)
    assert restore_pending_repairs(run, graph)
    assert graph.ready_nodes() == ['coding_attempt_2']
    assert graph.state('execution') == NodeState.FAILED
    assert not restore_pending_repairs(run, graph)


def test_project_discovery_and_admission_share_exact_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands = [{'id': 'old', 'project_ids': ['old-project']}, {'id': 'new', 'project_ids': ['new-project']},
                {'id': 'shared'}, {'id': 'empty', 'project_ids': []}, {'id': 'bad', 'project_ids': 'new-project'}]
    path = tmp_path / 'execution.yaml'
    path.write_text(yaml.safe_dump({'execution': {'local_commands': commands}}))
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(path))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    reset_settings_cache()
    try:
        exposed = execution_delivery_context(project='new-project')['local_commands']
        assert [row['id'] for row in exposed] == ['new', 'shared']
        for row in commands:
            job = LocalCommandJob('run', 'experiment', 'new-project', tmp_path, command_id=row['id'])
            if command_supports_project(row, 'new-project'):
                assert _registered_command(job) == row
            else:
                with pytest.raises(ValueError, match='does not support this project'):
                    _registered_command(job)
    finally:
        reset_settings_cache()


def test_approval_and_selected_data_changes_change_scientific_identity(tmp_path: Path) -> None:
    run = RunHandle('run', tmp_path, 'project', 'task', 'coding', '2026-10-22T00:00:00Z')
    first = scientific_inputs(run)
    target = tmp_path / 'input/selected_data_source.json'
    target.parent.mkdir()
    target.write_text('{"id":"data-a"}')
    second = scientific_inputs(run)
    target.write_text('{"id":"data-b"}')
    assert first != second != scientific_inputs(run)


@pytest.mark.asyncio
async def test_only_real_terminal_failure_receipt_is_eligible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / 'fail_training.py'
    script.write_text("raise RuntimeError('deliberate real process failure for recovery validation')\n")
    argv = [sys.executable, str(script)]
    tools = yaml.safe_load((repo_root() / 'configs/tools.yaml').read_text(encoding='utf-8'))
    tools['tools']['execution.simulation_runner']['command_allowlist'] = [argv]
    tools['tools']['execution.batch_runner']['command_allowlist'] = [argv]
    tool_path = tmp_path / 'tools.yaml'
    tool_path.write_text(yaml.safe_dump(tools))
    config = tmp_path / 'execution.yaml'
    config.write_text(yaml.safe_dump({'execution': {'backend': 'local_command', 'command_timeout_seconds': 10,
        'local_commands': [{'id': 'failure', 'argv': argv, 'required_metrics': ['mse']}]}}))
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(config))
    monkeypatch.setenv('MARS_TOOLS_CONFIG_PATH', str(tool_path))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'local_command')
    reset_settings_cache()
    root = tmp_path / 'run'
    root.mkdir()
    run = RunHandle('run', root, 'project', 'actual failure', 'execution', '2026-10-22T00:00:00Z')
    try:
        spec = JobSpec(run_id='run', experiment_id='baseline', project='project', run_root=root,
                       config={'command_id': 'failure', 'attempt': 1}, seed=42)
        outcome = await run_batch([spec], config=BatchConfig(max_concurrency=1, steps=1))
        assert outcome.failures
        failures = verified_failures(run, 1)
        assert len(failures) == 1 and failures[0]['returncode'] != 0
        receipt = root / failures[0]['receipt_ref']
        assert hashlib.sha256(receipt.read_bytes()).hexdigest() == failures[0]['receipt_sha256']
        original = receipt.read_bytes()
        receipt.write_bytes(original + b' ')
        with pytest.raises(ValueError, match='唯一真实退出回执'):
            verified_failures(run, 1)
        receipt.write_bytes(original)
        journal = next((root / 'execution/jobs').glob('*.json'))
        raw = json.loads(journal.read_text())
        raw['status'] = 'running'  # Contract rejection input, never a claimed process outcome.
        journal.write_text(json.dumps(raw))
        with pytest.raises(ValueError, match='结果不明'):
            verified_failures(run, 1)
    finally:
        reset_settings_cache()
