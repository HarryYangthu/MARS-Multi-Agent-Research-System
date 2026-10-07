"""Pure classification plus optional real archived provider failure evidence."""
from pathlib import Path
import os

import pytest

from app.harness.llm.failure_hint import checkpoint_failure_hint, provider_failure_hint


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
