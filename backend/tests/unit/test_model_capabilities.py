"""Pure model protocol contracts and a real refused transport, without doubles."""
from __future__ import annotations

import json
import socket
from dataclasses import replace

import pytest
from openai import APIConnectionError
from openai.types.chat import ChatCompletion

from app.harness.agent_loop.context import pack_context
from app.harness.agent_loop.executor import phase_llm_config, validate_native_thinking
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.llm.model_capabilities import ModelCompatibilityError, requires_glm_thinking, setup_reasoning
from app.harness.llm.openai_provider import ZhipuProvider
from app.harness.llm.provider_base import LLMConfig, Message, ToolCall

TOOLS = ({"type": "function", "function": {"name": "inspect_document", "parameters": {"type": "object"}}},)


@pytest.mark.parametrize("model", ["glm-5.3", "GLM-5.3", "glm-5.3-flash"])
def test_glm_family_requires_explicit_thinking_with_bounded_default_effort(model: str) -> None:
    assert requires_glm_thinking("zhipu", model)
    assert setup_reasoning("zhipu", model) == (True, "low")
    provider = ZhipuProvider(api_key="serialization-only")
    config = LLMConfig(provider="zhipu", model=model)
    request = provider._request_kwargs([Message("user", "request construction")], config)
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert request["reasoning_effort"] == "low"
    with pytest.raises(ModelCompatibilityError, match="thinking enabled"):
        provider._request_kwargs([], replace(config, thinking_enabled=False))
    with pytest.raises(ModelCompatibilityError, match="low, high or max"):
        provider._request_kwargs([], replace(config, reasoning_effort="medium"))
    assert provider._client is None


@pytest.mark.parametrize("provider,model", [
    ("zhipu", "glm-5.30"), ("zhipu", "glm-5.3x"), ("zhipu", "glm-5.2"),
    ("custom", "glm-5.3"), ("openai", "glm-5.3"),
])
def test_model_family_detection_does_not_expand_to_unreviewed_routes(provider: str, model: str) -> None:
    assert not requires_glm_thinking(provider, model)
    assert setup_reasoning(provider, model) == (False, None)


def test_native_glm_rounds_keep_observations_without_assistant_or_tool_history() -> None:
    policy = AgentLoopPolicy(protocol="native_tools", native_observation_history=True,
                             reflection_thinking_enabled=True, reflection_reasoning_effort="low")
    config = LLMConfig(provider="zhipu", model="glm-5.3", thinking_enabled=True, reasoning_effort="low")
    validate_native_thinking(config, policy)
    # Pure serialization of a failure receipt; this is not an executed tool
    # result and does not stand in for a successful provider/tool loop.
    history = [{"tool": "inspect_document", "args": {"path": "missing.md"}, "ok": False,
                "error": "missing_file", "reason": "inspect the supplied reference", "raw_ref": "receipt.json"}]
    messages, _ = pack_context([Message("system", "Follow the project rules"), Message("user", "Read the reference")],
                               history, "", "", budget=8000, observation_chars=2000, native=True,
                               native_observation_history=policy.native_observation_history)
    assert all(message.role in {"system", "user"} for message in messages)
    assert "missing_file" in "\n".join(message.content for message in messages)
    provider = ZhipuProvider(api_key="serialization-only")
    for phase in ("act", "reflect"):
        selected = phase_llm_config(config, policy, phase=phase, native=True, wire_tools=TOOLS, effort_overrides={})
        request = provider._request_kwargs(messages, selected)
        assert request["extra_body"] == {"thinking": {"type": "enabled"}}
        assert request["reasoning_effort"] == "low"
        assert "reasoning_content" not in json.dumps(request)
        assert ("tools" in request) is (phase == "act")


def test_glm_rejects_native_history_and_unsupported_tool_streaming() -> None:
    provider = ZhipuProvider(api_key="serialization-only")
    config = LLMConfig(provider="zhipu", model="glm-5.3", tools=TOOLS, thinking_enabled=True,
                       extra={"native_observation_history": True})
    call = ToolCall("serialization-only", "inspect_document", "{}")
    for messages in ([Message("assistant", "", (call,))], [Message("tool", "receipt", tool_call_id=call.id)]):
        with pytest.raises(ModelCompatibilityError, match="observation-only"):
            provider._request_kwargs(messages, config)
    with pytest.raises(ModelCompatibilityError, match="observation-only"):
        provider._request_kwargs([Message("user", "input")], replace(config, extra={}))
    with pytest.raises(ValueError, match="streaming"):
        provider._request_kwargs([Message("user", "input")], config, stream=True)


@pytest.mark.parametrize("provider,model,thinking,observations", [
    ("zhipu", "glm-5.3", True, False), ("zhipu", "glm-5.3", False, True),
    ("zhipu", "glm-5.2", True, True), ("deepseek", "deepseek-chat", True, False),
    ("openai", "configured-model", True, True),
])
def test_unsupported_native_thinking_modes_fail_before_execution(
    provider: str, model: str, thinking: bool, observations: bool,
) -> None:
    with pytest.raises(ModelCompatibilityError):
        validate_native_thinking(LLMConfig(provider=provider, model=model, thinking_enabled=thinking),
                                 AgentLoopPolicy(protocol="native_tools", native_observation_history=observations))


def test_glm_native_envelope_parser_excludes_reasoning_from_public_completion() -> None:
    # Authored SDK envelope is only parser input, never a replacement transport.
    envelope = ChatCompletion.model_validate({
        "id": "parser-contract", "object": "chat.completion", "created": 0, "model": "glm-5.3",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "reasoning_content": "private-parser-marker",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "inspect_document", "arguments": "{}"}}],
        }}], "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
    })
    result = ZhipuProvider(api_key="serialization-only")._completion_from_response(
        envelope, LLMConfig(provider="zhipu", model="glm-5.3", tools=TOOLS))
    assert result.tool_calls == (ToolCall("call-1", "inspect_document", "{}"),)
    assert result.raw["usage"]["total_tokens"] == 13
    assert "private-parser-marker" not in repr(result)
    assert "reasoning_content" not in repr(result)


@pytest.mark.asyncio
async def test_glm_native_tools_reach_real_non_stream_transport() -> None:
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        provider = ZhipuProvider(api_key="transport-failure-only", base_url=f"http://127.0.0.1:{port}/v1")
        config = LLMConfig(provider="zhipu", model="glm-5.3", tools=TOOLS, thinking_enabled=True,
                           reasoning_effort="low", extra={"native_observation_history": True},
                           max_retries=0, request_timeout_seconds=0.3)
        try:
            # No listening server and no substitute response: this verifies the
            # native request reaches transport instead of the unsupported SSE path.
            with pytest.raises(APIConnectionError):
                await provider.complete([Message("user", "transport failure check")], config)
        finally:
            await provider.close()
