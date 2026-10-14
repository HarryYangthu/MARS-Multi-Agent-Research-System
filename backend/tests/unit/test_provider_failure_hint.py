"""Pure classification plus optional real archived provider failure evidence."""
from pathlib import Path
from typing import Any
import json
import os

import pytest

from app.harness.llm.failure_hint import checkpoint_failure_hint, provider_failure_hint


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


def test_connection_after_timeout_explains_the_terminal_cause() -> None:
    events: list[dict[str, Any]] = [{'kind': 'model_request', 'provider': 'zhipu'},
        {'kind': 'sdk_attempt_failed', 'details': {'exception_type': 'APITimeoutError'}},
        {'kind': 'sdk_attempt_failed', 'details': {'exception_type': 'APIConnectionError'}}]
    hint = provider_failure_hint(events, status='model_error')
    assert '先超时，重试后连接失败' in hint and '2 次' in hint
    assert '不表示仿真代码失败' in hint
    assert provider_failure_hint(events + [{'kind': 'sdk_attempt_succeeded'}], status='model_error') == ''
    assert provider_failure_hint(events + [{'kind': 'model_request'}], status='model_error') == ''


def test_connection_only_does_not_invent_timeout_or_provider() -> None:
    events: list[dict[str, Any]] = [{'kind': 'model_request'},
        {'kind': 'sdk_attempt_failed', 'details': {'exception_type': 'APIConnectionError'}}]
    hint = provider_failure_hint(events, status='model_error')
    assert '模型服务 请求连接失败' in hint and '先超时' not in hint and 'GLM' not in hint


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


def test_model_limit_hint_reads_only_verified_terminal_counts(tmp_path: Path) -> None:
    checkpoint = tmp_path / 'checkpoint.json'
    state = {'status': 'budget_exhausted', 'error': 'ZCode model-call limit reached',
             'counts': {'model_requests': 24}, 'history': 'private prompt'}
    checkpoint.write_text(json.dumps(state), encoding='utf-8')
    before = checkpoint.read_bytes()
    hint = checkpoint_failure_hint(tmp_path, status='budget_exhausted')
    assert '24 次' in hint and '交付内容尚未通过验收' in hint
    assert '此状态不代表仿真已运行' in hint and 'private prompt' not in hint
    assert checkpoint.read_bytes() == before
    assert checkpoint_failure_hint(tmp_path, status='passed') == ''
    state['error'] = 'private unknown reason'
    checkpoint.write_text(json.dumps(state), encoding='utf-8')
    assert checkpoint_failure_hint(tmp_path, status='budget_exhausted') == ''


@pytest.mark.parametrize('counts', [None, {}, {'model_requests': True}, {'model_requests': -1}])
def test_model_limit_hint_never_invents_missing_or_invalid_counts(tmp_path: Path, counts: Any) -> None:
    (tmp_path / 'checkpoint.json').write_text(json.dumps({
        'status': 'budget_exhausted', 'error': 'ZCode model-call limit reached', 'counts': counts}))
    assert checkpoint_failure_hint(tmp_path, status='budget_exhausted') == ''
