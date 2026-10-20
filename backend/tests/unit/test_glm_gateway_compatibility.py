"""Gateway serialization and parsing of authored envelopes; no simulated execution."""
import json

from openai.types.chat import ChatCompletion
import pytest

from app.bridge.commander_compat import parse_text_decision
from app.bridge.commander_native import parse_native_commander_completion
from app.harness.llm.openai_provider import ZhipuProvider
from app.harness.llm.provider_base import LLMConfig, Message


@pytest.mark.parametrize("model", ["glm-5", "glm-5.0", "GLM-5.2", "glm5.2", "glm-5.2-FP8"])
def test_glm5_thinking_tools_are_not_rejected_before_request(model: str) -> None:
    provider = ZhipuProvider(api_key="serialization-only")
    config = LLMConfig(provider="zhipu", model=model, thinking_enabled=True,
        tools=({"type": "function", "function": {"name": "mars_list_runs", "parameters": {"type": "object"}}},),
        extra={"native_observation_history": True})
    kwargs = provider._request_kwargs([Message("user", "只查看任务")], config)
    assert kwargs["tools"] and kwargs["messages"][0]["content"] == "只查看任务"
    assert provider._client is None


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
    assert parse_text_decision(text)["actions"] == [{"tool": "list_runs", "args": {}}]


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
