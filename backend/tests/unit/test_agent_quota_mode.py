"""Quota-free admission and unchanged-error guards; no model execution doubles."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.harness.agent_loop.failure_guard import repeated_failure, clear_failure
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.zcode.config import ZCodeConfig
from app.harness.agent_loop.zcode.gateway import invocation_budget


def test_unlimited_loop_and_zcode_allowances_ignore_historical_counters() -> None:
    original = AgentLoopPolicy(max_model_calls=2, max_tool_steps=1, max_active_seconds=10)
    effective = original.effective('statistics_only')
    assert effective.allows_model_calls(1000000, 100)
    assert effective.allows_tool_calls(1000000, 100)
    assert effective.max_active_seconds == 0
    assert effective.input_token_budget == original.input_token_budget
    assert not original.effective('limited').allows_model_calls(2)
    assert original.fingerprint_data() != effective.fingerprint_data()
    config = replace(ZCodeConfig.load(), max_model_calls=2, max_tool_calls=1)
    assert invocation_budget(config, original, {'model_requests': 100, 'tool_dispatches': 100},
        quota_mode='statistics_only') == {'model_calls_remaining': None, 'tool_calls_remaining': None}


def test_failure_guard_stops_unchanged_errors_but_allows_actual_progress() -> None:
    state: dict[str, Any] = {}
    for index in range(100):
        assert not repeated_failure(state, 'validation', {'candidate_sha': str(index), 'error': 'missing evidence'}, limit=2)
    assert not repeated_failure(state, 'tool', {'error': 'file not found', 'path': 'missing.py'}, limit=2)
    assert not repeated_failure(state, 'tool', {'error': 'file not found', 'path': 'missing.py'}, limit=2)
    assert repeated_failure(state, 'tool', {'error': 'file not found', 'path': 'missing.py'}, limit=2)
    clear_failure(state, 'tool')
    assert not repeated_failure(state, 'tool', {'error': 'file not found', 'path': 'missing.py'}, limit=2)
