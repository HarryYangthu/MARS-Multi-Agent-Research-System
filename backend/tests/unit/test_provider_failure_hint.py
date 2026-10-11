"""Pure classification plus optional real archived provider failure evidence."""
from pathlib import Path
from typing import Any
import os
import json

import pytest

from app.harness.llm.failure_hint import checkpoint_failure_hint, provider_failure_hint


@pytest.mark.parametrize('phase,label', [('act', '方案生成'), ('reflect', '独立评审')])
def test_truncation_hint_uses_terminal_metadata_without_exposing_content(phase: str, label: str) -> None:
    # Rendering inputs only, not a simulated provider or execution.
    reason = {'code': 'output_truncated', 'finish_reason': 'length', 'empty_final': True}
    events: list[dict[str, Any]] = [
        {'kind': 'model_request', 'phase': phase, 'provider': 'deepseek', 'max_tokens': 8192,
         'visible': 'PRIVATE PROMPT'},
        {'kind': 'sdk_attempt_succeeded'},
        {'kind': 'model_response', 'rejected': True, 'reason': reason},
        {'kind': 'model_error', 'reason': reason, 'message': 'PRIVATE ERROR'},
        {'kind': 'finished', 'status': 'model_error'},
    ]
    hint = provider_failure_hint(events, status='model_error')
    assert label in hint and 'DeepSeek' in hint and '8192 token' in hint and '被截断' in hint
    assert 'PRIVATE' not in hint
    assert provider_failure_hint(events, status='passed') == ''
    for following in [{'kind': 'completion_recovery'}, {'kind': 'model_response'},
                      {'kind': 'model_request'}, {'kind': 'sdk_attempt_started'}]:
        assert provider_failure_hint(events + [following], status='model_error') == ''


def test_truncation_hint_requires_a_matching_rejected_response() -> None:
    reason = {'code': 'output_truncated', 'finish_reason': 'length'}
    request = {'kind': 'model_request', 'phase': 'reflect', 'provider': 'deepseek'}
    error = {'kind': 'model_error', 'reason': reason}
    assert provider_failure_hint([request, error], status='model_error') == ''
    assert provider_failure_hint([request, {'kind': 'model_response', 'rejected': False,
                                           'reason': reason}, error], status='model_error') == ''


def test_actual_truncation_archive_explains_review_without_modifying_evidence() -> None:
    configured = os.environ.get('MARS_TEST_TRUNCATION_TRACE')
    if not configured:
        pytest.skip('requires a real completed truncation trace')
    import hashlib
    root = Path(configured)
    path = root / 'events.jsonl'
    before = hashlib.sha256(path.read_bytes()).digest()
    hint = checkpoint_failure_hint(root, status='model_error')
    assert '独立评审' in hint and '8192 token' in hint and '被截断' in hint
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_timeout_hint_requires_a_terminal_timeout_and_keeps_outcome_uncertainty() -> None:
    events: list[dict[str, Any]] = [{'kind': 'model_request', 'provider': 'zhipu'},
              {'kind': 'sdk_attempt_failed', 'details': {'exception_type': 'APITimeoutError'}},
              {'kind': 'sdk_attempt_failed', 'details': {'exception_type': 'APITimeoutError'}}]
    hint = provider_failure_hint(events, status='model_error')
    assert 'GLM 请求超时' in hint and '2 次' in hint and '核对原请求和工具状态' in hint
    assert provider_failure_hint(events, status='passed') == ''
    assert provider_failure_hint(events + [{'kind': 'sdk_attempt_succeeded'}], status='model_error') == ''
    assert provider_failure_hint(events + [{'kind': 'model_request'}], status='model_error') == ''


def test_protocol_hint_explains_rejected_work_without_claiming_acceptance() -> None:
    events: list[dict[str, Any]] = [{'kind': 'protocol_error', 'error': 'revision path must be a valid pointer'}]
    hint = provider_failure_hint(events, status='protocol_exhausted')
    assert '字段路径' in hint and '仍需通过内容校验与独立评审' in hint
    assert provider_failure_hint(events, status='passed') == ''


def test_actual_timeout_is_explained_without_another_model_request() -> None:
    configured = os.environ.get('MARS_TEST_PROTOCOL_RECOVERY_RUN')
    if not configured:
        pytest.skip('requires actual timeout receipts')
    checkpoints = list((Path(configured) / 'agent_traces/idea').glob('*/checkpoint.json'))
    import json
    path = next(p for p in checkpoints if json.loads(p.read_text()).get('status') == 'model_error')
    before = path.read_bytes(), (path.parent / 'events.jsonl').read_bytes()
    assert 'GLM 请求超时' in checkpoint_failure_hint(path.parent, status='model_error')
    assert (path.read_bytes(), (path.parent / 'events.jsonl').read_bytes()) == before


def test_quota_hint_requires_matching_provider_status_and_terminal_rejection() -> None:
    events: list[dict[str, Any]] = [{'kind': 'model_request', 'provider': 'zhipu'}, {'kind': 'sdk_attempt_failed',
              'details': {'http_status': 429, 'api_error_code': '1113'}}]
    assert '1113' in provider_failure_hint(events, status='model_error')
    assert provider_failure_hint(events, status='passed') == ''
    assert provider_failure_hint(events + [{'kind': 'sdk_attempt_succeeded'}], status='model_error') == ''
    assert provider_failure_hint(events + [{'kind': 'model_request', 'provider': 'zhipu'}], status='model_error') == ''
    assert provider_failure_hint([{'kind': 'model_request', 'provider': 'openai'}, events[1]], status='model_error') == ''


def test_unknown_or_missing_receipts_do_not_invent_quota_failure(tmp_path: Path) -> None:
    assert checkpoint_failure_hint(tmp_path, status='model_error') == ''
    assert provider_failure_hint([], status='model_error') == ''
    events: list[dict[str, Any]] = [{'kind': 'model_request', 'provider': 'zhipu'}, {'kind': 'sdk_attempt_failed',
              'details': {'http_status': 429, 'api_error_code': '1302'}}]
    assert provider_failure_hint(events, status='model_error') == ''


def test_actual_archived_quota_error_is_explained_without_a_new_api_call() -> None:
    configured = os.environ.get('MARS_TEST_QUOTA_TRACE')
    if not configured:
        pytest.skip('requires actual native provider failure receipts')
    assert '1113' in checkpoint_failure_hint(Path(configured), status='model_error')


def test_budget_hint_requires_a_matching_zcode_terminal_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / 'checkpoint.json'
    state = {'backend': 'zcode', 'status': 'budget_exhausted',
             'error': 'ZCode tool-call limit reached', 'counts': {'tool_dispatches': 40}}
    path.write_text(json.dumps(state), encoding='utf-8')
    hint = checkpoint_failure_hint(tmp_path, status='budget_exhausted')
    assert '工具调用次数上限' in hint and '40 次' in hint and '尚未完成验收' in hint
    for field, value in [('backend', 'native'), ('status', 'passed'), ('error', 'PRIVATE UNKNOWN ERROR')]:
        path.write_text(json.dumps({**state, field: value}), encoding='utf-8')
        assert checkpoint_failure_hint(tmp_path, status='budget_exhausted') == ''


def test_actual_zcode_budget_failure_explains_preserved_incomplete_work() -> None:
    configured = os.environ.get('MARS_TEST_ZCODE_BUDGET_TRACE')
    if not configured:
        pytest.skip('requires actual ZCode tool budget exhaustion receipts')
    root = Path(configured)
    paths = [root / 'checkpoint.json', root / 'events.jsonl']
    before = [path.read_bytes() for path in paths]
    hint = checkpoint_failure_hint(root, status='budget_exhausted')
    assert '工具调用次数上限' in hint and '40 次' in hint and '人工审核' in hint
    assert [path.read_bytes() for path in paths] == before
