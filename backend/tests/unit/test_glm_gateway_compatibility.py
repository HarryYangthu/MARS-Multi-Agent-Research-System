"""Gateway serialization and parsing of authored envelopes; no simulated execution."""
import json
from typing import Any

from openai.types.chat import ChatCompletion
import pytest

from app.bridge.commander_compat import parse_text_decision
from app.bridge.commander_native import parse_native_commander_completion
from app.harness.llm.openai_provider import ZhipuProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.agent_loop.executor import validate_native_thinking
from app.harness.agent_loop.policy import AgentLoopPolicy


@pytest.mark.parametrize("model", ["glm-5", "glm-5.0", "GLM-5.2", "glm5.2", "glm-5.2-FP8"])
def test_glm5_thinking_tools_are_not_rejected_before_request(model: str) -> None:
    provider = ZhipuProvider(api_key="serialization-only")
    config = LLMConfig(provider="zhipu", model=model, thinking_enabled=True,
        tools=({"type": "function", "function": {"name": "mars_list_runs", "parameters": {"type": "object"}}},),
        extra={"native_observation_history": True})
    kwargs = provider._request_kwargs([Message("user", "只查看任务")], config)
    assert kwargs["tools"] and kwargs["messages"][0]["content"] == "只查看任务"
    assert provider._client is None
    validate_native_thinking(config, AgentLoopPolicy(protocol="native_tools", native_observation_history=True))


def test_old_function_call_envelope_becomes_an_explicit_native_command() -> None:
    envelope = ChatCompletion.model_validate({"id": "parser-input", "object": "chat.completion", "created": 0,
        "model": "glm-5.2", "choices": [{"index": 0, "finish_reason": "function_call", "message": {
            "role": "assistant", "content": None,
            "function_call": {"name": "mars_list_runs", "arguments": "{}"}}}]})
    provider = ZhipuProvider(api_key="parser-only")
    completion = provider._completion_from_response(envelope, LLMConfig(provider="zhipu", model="glm-5.2", tools=({"type": "function"},)))
    parsed = parse_native_commander_completion(completion)
    assert parsed["actions"] == [{"tool": "list_runs", "args": {}}]
    assert provider._client is None


@pytest.mark.parametrize("text", [
    '{"actions":[{"tool":"list_runs","args":{}}]}',
    '```json\n{"actions":[{"tool":"mars_list_runs","args":{}}]}\n```',
    '{"function_call":{"name":"mars_list_runs","arguments":"{}"}}',
])
def test_explicit_text_commands_keep_exact_tool_and_arguments(text: str) -> None:
    decision = parse_text_decision(text)
    assert decision is not None and decision["actions"] == [{"tool": "list_runs", "args": {}}]


@pytest.mark.parametrize("text", ["我将启动", "普通问题的回答", '{"actions":[{"tool":"invented","args":{}}]}',
    '{"actions":[{"tool":"list_runs"}]}', '{"actions":[{"tool":"list_runs","args":{},"arguments":{"x":1}}]}'])
def test_compatibility_never_invents_missing_execution_arguments(text: str) -> None:
    assert parse_text_decision(text) is None


@pytest.mark.parametrize("wrapper", ["data", "result"])
def test_gateway_wrapper_and_text_blocks_preserve_only_final_content(wrapper: str) -> None:
    payload = {wrapper: {"model": "GLM5.2", "choices": [{"message": {
        "role": "assistant", "reasoning_content": "private-parser-input",
        "content": [{"type": "text", "text": "最终答复"}, {"type": "reasoning", "text": "private-parser-input"}]},
        "finish_reason": "stop"}]}}
    provider = ZhipuProvider(api_key="parser-only")
    completion = provider._completion_from_response(payload, LLMConfig(provider="zhipu", model="GLM5.2"))
    assert completion.text == "最终答复" and "private-parser-input" not in repr(completion)
    assert provider._client is None


def test_gateway_text_fallback_omits_optional_extensions_without_guessing_model_parameters() -> None:
    provider = ZhipuProvider(api_key="serializer-only")
    config = LLMConfig(provider="zhipu", model="glm-5.0", thinking_enabled=False,
        extra={"gateway_plain_request": True})
    kwargs = provider._request_kwargs([Message("user", "原始要求")], config)
    assert kwargs["model"] == "glm-5.0" and kwargs["messages"][0]["content"] == "原始要求"
    assert not ({"tools", "extra_body", "response_format", "reasoning_effort", "parallel_tool_calls"} & set(kwargs))
    assert provider._client is None


def _sse(events: list[dict[str, Any]]) -> str:
    return ': keepalive\r\n\r\n' + ''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\r\n\r\n' for e in events) + 'data: [DONE]\r\n\r\n'


def _tool_events(legacy: bool = False) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for name, args in [('mars_', '{"project":'), ('list_runs', '"原始项目"}')]:
        function = {'name': name, 'arguments': args}
        delta = {'function_call': function} if legacy else {'tool_calls': [{'index': 0, 'id': 'actual-call-id', 'function': function}]}
        events.append({'model': 'GLM-V5.2', 'choices': [{'index': 0, 'delta': {**delta, 'reasoning_content': 'private-parser-marker'}}]})
    events.append({'model': 'GLM-V5.2', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'function_call' if legacy else 'tool_calls'}]})
    events.append({'model': 'GLM-V5.2', 'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 8, 'total_tokens': 15}})
    return events


@pytest.mark.parametrize('legacy', [False, True])
def test_sdk_returning_sse_string_preserves_complete_tool_call(legacy: bool) -> None:
    provider = ZhipuProvider(api_key='parser-only')
    result = provider._completion_from_response(_sse(_tool_events(legacy)),
        LLMConfig(provider='zhipu', model='GLM-V5.2', tools=({'type': 'function'},)))
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.name == 'mars_list_runs'
    assert json.loads(call.arguments) == {'project': '原始项目'}
    assert legacy or call.id == 'actual-call-id'
    assert result.raw['usage']['total_tokens'] == 15
    assert 'private-parser-marker' not in repr(result)
    assert result.model == 'GLM-V5.2' and provider._client is None


def test_sdk_stream_chunks_support_interleaved_indexed_calls_without_private_reasoning() -> None:
    from app.harness.llm.openai_provider import VisibleStreamAccumulator
    from openai.types.chat import ChatCompletionChunk
    state = VisibleStreamAccumulator()
    for event in [
        {'tool_calls': [{'index': 1, 'id': 'b', 'function': {'name': 'mars_list_runs', 'arguments': '{'}},
                        {'index': 0, 'id': 'a', 'function': {'name': 'mars_list_runs', 'arguments': '{"project":'}}]},
        {'tool_calls': [{'index': 0, 'function': {'arguments': '"p"}'}}, {'index': 1, 'function': {'arguments': '}'}}]},
    ]:
        state.add(ChatCompletionChunk.model_validate({'id': 'parser-input', 'created': 0, 'object': 'chat.completion.chunk',
            'model': 'GLM-V5.0', 'choices': [{'index': 0, 'delta': {**event, 'reasoning_content': 'private-parser-marker'}}]}))
    state.add({'model': 'GLM-V5.0', 'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]})
    result = state.completion(provider='zhipu', model='GLM-V5.0', allow_tools=True)
    assert [(c.id, json.loads(c.arguments)) for c in result.tool_calls] == [('a', {'project': 'p'}), ('b', {})]
    assert 'private-parser-marker' not in repr(state)


@pytest.mark.parametrize('model', ['GLM-V5.2', 'GLM-V5.0', 'glm_v5.2'])
def test_company_alias_and_tool_streaming_preserve_actual_model_id(model: str) -> None:
    provider = ZhipuProvider(api_key='serializer-only')
    config = LLMConfig(provider='zhipu', model=model, thinking_enabled=True,
        tools=({'type': 'function', 'function': {'name': 'mars_list_runs'}},),
        extra={'native_observation_history': True})
    kwargs = provider._request_kwargs([Message('user', '查看任务')], config, stream=True)
    validate_native_thinking(config, AgentLoopPolicy(protocol='native_tools', native_observation_history=True))
    assert kwargs['stream'] is True and kwargs['model'] == model and kwargs['tools']
    assert provider._client is None


def test_nonstream_json_string_is_also_accepted_without_sdk_attribute_access() -> None:
    provider = ZhipuProvider(api_key='parser-only')
    result = provider._completion_from_response(json.dumps({'result': {'model': 'GLM-V5.2', 'choices': [
        {'message': {'content': '最终正文'}, 'finish_reason': 'stop'}]}}), LLMConfig(provider='zhipu', model='GLM-V5.2'))
    assert result.text == '最终正文'


@pytest.mark.parametrize('broken', ['truncated', 'bad_json', 'error_event', 'unsolicited'])
def test_broken_sse_cannot_be_mistaken_for_an_executable_tool_call(broken: str) -> None:
    from app.harness.llm.provider_base import LLMCompletionError
    events = _tool_events()
    if broken == 'truncated':
        events = events[:2]
    elif broken == 'error_event':
        events.append({'error': {'message': 'private-service-data'}})
    body = _sse(events) if broken != 'bad_json' else 'data: {"choices":\n\ndata: [DONE]\n\n'
    provider = ZhipuProvider(api_key='parser-only')
    config = LLMConfig(provider='zhipu', model='GLM-V5.2', tools=() if broken == 'unsolicited' else ({'type': 'function'},))
    with pytest.raises((LLMCompletionError, ValueError)) as caught:
        provider._completion_from_response(body, config)
    assert 'private-service-data' not in str(caught.value)


def test_external_coding_wire_also_decodes_sse_tool_arguments() -> None:
    provider = ZhipuProvider(api_key='parser-only')
    result = provider._wire_from_response(_sse(_tool_events()), {'tools': [{'type': 'function'}]},
        LLMConfig(provider='zhipu', model='GLM-V5.2'))
    call = result['choices'][0]['message']['tool_calls'][0]
    assert call['id'] == 'actual-call-id' and call['function']['name'] == 'mars_list_runs'
    assert json.loads(call['function']['arguments']) == {'project': '原始项目'}
    assert result['choices'][0]['finish_reason'] == 'tool_calls' and result['usage']['total_tokens'] == 15
    assert 'private-parser-marker' not in repr(result)
