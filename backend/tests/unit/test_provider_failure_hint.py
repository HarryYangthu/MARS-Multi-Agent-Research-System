"""Pure classification plus optional real archived provider failure evidence."""
from pathlib import Path
import os

import pytest

from app.harness.llm.failure_hint import checkpoint_failure_hint, provider_failure_hint


def test_timeout_hint_requires_a_terminal_timeout_and_keeps_outcome_uncertainty() -> None:
    events = [{'kind': 'model_request', 'provider': 'zhipu'},
              {'kind': 'sdk_attempt_failed', 'details': {'exception_type': 'APITimeoutError'}},
              {'kind': 'sdk_attempt_failed', 'details': {'exception_type': 'APITimeoutError'}}]
    hint = provider_failure_hint(events, status='model_error')
    assert 'GLM 请求超时' in hint and '2 次' in hint and '核对原请求和工具状态' in hint
    assert provider_failure_hint(events, status='passed') == ''
    assert provider_failure_hint(events + [{'kind': 'sdk_attempt_succeeded'}], status='model_error') == ''
    assert provider_failure_hint(events + [{'kind': 'model_request'}], status='model_error') == ''


def test_protocol_hint_explains_rejected_work_without_claiming_acceptance() -> None:
    events = [{'kind': 'protocol_error', 'error': 'revision path must be a valid pointer'}]
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
    events = [{'kind': 'model_request', 'provider': 'zhipu'}, {'kind': 'sdk_attempt_failed',
              'details': {'http_status': 429, 'api_error_code': '1113'}}]
    assert '1113' in provider_failure_hint(events, status='model_error')
    assert provider_failure_hint(events, status='passed') == ''
    assert provider_failure_hint(events + [{'kind': 'sdk_attempt_succeeded'}], status='model_error') == ''
    assert provider_failure_hint(events + [{'kind': 'model_request', 'provider': 'zhipu'}], status='model_error') == ''
    assert provider_failure_hint([{'kind': 'model_request', 'provider': 'openai'}, events[1]], status='model_error') == ''


def test_unknown_or_missing_receipts_do_not_invent_quota_failure(tmp_path: Path) -> None:
    assert checkpoint_failure_hint(tmp_path, status='model_error') == ''
    assert provider_failure_hint([], status='model_error') == ''
    events = [{'kind': 'model_request', 'provider': 'zhipu'}, {'kind': 'sdk_attempt_failed',
              'details': {'http_status': 429, 'api_error_code': '1302'}}]
    assert provider_failure_hint(events, status='model_error') == ''


def test_actual_archived_quota_error_is_explained_without_a_new_api_call() -> None:
    configured = os.environ.get('MARS_TEST_QUOTA_TRACE')
    if not configured:
        pytest.skip('requires actual native provider failure receipts')
    assert '1113' in checkpoint_failure_hint(Path(configured), status='model_error')
