"""Pure classification plus optional real archived provider failure evidence."""
from pathlib import Path
from typing import Any
import json
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


def test_deepseek_balance_hint_requires_terminal_matching_provider_rejection() -> None:
    # Classification inputs only; no provider execution is substituted.
    request = {'kind': 'model_request', 'provider': 'deepseek'}
    failure = {'kind': 'sdk_attempt_failed', 'details': {'http_status': 402},
               'error': 'PRIVATE RESPONSE'}
    events: list[dict[str, Any]] = [request, failure]
    hint = provider_failure_hint(events, status='model_error')
    assert 'DeepSeek' in hint and '余额不足' in hint and 'HTTP 402' in hint
    assert 'PRIVATE' not in hint
    assert provider_failure_hint(events, status='passed') == ''
    assert provider_failure_hint(events + [{'kind': 'sdk_attempt_succeeded'}], status='model_error') == ''
    assert provider_failure_hint(events + [request], status='model_error') == ''
    assert provider_failure_hint([{'kind': 'model_request', 'provider': 'openai'}, failure], status='model_error') == ''


def test_actual_archived_quota_error_is_explained_without_a_new_api_call() -> None:
    configured = os.environ.get('MARS_TEST_QUOTA_TRACE')
    if not configured:
        pytest.skip('requires actual native provider failure receipts')
    root = Path(configured)
    before = (root / 'events.jsonl').read_bytes(), (root / 'checkpoint.json').read_bytes()
    hint = checkpoint_failure_hint(root, status='model_error')
    assert 'GLM' in hint and '1113' in hint and '不会自动重复调用' in hint
    assert ((root / 'events.jsonl').read_bytes(), (root / 'checkpoint.json').read_bytes()) == before


def _zcode_quota_events() -> list[dict[str, Any]]:
    return [{'kind': 'model_request', 'backend': 'zcode', 'model': 'glm-5.3'},
            {'kind': 'provider_request', 'request': 20, 'provider': 'zhipu'},
            {'kind': 'sdk_attempt_started', 'request': 20, 'attempt': 1},
            {'kind': 'sdk_attempt_failed', 'request': 20, 'attempt': 1,
             'details': {'http_status': 429, 'api_error_code': '1113',
                         'exception_type': 'RateLimitError'}}]


def test_zcode_provider_receipt_binds_the_terminal_request_without_prompt_content() -> None:
    events = _zcode_quota_events()
    events[0]['visible'] = {'provider': 'openai', 'content': 'private prompt, credential, URL'}
    events[1]['visible'] = {'content': 'private prompt, credential, URL'}
    hint = provider_failure_hint(events, status='model_error')
    assert 'GLM' in hint and '1113' in hint and '不会自动重复调用' in hint
    assert 'private' not in hint and 'credential' not in hint and 'URL' not in hint


@pytest.mark.parametrize('identity', [None, True, '20', 19, -1])
def test_zcode_quota_hint_rejects_unmatched_or_invalid_provider_request(identity: Any) -> None:
    events = _zcode_quota_events()
    events[1]['request'] = identity
    assert provider_failure_hint(events, status='model_error') == ''


@pytest.mark.parametrize('identity', [None, True, '20', 19, -1])
def test_zcode_quota_hint_rejects_unmatched_or_invalid_terminal_request(identity: Any) -> None:
    events = _zcode_quota_events()
    events[-1]['request'] = identity
    assert provider_failure_hint(events, status='model_error') == ''


@pytest.mark.parametrize('provider', [None, '', 'openai', 'unknown'])
def test_zcode_quota_hint_never_infers_provider_from_model_name(provider: Any) -> None:
    events = _zcode_quota_events()
    events[1]['provider'] = provider
    assert provider_failure_hint(events, status='model_error') == ''


def test_zcode_quota_hint_rejects_conflicting_matching_provider_receipts() -> None:
    events = _zcode_quota_events()
    events.insert(2, {'kind': 'provider_request', 'request': 20, 'provider': 'openai'})
    assert provider_failure_hint(events, status='model_error') == ''
    events.pop(2)
    events[0]['provider'] = 'openai'
    assert provider_failure_hint(events, status='model_error') == ''


def test_zcode_quota_hint_accepts_consistent_repeated_provider_receipts() -> None:
    events = _zcode_quota_events()
    events.insert(2, dict(events[1]))
    assert '1113' in provider_failure_hint(events, status='model_error')


def test_zcode_quota_hint_rejects_stale_and_late_provider_receipts() -> None:
    events = _zcode_quota_events()
    provider = events.pop(1)
    assert provider_failure_hint([provider] + events, status='model_error') == ''
    assert provider_failure_hint(events + [provider], status='model_error') == ''


def test_zcode_quota_hint_rejects_another_invocation_with_the_same_request_number() -> None:
    events = _zcode_quota_events()
    events[0]['correlation'] = {'invocation_id': 'current'}
    events[1]['correlation'] = {'invocation_id': 'other'}
    events[-1]['correlation'] = {'invocation_id': 'current'}
    assert provider_failure_hint(events, status='model_error') == ''
    events[1]['correlation'] = {'invocation_id': 'current'}
    assert '1113' in provider_failure_hint(events, status='model_error')


def test_zcode_quota_hint_rejects_conflicting_outer_request_identity() -> None:
    events = _zcode_quota_events()
    events[0]['request'] = 19
    assert provider_failure_hint(events, status='model_error') == ''
    events[0]['request'] = 20
    assert '1113' in provider_failure_hint(events, status='model_error')


def test_zcode_quota_hint_requires_terminal_failure_and_no_newer_model_request() -> None:
    events = _zcode_quota_events()
    assert provider_failure_hint(events, status='passed') == ''
    assert provider_failure_hint(events + [{'kind': 'sdk_attempt_succeeded', 'request': 20}],
                                 status='model_error') == ''
    assert provider_failure_hint(events + [{'kind': 'model_request'}], status='model_error') == ''


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
