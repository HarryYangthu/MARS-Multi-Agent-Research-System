"""Pure failure contracts and immutable real journal replay; no service substitute."""
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.harness.agent_loop.completion_recovery import validate_author_empty_recovery_resume
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.provider_rejection_resume import checkpoint_quota_rejection_receipt, quota_rejection_receipt


def _failure_metadata() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    correlation = {key: key for key in ('invocation_id', 'node_id', 'task_id', 'parent_task_id', 'trace_id')}
    state: dict[str, Any] = {'status': 'model_error', 'pending': 'model', 'next_phase': 'reflect',
             'last_model_error': None, 'counts': {'model_requests': 1}, 'correlation': correlation}
    events: list[dict[str, Any]] = [
        {'kind': 'model_request', 'provider': 'zhipu', 'model': 'contract-input', 'phase': 'reflect',
         'request': 1, 'event_seq': 1, 'time': '2026-10-07T00:00:00+00:00', 'correlation': correlation},
        {'kind': 'sdk_attempt_started', 'attempt': 1, 'request': 1, 'event_seq': 2,
         'time': '2026-10-07T00:00:01+00:00', 'correlation': correlation},
        {'kind': 'sdk_attempt_failed', 'attempt': 1, 'request': 1, 'event_seq': 3,
         'time': '2026-10-07T00:00:02+00:00', 'correlation': correlation,
         'details': {'http_status': 429, 'api_error_code': '1113'}},
        {'kind': 'model_error', 'error_type': 'RateLimitError', 'reason': None, 'event_seq': 4,
         'time': '2026-10-07T00:00:03+00:00', 'correlation': correlation},
    ]
    ledger: dict[str, Any] = {'schema': 'runtime.model_budget.v1', 'requests': {'request1': {
        'status': 'failed', 'attempts_complete': True, 'observed_attempts': 1, 'charged_attempts': 1,
        'provider': 'zhipu', 'model': 'contract-input', 'correlation': correlation,
        'started_at': 1791331200.5, 'finished_at': 1791331202.5}}}
    return state, events, ledger


def test_matching_rejection_is_a_receipt_without_mutating_inputs() -> None:
    state, events, ledger = _failure_metadata()
    before = deepcopy((state, events, ledger))
    receipt = quota_rejection_receipt(state, events, ledger)
    assert receipt and receipt['reservation_id'] == 'request1'
    assert receipt['response_created'] is False and receipt['budgets_reset'] is False
    assert receipt['acceptance_inherited'] is False
    assert (state, events, ledger) == before


@pytest.mark.parametrize('change', ['no_request', 'different_code', 'different_provider', 'unsettled',
    'unknown_attempt', 'different_invocation', 'later_response', 'later_request', 'tool', 'batch',
    'review_plan', 'empty_response', 'pending_tool', 'wrong_count', 'wrong_attempt', 'wrong_model',
    'late_reservation', 'early_finish', 'later_ledger_request'])
def test_unproven_or_other_failures_cannot_unlock_resume(change: str) -> None:
    state, events, ledger = _failure_metadata()
    row = ledger['requests']['request1']
    if change == 'no_request': events = events[1:]
    elif change == 'different_code': events[2]['details']['api_error_code'] = '1302'
    elif change == 'different_provider': events[0]['provider'] = 'openai'
    elif change == 'unsettled': row['status'] = 'reconciliation_required'
    elif change == 'unknown_attempt': row['attempts_complete'] = False
    elif change == 'different_invocation': row['correlation'] = {'invocation_id': 'another'}
    elif change == 'later_response': events.append({'kind': 'model_response'})
    elif change == 'later_request': events.append({'kind': 'model_request'})
    elif change == 'tool': events.append({'kind': 'tool_dispatch'})
    elif change == 'batch': state['pending_batch'] = True
    elif change == 'review_plan': state['review_plan_contract_id'] = 'unit-review-contract'
    elif change == 'empty_response': state['last_model_error'] = {'code': 'empty_final_content'}
    elif change == 'pending_tool': state['pending'] = 'tool'
    elif change == 'wrong_count': state['counts']['model_requests'] = 2
    elif change == 'wrong_attempt': events[2]['attempt'] = 2
    elif change == 'wrong_model': row['model'] = 'another'
    elif change == 'late_reservation': row['started_at'] += 5
    elif change == 'early_finish': row['finished_at'] -= 5
    elif change == 'later_ledger_request':
        ledger['requests']['request2'] = {**deepcopy(row), 'started_at': row['started_at'] + 5}
    before = deepcopy((state, events, ledger))
    assert quota_rejection_receipt(state, events, ledger) is None
    assert (state, events, ledger) == before


def test_actual_quota_checkpoint_can_pass_resume_guard_without_calls_or_file_changes() -> None:
    configured = os.environ.get('MARS_TEST_QUOTA_TRACE')
    if not configured:
        pytest.skip('requires actual native quota receipts and settled ledger')
    trace = Path(configured).resolve()
    run = trace.parents[2]
    checkpoint_path = trace / 'checkpoint.json'
    ledger_path = run / 'resources/model_budget.v1.json'
    before_checkpoint, before_ledger = checkpoint_path.read_bytes(), ledger_path.read_bytes()
    state = json.loads(before_checkpoint)
    original = deepcopy(state)
    policy = AgentLoopPolicy(author_empty_completion_repair_enabled=True)
    with pytest.raises(ValueError, match='unknown'):
        validate_author_empty_recovery_resume(state, policy)
    receipt = checkpoint_quota_rejection_receipt(trace, state, run_root=run)
    assert receipt and receipt['request'] == state['counts']['model_requests']
    state['pending'] = None  # Only an in-memory preview of the normal explicit-resume transition.
    validate_author_empty_recovery_resume(state, policy)
    assert all(state[key] == original[key] for key in original if key != 'pending')
    assert state['usage_complete'] is False and state['reflection_accepted'] is False
    assert checkpoint_path.read_bytes() == before_checkpoint and ledger_path.read_bytes() == before_ledger
    from app.bridge.task_runtime import resumable_task
    from app.storage.run_store import RunStore
    handle = RunStore(run.parent).get(run.name)
    assert handle is not None
    task = resumable_task(handle, state['correlation']['node_id'])
    assert task.invocation_id == trace.name
