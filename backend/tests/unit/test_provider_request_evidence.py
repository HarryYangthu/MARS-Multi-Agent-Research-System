from app.harness.agent_loop.trace import LoopTrace
from app.harness.llm.provider_base import LLMConfig
from app.harness.llm.request_evidence import record_provider_request
from pathlib import Path
import json


def test_outbound_body_preserves_messages_but_excludes_transport_and_private_reasoning(tmp_path: Path) -> None:
    trace = LoopTrace(tmp_path, 'full')
    state = {'counts': {'model_requests': 1, 'sdk_attempts': 0}}
    config = LLMConfig(provider='openai', model='test-contract', attempt_observer=lambda kind, row: trace.record_attempt(state, kind, row))
    record_provider_request(config, 'openai', {'model': 'test-contract', 'messages': [{'role': 'user', 'content': 'input'}], 'authorization': 'private', 'api_key': 'private', 'extra_body': {'thinking': {'type': 'enabled'}, 'api_key': 'private'}, 'reasoning_content': 'hidden'})
    row = json.loads(trace.events.read_text())
    assert row['visible']['messages'][0]['content'] == 'input'
    assert row['visible']['thinking'] == {'type': 'enabled'}
    assert 'extra_body' not in row['visible']
    assert 'private' not in trace.events.read_text() and 'hidden' not in trace.events.read_text()
    assert state['counts']['sdk_attempts'] == 0
