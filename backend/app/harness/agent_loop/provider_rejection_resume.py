"""Reconcile an explicitly rejected request on human-requested resume only."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import audit_trace, digest
from app.harness.llm.accounting import ResourceBudgetError, RunModelBudget


def quota_rejection_receipt(state: dict[str, Any], events: list[dict[str, Any]],
                            ledger: dict[str, Any]) -> dict[str, Any] | None:
    """Pure receipt matching; never create a response, acceptance or fresh budget."""
    if (state.get('status') != 'model_error' or state.get('pending') != 'model'
            or state.get('pending_batch') or state.get('last_model_error') is not None
            or state.get('review_plan_contract_id') or state.get('review_plan')):
        return None
    try:
        index = max(i for i, event in enumerate(events) if event.get('kind') == 'model_request')
        request, tail = events[index], events[index + 1:]
        correlation = state['correlation']
        if (any(not isinstance(correlation.get(key), str) or not correlation[key]
                for key in ('invocation_id', 'node_id', 'task_id', 'parent_task_id', 'trace_id'))
                or request.get('correlation') != correlation
                or request.get('request') != state['counts']['model_requests']
                or request.get('phase') != state.get('next_phase')
                or request.get('provider') != 'zhipu'
                or any(event.get('kind') in {'model_response', 'tool_dispatch', 'observation', 'reflection'}
                       for event in tail)):
            return None
        attempts = [event for event in tail if event.get('kind', '').startswith('sdk_attempt_')]
        if not attempts or len(attempts) % 2:
            return None
        for number in range(1, len(attempts) // 2 + 1):
            started, failed = attempts[(number - 1) * 2:number * 2]
            if (started.get('kind') != 'sdk_attempt_started' or failed.get('kind') != 'sdk_attempt_failed'
                    or any(event.get('attempt') != number or event.get('request') != request['request']
                           or event.get('correlation') != correlation for event in (started, failed))
                    or failed.get('details', {}).get('http_status') != 429
                    or str(failed.get('details', {}).get('api_error_code')) != '1113'):
                return None
        errors = [event for event in tail if event.get('kind') == 'model_error']
        if (len(errors) != 1 or errors[0].get('reason') is not None
                or errors[0].get('correlation') != correlation
                or errors[0].get('error_type') != 'RateLimitError'):
            return None
        if ledger.get('schema') != 'runtime.model_budget.v1':
            return None
        rows = ledger['requests']
        if any(row.get('status') in {'in_flight', 'reconciliation_required'} for row in rows.values()):
            return None
        request_time = datetime.fromisoformat(request['time']).timestamp()
        start_time = datetime.fromisoformat(attempts[0]['time']).timestamp()
        failure_time = datetime.fromisoformat(attempts[-1]['time']).timestamp()
        error_time = datetime.fromisoformat(errors[0]['time']).timestamp()
        matched = [(identifier, row) for identifier, row in rows.items()
                   if row.get('correlation') == correlation
                   and request_time <= row.get('started_at', -1) <= start_time]
        if len(matched) != 1:
            return None
        identifier, row = matched[0]
        count = len(attempts) // 2
        if (row.get('status') != 'failed' or row.get('attempts_complete') is not True
                or row.get('observed_attempts') != count or row.get('charged_attempts', -1) < count
                or row.get('provider') != request['provider'] or row.get('model') != request['model']
                or not start_time <= failure_time <= row.get('finished_at', -1) <= error_time
                or any(other.get('correlation') == correlation
                       and other.get('started_at', -1) > row['started_at'] for other in rows.values())):
            return None
        return {'code': '1113', 'request': request['request'], 'request_event_seq': request['event_seq'],
                'failure_event_seq': attempts[-1]['event_seq'], 'reservation_id': identifier,
                'reservation_sha256': digest(row), 'charged_attempts': row['charged_attempts'],
                'budgets_reset': False, 'response_created': False, 'acceptance_inherited': False}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def checkpoint_quota_rejection_receipt(root: Path, state: dict[str, Any], *,
                                       run_root: Path) -> dict[str, Any] | None:
    """Verify the actual journal, matching checkpoint and settled resource ledger."""
    if state.get('status') != 'model_error' or state.get('pending') != 'model':
        return None
    try:
        if (not root.resolve().is_relative_to(run_root.resolve())
                or state.get('correlation', {}).get('invocation_id') != root.name
                or state.get('correlation', {}).get('trace_id') != run_root.name):
            return None
        audit = audit_trace(root)
        if not audit.get('consistent') or any(audit['facts'].get(key) != state.get(key)
                for key in ('status', 'counts', 'usage', 'usage_complete', 'fingerprint', 'pending', 'correlation')):
            return None
        events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
        ledger = RunModelBudget(run_root).recovery_snapshot()
        return quota_rejection_receipt(state, events, ledger)
    except (OSError, ValueError, KeyError, TypeError, ResourceBudgetError):
        return None
