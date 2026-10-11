"""Pure request adaptation and optional real DeepSeek rejection evidence."""
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.harness.agent_loop.trace import LoopTrace, audit_trace, digest
from app.harness.agent_loop.zcode.gateway import (
    record_rejected_completion, upstream_payload, validate_wire_completion,
)
from app.harness.llm.provider_base import LLMCompletionError, LLMConfig
from app.harness.llm.request_evidence import BODY_FIELDS


@pytest.mark.parametrize('stream', [True, False, None])
def test_buffered_request_removes_stream_options_and_preserves_history(stream: bool | None) -> None:
    # Serialization input only; no model, tool or service response is supplied.
    original = {'stream': stream, 'stream_options': {'include_usage': True},
        'model': 'sdk-choice', 'max_completion_tokens': 9999,
        'messages': [{'role': 'user', 'content': 'Read actual evidence'}],
        'tools': [{'type': 'function', 'function': {'name': 'read_evidence'}}], 'tool_choice': 'auto'}
    before = deepcopy(original)
    config = LLMConfig('deepseek', 'selected-model', max_tokens=8192,
                       thinking_enabled=True, reasoning_effort='low')
    result = upstream_payload(original, config)
    assert original == before
    assert result['stream'] is False and 'stream_options' not in result
    assert result['model'] == config.model and result['max_tokens'] == config.max_tokens
    assert 'max_completion_tokens' not in result
    for key in ('messages', 'tools', 'tool_choice'):
        assert result[key] == before[key]
    assert result['thinking'] == {'type': 'enabled'} and result['reasoning_effort'] == 'low'
    assert 'stream_options' in BODY_FIELDS


def test_actual_failed_request_adapts_to_the_independently_accepted_wire_body() -> None:
    configured = os.environ.get('MARS_TEST_ZCODE_STREAM_TRACE')
    diagnostic = os.environ.get('MARS_TEST_ZCODE_STREAM_DIAGNOSTIC')
    if not configured or not diagnostic:
        pytest.skip('requires actual failed ZCode request and independent DeepSeek diagnostic receipts')
    root, evidence = Path(configured), Path(diagnostic)
    events = [json.loads(line) for line in (root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    recorded = next(e['visible'] for e in events if e['kind'] == 'provider_request')
    assert any(e['kind'] == 'sdk_attempt_failed' and e['details']['http_status'] == 400 for e in events)
    archive = next((root / 'zcode_runtime/storage/cli/rollout').glob('model-io-*.jsonl'))
    raw = json.loads(archive.read_text(encoding='utf-8').splitlines()[0])['request']['body']
    if isinstance(raw, str):
        raw = json.loads(raw)
    before = deepcopy(raw)
    config = LLMConfig('deepseek', recorded['model'], max_tokens=recorded['max_tokens'],
                       thinking_enabled=True, reasoning_effort=recorded['reasoning_effort'])
    fixed = upstream_payload(raw, config)
    accepted = json.loads((evidence / 'result.json').read_text(encoding='utf-8'))
    rejected = json.loads((evidence / 'original-wire-result.json').read_text(encoding='utf-8'))
    assert rejected['http_status'] == 400
    assert 'stream_options' in rejected['provider_error']['message']
    assert accepted['status'] == 'model_response_received'
    assert accepted['tool_execution_performed'] is False
    assert accepted['usage']['total_tokens'] > 0
    assert digest(fixed) == accepted['payload_sha256']
    assert raw == before


@pytest.mark.parametrize('finish,content,tool_calls,code', [
    ('length', '', [], 'output_truncated'),
    ('length', 'incomplete', [{'function': {'arguments': '{'}}], 'output_truncated'),
    ('stop', '  ', [], 'empty_final_content'),
    ('stop', 'visible text', [], None),
    ('tool_calls', '', [{'function': {'arguments': '{}'}}], None),
])
def test_completion_boundary_checks_finish_and_visible_output(
    finish: str, content: str, tool_calls: list[dict[str, Any]], code: str | None,
) -> None:
    # Pure validator inputs; no provider/service or tool execution is replaced.
    value = {'choices': [{'finish_reason': finish,
                         'message': {'content': content, 'tool_calls': tool_calls}}]}
    before = deepcopy(value)
    if code:
        with pytest.raises(LLMCompletionError) as caught:
            validate_wire_completion(value, provider='deepseek', model='configured-model')
        assert caught.value.reason['code'] == code
        assert caught.value.usage is None
        assert 'incomplete' not in str(caught.value)
    else:
        validate_wire_completion(value, provider='deepseek', model='configured-model')
    assert value == before


def test_rejected_completion_without_usage_remains_unknown(tmp_path: Path) -> None:
    state: dict[str, Any] = {'counts': {'model_responses': 0},
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
        'usage_complete': True, 'pending': 'model'}
    error = LLMCompletionError(code='empty_final_content', provider='deepseek',
        model='configured-model', finish_reason='stop', empty_final=True)
    record_rejected_completion(state, LoopTrace(tmp_path, 'full'), error)
    assert state['usage_complete'] is False
    assert state['pending'] is None
    assert state['counts']['model_responses'] == 1
    assert state['last_model_error'] == error.reason


def test_actual_truncated_zcode_responses_rejected_with_usage_and_audit(tmp_path: Path) -> None:
    configured = os.environ.get('MARS_TEST_ZCODE_TRUNCATION_TRACE')
    if not configured:
        pytest.skip('requires an actual truncated ZCode rollout archive')
    archive = next((Path(configured) / 'zcode_runtime/storage/cli/rollout').glob('model-io-*.jsonl'))
    rows = [json.loads(line) for line in archive.read_text(encoding='utf-8').splitlines()]
    truncated = [row['response'] for row in rows if row.get('response', {}).get('finishReason') == 'length']
    assert truncated and any(row['toolCalls'] for row in truncated)
    expected_usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    state: dict[str, Any] = {'status': 'running', 'counts': {'model_requests': 0, 'model_responses': 0},
        'usage': dict(expected_usage), 'usage_complete': True, 'fingerprint': 'archive-validation', 'pending': None}
    trace = LoopTrace(tmp_path, 'full')
    for recorded in truncated:
        # Restore only the response fields needed by the validator from the
        # official CLI's normalized archive. This never runs a model or tool.
        usage = {'prompt_tokens': recorded['usage']['inputTokens'],
                 'completion_tokens': recorded['usage']['outputTokens'],
                 'total_tokens': recorded['usage']['totalTokens']}
        response = {'choices': [{'finish_reason': recorded['finishReason'],
            'message': {'content': recorded['text'], 'tool_calls': recorded['toolCalls']}}], 'usage': usage}
        state['counts']['model_requests'] += 1
        state['pending'] = 'model'
        trace.emit('model_request', {'max_tokens': usage['completion_tokens']})
        with pytest.raises(LLMCompletionError) as caught:
            validate_wire_completion(response, provider='deepseek', model=recorded['modelId'])
        assert caught.value.reason['code'] == 'output_truncated'
        record_rejected_completion(state, trace, caught.value)
        for key in expected_usage:
            expected_usage[key] += usage[key]
    state['status'] = 'model_error'
    trace.snapshot(state)
    assert state['usage'] == expected_usage
    assert state['usage_complete'] is True and state['pending'] is None
    assert audit_trace(tmp_path)['consistent'] is True
    events = [json.loads(line) for line in trace.events.read_text(encoding='utf-8').splitlines()]
    assert not any('visible' in event for event in events)
    assert sum(event['kind'] == 'model_error' for event in events) == len(truncated)
