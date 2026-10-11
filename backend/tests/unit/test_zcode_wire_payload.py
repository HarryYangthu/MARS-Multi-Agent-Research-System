"""Pure request adaptation and optional real DeepSeek rejection evidence."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from app.harness.agent_loop.trace import digest
from app.harness.agent_loop.zcode.gateway import upstream_payload
from app.harness.llm.provider_base import LLMConfig
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
