"""Pure review transitions and real checkpoint files, without a provider double."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from app.harness.agent_loop.executor import budget_message
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import parse_action, parse_review
from app.harness.agent_loop.review_plan import (
    ReviewPlan, ReviewUnit, UnitReviewResult, _plan_structure_errors,
    finish_review_unit, isolated_unit_input_errors, pack_review_unit, plan_payload,
    prepare_review_plan, queue_review_format_correction, start_review_unit,
)
from app.harness.agent_loop.trace import digest
from app.harness.llm.provider_base import Message


def _prepared() -> tuple[dict[str, Any], ReviewPlan, list[Message]]:
    candidate = 'Human-authored test document, not an Agent output or a scientific result.'
    unit = ReviewUnit('field:0', (Message('user', 'Pure contract fixture: inspect original field.'),),
        {'type': 'object'}, lambda text: UnitReviewResult(parse_review(text), {}))
    plan = ReviewPlan('pure.format_correction.v1', digest(candidate), (unit,))
    state: dict[str, Any] = {'candidate': candidate, 'status': 'running', 'pending': None,
        'counts': {'model_requests': 0, 'reflections': 0, 'tool_dispatches': 0,
                   'protocol_repairs': 0, 'validation_repairs': 0}, 'reflection_accepted': False}
    assert prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    messages, _ = pack_review_unit(unit, budget=24000, budget_context=budget_message(AgentLoopPolicy(), state['counts']))
    state['counts']['model_requests'] = 1
    start_review_unit(state, messages)
    return state, plan, messages


def _queue(state: dict[str, Any]) -> dict[str, Any]:
    original = '{"accept":false,"issues":["Original unresolved'
    record = queue_review_format_correction(state, error='JSON string is truncated',
        response_text=original, response_visible=original)
    assert record is not None
    return record


def test_tool_commentary_is_optional_but_actual_arguments_stay_exact() -> None:
    text = '调用如下：```JSON\r\n{"tool":"registered.reader","args":{"steps":7,"seed":2031},}\r\n```'
    assert parse_action(text) == {'tool': 'registered.reader', 'args': {'steps': 7, 'seed': 2031}, 'reason': ''}
    for text in ('{"tool":"registered.reader"}', '{"tool":"registered.reader","args":[]}'):
        with pytest.raises(ValueError):
            parse_action(text)
    with pytest.raises(ValueError, match='duplicate'):
        parse_action('{"tool":"a","tool":"b","args":{}}')


def test_one_format_failure_remains_separate_from_corrected_unit_and_checkpoint(tmp_path: Path) -> None:
    state, plan, first_messages = _prepared()
    candidate = state['candidate']
    original = _queue(state)
    assert state['counts']['model_requests'] == 1 and state['counts']['tool_dispatches'] == 0
    assert state['candidate'] == candidate and state['reflection_accepted'] is False
    assert state['review_plan']['results'] == [] and state['review_plan']['next_unit'] == 0
    assert _plan_structure_errors(state['review_plan']) == []
    checkpoint = tmp_path / 'checkpoint.json'
    checkpoint.write_text(json.dumps(state))
    original_file = checkpoint.read_bytes()
    loaded = json.loads(original_file)
    assert prepare_review_plan(loaded, plan, contract_id=plan.contract_id, max_model_calls=3)
    messages, _ = pack_review_unit(plan.units[0], budget=24000,
        budget_context=budget_message(AgentLoopPolicy(), loaded['counts']), format_correction=original)
    unit = plan_payload(plan)['units'][0]
    assert [m.to_wire() for m in messages[:len(plan.units[0].messages)]] == [m.to_wire() for m in first_messages[:-2]]
    assert isolated_unit_input_errors(unit, [m.to_wire() for m in messages], original) == []
    assert isolated_unit_input_errors(unit, [m.to_wire() for m in messages])
    loaded['counts']['model_requests'] += 1
    second = start_review_unit(loaded, messages)
    assert second['request'] == 2 and second['format_source_request'] == 1
    assert second['format_failure_sha256'] == original['format_failure_sha256']
    assert queue_review_format_correction(loaded, error='second format error', response_text='bad', response_visible='bad') is None
    # A pure authored rejection exercises the transition; no provider response
    # or measurement is substituted, and formatting never supplies acceptance.
    text = '{"accept":false,"issues":["Original unresolved issue"],"rationale":"Still unresolved"}'
    result = UnitReviewResult(parse_review(text), {})
    saved = finish_review_unit(loaded, result, response_text=text, response_visible=text)
    assert saved['request'] == 2 and loaded['review_plan']['format_failures'][0] == original
    assert loaded['candidate'] == candidate and loaded['reflection_accepted'] is False
    assert loaded['counts']['model_requests'] == 2 and loaded['counts']['tool_dispatches'] == 0
    assert loaded['review_plan']['status'] == 'rejected' and _plan_structure_errors(loaded['review_plan']) == []
    assert checkpoint.read_bytes() == original_file


def test_unknown_request_or_changed_failure_cannot_be_replayed() -> None:
    state, plan, _ = _prepared()
    state['pending'] = 'model'
    assert queue_review_format_correction(state, error='unknown', response_text='x', response_visible='x') is None
    state['pending'] = None
    original = _queue(state)
    changed = deepcopy(state)
    changed['planned_review_format_correction']['original_output'] = 'Different previous output'
    messages, _ = pack_review_unit(plan.units[0], budget=24000,
        budget_context=budget_message(AgentLoopPolicy(), changed['counts']), format_correction=original)
    changed['counts']['model_requests'] = 2
    with pytest.raises(ValueError, match='different unit or candidate'):
        start_review_unit(changed, messages)
    changed = deepcopy(state)
    changed['review_plan']['format_failures'][0]['original_output'] = 'Changed stored source'
    assert _plan_structure_errors(changed['review_plan'])


def test_format_correction_does_not_create_more_model_budget() -> None:
    state, plan, _ = _prepared()
    _queue(state)
    assert not prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=2)
    assert state['status'] == 'budget_exhausted' and state['counts']['model_requests'] == 1
    assert state['reflection_accepted'] is False
