"""Pure wire inputs and actual failed transports; no returned model/service substitutes."""
from __future__ import annotations

import asyncio
import inspect
import json
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from anthropic import APIConnectionError
from anthropic.resources.messages import AsyncMessages

from app.harness.llm.anthropic_provider import AnthropicProvider
from app.harness.llm.gemini_provider import GeminiProvider
from app.harness.llm.provider_base import LLMConfig, Message, ToolCall
from app.harness.llm.retry import request_with_retries, stream_with_retries


_TOOLS: tuple[dict[str, Any], ...] = ({"type": "function", "function": {
    "name": "lookup", "description": "serializer input", "parameters": {
        "type": "object", "properties": {"index": {"type": "integer"}},
        "required": ["index"], "additionalProperties": False,
    },
}},)


def _wire_messages() -> list[Message]:
    return [Message("system", "caller-authored system input"), Message("user", "caller-authored user input"),
            Message("assistant", "", tool_calls=(ToolCall("first", "lookup", '{"index":1}'),
                                                     ToolCall("second", "lookup", '{"index":2}'))),
            Message("tool", "caller-authored serializer field A", tool_call_id="first"),
            Message("tool", "caller-authored serializer field B", tool_call_id="second")]


def test_anthropic_serializes_parallel_results_in_one_user_turn() -> None:
    provider = AnthropicProvider(api_key="wire-contract-not-a-credential")
    request = provider._request_kwargs(_wire_messages(), LLMConfig(provider="anthropic", model="wire-contract",
                                       tools=_TOOLS, thinking_enabled=False, json_mode=True))
    assert len(request["messages"]) == 3
    assert request["messages"][1]["role"] == "assistant"
    assert [item["id"] for item in request["messages"][1]["content"]] == ["first", "second"]
    assert request["messages"][2]["role"] == "user"
    assert [item["tool_use_id"] for item in request["messages"][2]["content"]] == ["first", "second"]
    assert request["tools"][0]["input_schema"] == _TOOLS[0]["function"]["parameters"]
    assert "valid JSON" in request["system"] and provider._client is None


def test_gemini_serializes_parallel_function_ids_and_full_json_schema() -> None:
    provider = GeminiProvider(api_key="wire-contract-not-a-credential")
    request = provider._request_body(_wire_messages(), LLMConfig(provider="gemini", model="wire-contract",
                                     tools=_TOOLS, thinking_enabled=False))
    assert len(request["contents"]) == 3
    assert request["contents"][1]["role"] == "model"
    assert [item["functionCall"]["id"] for item in request["contents"][1]["parts"]] == ["first", "second"]
    assert [item["functionResponse"]["id"] for item in request["contents"][2]["parts"]] == ["first", "second"]
    assert request["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == _TOOLS[0]["function"]["parameters"]
    assert request["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert provider._client is None


@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
@pytest.mark.parametrize("messages", [
    [Message("tool", "authored field", tool_call_id="orphan")],
    [Message("user", "authored field", tool_calls=(ToolCall("id", "lookup", "{}"),))],
    [Message("assistant", "", tool_calls=(ToolCall("id", "lookup", "[]"),))],
    [Message("assistant", "", tool_calls=(ToolCall("id", "lookup", '{"n":NaN}'),))],
    [Message("assistant", "", tool_calls=(ToolCall("id", "lookup", "{}"),))],
    [Message("assistant", "", tool_calls=(ToolCall("id", "lookup", "{}"),)), Message("user", "interleaved")],
    [Message("assistant", "", tool_calls=(ToolCall("id", "lookup", "{}"),)),
     Message("tool", "authored field", tool_call_id="id"), Message("tool", "duplicate", tool_call_id="id")],
])
def test_native_history_rejects_malformed_roles_ids_and_unresolved_calls(provider: str, messages: list[Message]) -> None:
    with pytest.raises(ValueError):
        if provider == "anthropic":
            AnthropicProvider._split_system(messages)
        else:
            GeminiProvider._format_contents(messages)


@pytest.mark.parametrize("provider_class", [AnthropicProvider, GeminiProvider])
def test_missing_keys_fail_explicitly(provider_class: type[AnthropicProvider] | type[GeminiProvider]) -> None:
    with pytest.raises(ValueError, match="API_KEY"):
        provider_class(api_key="")


def test_unsupported_thinking_controls_fail_before_client_creation() -> None:
    anthropic = AnthropicProvider(api_key="wire-contract-not-a-credential")
    gemini = GeminiProvider(api_key="wire-contract-not-a-credential")
    with pytest.raises(ValueError, match="thinking"):
        anthropic._request_kwargs([], LLMConfig(provider="anthropic", model="wire-contract", thinking_enabled=True))
    with pytest.raises(ValueError, match="thinking"):
        gemini._request_body([], LLMConfig(provider="gemini", model="wire-contract", tools=_TOOLS))
    assert anthropic._client is gemini._client is None


@pytest.mark.parametrize("streaming", [False, True])
def test_anthropic_sampling_configuration_matches_installed_sdk(streaming: bool) -> None:
    provider = AnthropicProvider(api_key="wire-contract-not-a-credential")
    config = LLMConfig(provider="anthropic", model="wire-contract", temperature=0.25)
    method = AsyncMessages.stream if streaming else AsyncMessages.create
    if "temperature" in inspect.signature(method).parameters:
        assert provider._request_kwargs([Message("user", "wire input")], config, stream=streaming)["temperature"] == 0.25
    else:
        with pytest.raises(ValueError, match="custom temperature"):
            provider._request_kwargs([Message("user", "wire input")], config, stream=streaming)
        request = provider._request_kwargs([Message("user", "wire input")],
                    LLMConfig(provider="anthropic", model="wire-contract"), stream=streaming)
        assert "temperature" not in request
    assert provider._client is None


def test_usage_normalization_preserves_unknown_fields_and_cache_tokens() -> None:
    assert AnthropicProvider._usage({"input_tokens": 10, "output_tokens": 3,
        "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2}) == {
            "prompt_tokens": 17, "completion_tokens": 3, "total_tokens": 20}
    assert AnthropicProvider._usage({"input_tokens": 10}) is None
    assert AnthropicProvider._usage({"input_tokens": 10, "output_tokens": None}) is None
    assert GeminiProvider._usage({"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3,
        "thoughtsTokenCount": 2, "totalTokenCount": 15}}) == {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    partial = GeminiProvider._usage({"usageMetadata": {"promptTokenCount": 10, "totalTokenCount": 15}})
    assert partial == {"prompt_tokens": 10, "total_tokens": 15}
    assert GeminiProvider._usage({"usageMetadata": {"promptTokenCount": True, "totalTokenCount": -1,
                                                   "candidatesTokenCount": None}}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["anthropic", "gemini"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_actual_refused_connection_obeys_retry_cap_in_completion_and_stream(provider_name: str, streaming: bool) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    def observe(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    # The OS owns this bound but non-listening port. There is no responding
    # HTTP server, and no provider object/client is substituted.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        base_url = f"http://127.0.0.1:{sock.getsockname()[1]}/v1"
        provider = (AnthropicProvider(api_key="local-transport-key", base_url=base_url) if provider_name == "anthropic"
                    else GeminiProvider(api_key="local-transport-key", base_url=base_url))
        config = LLMConfig(provider=provider_name, model="unavailable-local-model", max_retries=99,
                           retry_base_delay_seconds=0, request_timeout_seconds=0.2, attempt_observer=observe)
        deltas = []

        async def invoke() -> None:
            if streaming:
                async for delta in provider.stream([Message("user", "actual connection failure check")], config):
                    deltas.append(delta)
            else:
                await provider.complete([Message("user", "actual connection failure check")], config)

        try:
            with pytest.raises((APIConnectionError, httpx.ConnectError)):
                await asyncio.wait_for(invoke(), timeout=10)
        finally:
            await provider.close()
    assert len([kind for kind, _ in events if kind == "sdk_attempt_started"]) == 4
    assert len([kind for kind, _ in events if kind == "sdk_attempt_failed"]) == 4
    assert not [kind for kind, _ in events if kind == "sdk_attempt_succeeded"]
    assert not deltas and provider._client is None and "local-transport-key" not in repr(events)


@pytest.mark.asyncio
async def test_generic_stream_never_replays_real_local_output_after_transport_failure(tmp_path: Path) -> None:
    marker = tmp_path / "local-marker.txt"
    marker.write_text("actual local file bytes")
    events: list[str] = []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        async with httpx.AsyncClient(timeout=0.2, trust_env=False) as client:
            async def file_then_connect() -> AsyncIterator[str]:
                yield marker.read_text()
                await client.get(f"http://127.0.0.1:{sock.getsockname()[1]}/")

            config = LLMConfig(provider="local-lifecycle-contract", model="none", max_retries=3,
                               retry_base_delay_seconds=0, attempt_observer=lambda kind, _: events.append(kind))
            received: list[str] = []
            with pytest.raises(httpx.ConnectError):
                async for item in stream_with_retries(file_then_connect, config):
                    received.append(item)
    assert received == ["actual local file bytes"]
    assert events == ["sdk_attempt_started", "sdk_attempt_failed"]


@pytest.mark.asyncio
async def test_generic_retry_does_not_retry_actual_local_parse_failure(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{incomplete")
    events: list[str] = []

    async def read_json() -> Any:
        return json.loads(path.read_text())

    config = LLMConfig(provider="local-parser-contract", model="none", max_retries=3,
                       retry_base_delay_seconds=0, attempt_observer=lambda kind, _: events.append(kind))
    with pytest.raises(json.JSONDecodeError):
        await request_with_retries(read_json, config)
    assert events == ["sdk_attempt_started", "sdk_attempt_failed"]


@pytest.mark.asyncio
async def test_generic_retry_propagates_real_task_cancellation_without_retry() -> None:
    started = asyncio.Event()
    events: list[str] = []

    async def wait_for_cancellation() -> None:
        started.set()
        await asyncio.Event().wait()

    config = LLMConfig(provider="local-lifecycle-contract", model="none", max_retries=3,
                       retry_base_delay_seconds=0, attempt_observer=lambda kind, _: events.append(kind))
    task = asyncio.create_task(request_with_retries(wait_for_cancellation, config))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["sdk_attempt_started", "sdk_attempt_failed"]
