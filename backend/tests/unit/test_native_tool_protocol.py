"""Pure message/SDK envelope contracts, never substitutes for live execution."""
import pytest
from openai.types.chat import ChatCompletion
from app.harness.llm.provider_base import Completion, LLMConfig, Message, ToolCall
from app.harness.llm.openai_provider import DeepSeekProvider
from app.harness.agent_loop.native_protocol import native_decision, wire_name
from app.harness.agent_loop.context import pack_context


def test_native_envelope_and_wire_roundtrip() -> None:
    provider = DeepSeekProvider(api_key="parser-input-not-a-key")
    cfg = LLMConfig(provider="deepseek", model="parser-contract", thinking_enabled=False,
                    tools=({"type": "function", "function": {"name": "read"}},))
    envelope = ChatCompletion.model_validate({"id": "authored-parser-input", "object": "chat.completion",
        "created": 0, "model": "parser-contract", "choices": [{"index": 0, "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
        "function": {"name": wire_name("file.read"), "arguments": '{"path":"a"}'}}]}}]})
    completion = provider._completion_from_response(envelope, cfg)
    decision = native_decision(completion, ("file.read",))
    assert decision['tool'] == 'file.read' and decision['args'] == {'path': 'a'}
    messages = [Message('assistant', '', completion.tool_calls), Message('tool', 'actual content', tool_call_id='c1')]
    wire = provider._request_kwargs(messages, cfg)
    assert wire['messages'][1]['tool_call_id'] == wire['messages'][0]['tool_calls'][0]['id']
    assert wire['extra_body']['thinking']['type'] == 'disabled'
    assert provider._client is None


@pytest.mark.parametrize('args', ['{"x":1,"x":2}', '[]', '{"x":NaN}', '{"x":1e999}'])
def test_invalid_arguments_never_become_actions(args: str) -> None:
    with pytest.raises(ValueError):
        native_decision(Completion('', 'parser', 'parser', tool_calls=(ToolCall('c1',wire_name('read'),args),)), ('read',))


def test_compaction_keeps_complete_call_result_pairs() -> None:
    history = [{'tool':'read', 'args':{}, 'reason':'inspect', 'ok':True, 'output':'x'*12000,
        'native_call':{'id':f'c{i}','name':wire_name('read'),'arguments':'{}'}} for i in range(5)]
    messages, manifest = pack_context([Message('system','task')], history, '', '', budget=4000, observation_chars=512)
    for i,m in enumerate(messages):
        if m.role == 'tool':
            assert messages[i-1].tool_calls[0].id == m.tool_call_id
        if m.tool_calls:
            assert messages[i+1].tool_call_id == m.tool_calls[0].id
    assert manifest['estimated_upper_bound_tokens'] <= 4000
