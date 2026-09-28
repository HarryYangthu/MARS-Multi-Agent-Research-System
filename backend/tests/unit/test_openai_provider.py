"""Provider serialization/parsing and actual transport failures; no SDK/service replacements."""
from __future__ import annotations

import asyncio
import socket
from typing import Any, cast

import httpx
import pytest
from openai import APIConnectionError, APIStatusError
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from app.harness.llm.openai_provider import DeepSeekProvider, OpenAIProvider, ZhipuProvider, VisibleStreamAccumulator, _is_retryable_error
from app.harness.llm.provider_base import LLMCompletionError, LLMConfig, Message


def test_deepseek_request_serializer_and_extension_isolation() -> None:
    config = LLMConfig(provider="deepseek", model="configured-model", thinking_enabled=True,
                       reasoning_effort="high", request_timeout_seconds=120, max_tokens=4096)
    provider = DeepSeekProvider(api_key="serializer-input-not-a-credential")
    kwargs = provider._request_kwargs([Message("user", "authored serializer input")], config)
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["reasoning_effort"] == "high" and kwargs["timeout"] == 120
    assert kwargs["max_tokens"] == 4096
    assert "temperature" not in kwargs and "top_p" not in kwargs
    assert provider._client is None
    generic = OpenAIProvider(api_key="serializer-input-not-a-credential")
    request = generic._request_kwargs([], LLMConfig(provider="openai", model="configured-model"))
    assert "extra_body" not in request and "reasoning_effort" not in request


@pytest.mark.parametrize("content,finish,expected", [
    ("authored parser input", "stop", None), (None, "stop", "empty_final_content"),
    (None, "length", "output_truncated"), ("partial authored input", "length", "output_truncated"),
])
def test_envelope_parser_preserves_usage_and_never_promotes_private_fields(
    content: str | None, finish: str, expected: str | None,
) -> None:
    # A typed, manually authored SDK envelope is pure parser input. No call to
    # complete(), client assignment, service response, or real-run claim occurs.
    envelope = ChatCompletion.model_validate({
        "id": "authored-parser-envelope", "object": "chat.completion", "created": 0, "model": "parser-contract",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content, "reasoning_content": "private-field-marker"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        "system_fingerprint": "authored-parser-metadata",
    })
    provider = OpenAIProvider(api_key="parser-input-not-a-credential")
    config = LLMConfig(provider="openai", model="parser-contract")
    if expected:
        with pytest.raises(LLMCompletionError) as caught:
            provider._completion_from_response(envelope, config)
        assert caught.value.reason["code"] == expected
        assert caught.value.reason["empty_final"] is (content is None)
        assert caught.value.usage is not None and caught.value.usage["total_tokens"] == 18
        assert "private-field-marker" not in repr(caught.value)
    else:
        result = provider._completion_from_response(envelope, config)
        assert result.text == content and result.raw["usage"]["total_tokens"] == 18
        assert result.raw["finish_reason"] == "stop"
        assert "private-field-marker" not in repr(result)
    assert provider._client is None


@pytest.mark.parametrize("content,finish", [(None, None), ("authored delta", "length"), (None, "stop")])
def test_stream_delta_parser_excludes_private_fields(content: str | None, finish: str | None) -> None:
    chunk = ChatCompletionChunk.model_validate({
        "id": "authored-delta-envelope", "object": "chat.completion.chunk", "created": 0, "model": "parser-contract",
        "choices": [{"index": 0, "finish_reason": finish,
                     "delta": {"content": content, "reasoning_content": "private-field-marker"}}],
    })
    provider = OpenAIProvider(api_key="parser-input-not-a-credential")
    assert provider._visible_stream_delta(chunk) == (content or "", finish)
    config = LLMConfig(provider="openai", model="parser-contract")
    if finish is not None:
        with pytest.raises(LLMCompletionError) as caught:
            provider._check_final(config, finish, bool(content))
        assert caught.value.reason["code"] == ("output_truncated" if finish == "length" else "empty_final_content")


@pytest.mark.parametrize("status,retryable", [(400, False), (401, False), (429, True), (503, True)])
def test_retry_classifier_on_typed_error_inputs(status: int, retryable: bool) -> None:
    # Exception classification only: this response is never injected into a transport.
    # Some SDK builds vendor HTTPX types; the exception only reads this interface.
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))
    error = APIStatusError("authored classifier input", response=cast(Any, response), body=None)
    assert _is_retryable_error(error) is retryable
    assert _is_retryable_error(asyncio.TimeoutError())


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", [DeepSeekProvider, ZhipuProvider])
async def test_actual_connection_refusal_obeys_retry_cap(provider_class: type[DeepSeekProvider] | type[ZhipuProvider]) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    def observe(kind: str, row: dict[str, Any]) -> None:
        events.append((kind, row))
    # Reserve a real local port without listening: the actual SDK must encounter
    # connection refusal. No server returns a response and no execution succeeds.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        provider = provider_class(api_key="transport-contract-not-a-credential",
                                    base_url=f"http://127.0.0.1:{port}/v1")
        config = LLMConfig(provider="deepseek", model="unavailable-local-model", max_retries=99,
                           retry_base_delay_seconds=0, request_timeout_seconds=0.3, attempt_observer=observe)
        try:
            with pytest.raises(APIConnectionError):
                await asyncio.wait_for(provider.complete([Message("user", "transport failure check")], config), timeout=10)
        finally:
            await provider.close()
    assert len([e for e in events if e[0] == "sdk_attempt_started"]) == 4
    assert len([e for e in events if e[0] == "sdk_attempt_failed"]) == 4
    assert not [e for e in events if e[0] == "sdk_attempt_succeeded"]


@pytest.mark.parametrize("finish", [None, "length", "stop"])
def test_stream_assembly_requires_final_marker_and_preserves_only_public_content(finish: str | None) -> None:
    state = VisibleStreamAccumulator()
    for content in (None, "authored ", "parser input"):
        state.add(ChatCompletionChunk.model_validate({
            "id": "authored-stream", "object": "chat.completion.chunk", "created": 0, "model": "parser",
            "choices": [{"index": 0, "finish_reason": finish if content == "parser input" else None,
                         "delta": {"content": content, "reasoning_content": "private-field-marker"}}],
        }))
    state.add(ChatCompletionChunk.model_validate({
        "id": "authored-usage", "object": "chat.completion.chunk", "created": 0, "model": "parser",
        "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }))
    assert "private-field-marker" not in repr(state)
    if finish == "stop":
        result = state.completion(provider="zhipu", model="parser")
        assert result.text == "authored parser input" and result.raw["usage"]["total_tokens"] == 7
    else:
        with pytest.raises(LLMCompletionError) as caught:
            state.completion(provider="zhipu", model="parser")
        assert caught.value.reason["code"] == ("output_truncated" if finish == "length" else "incomplete_stream")


@pytest.mark.parametrize("reported,model,status", [
    ("returned-model-revision", "returned-model-revision", "consistent"),
    (None, "", "missing"), ("", "", "missing"),
    ("not a bounded identifier\nprivate-marker", "", "invalid"),
])
def test_envelope_model_identity_comes_only_from_response_fields(reported: str | None, model: str, status: str) -> None:
    # Manually authored SDK records are pure parser data, not a provider double.
    data = {"id": "identity-parser", "object": "chat.completion", "created": 0,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "parser input"}}]}
    envelope = ChatCompletion.model_validate({**data, "model": reported or "temporary-required-field"})
    if reported is None:
        delattr(envelope, "model")
    else:
        envelope.model = reported
    provider = OpenAIProvider(api_key="parser-input-not-a-credential")
    result = provider._completion_from_response(envelope, LLMConfig(provider="openai", model="requested-alias"))
    assert result.model == model
    assert result.raw["requested_model"] == "requested-alias"
    assert result.raw["response_models"] == ([model] if model else [])
    assert result.raw["response_model_status"] == status
    assert "private-marker" not in repr(result)
    assert provider._client is None


@pytest.mark.parametrize("reported,status,model", [
    (["response-v1", "response-v1"], "consistent", "response-v1"),
    (["response-v1", "response-v2"], "inconsistent", ""),
    ([None, None], "missing", ""),
    (["response-v1", None], "partial", "response-v1"),
    (["response-v1", "invalid\nprivate-marker"], "invalid", ""),
])
def test_stream_model_identity_records_conflict_missing_and_invalid_chunks(
    reported: list[str | None], status: str, model: str,
) -> None:
    state = VisibleStreamAccumulator()
    for index, identity in enumerate(reported):
        chunk = ChatCompletionChunk.model_validate({
            "id": "identity-parser", "object": "chat.completion.chunk", "created": 0,
            "model": identity or "temporary-required-field",
            "choices": [{"index": 0, "finish_reason": "stop" if index == len(reported) - 1 else None,
                         "delta": {"content": "authored parser input", "reasoning_content": "private-reasoning-marker"}}],
        })
        if identity is None:
            delattr(chunk, "model")
        state.add(chunk)
    result = state.completion(provider="zhipu", model="requested-alias")
    assert result.model == model
    assert result.raw["requested_model"] == "requested-alias"
    assert result.raw["response_model_status"] == status
    assert result.raw["response_model_missing_fields"] == reported.count(None)
    assert result.raw["response_models"] == sorted({value for value in reported if value and "\n" not in value})
    assert "private-marker" not in repr(result) and "private-reasoning-marker" not in repr(state)


def test_rejected_response_and_stream_preserve_response_identity_without_private_fields() -> None:
    envelope = ChatCompletion.model_validate({
        "id": "identity-parser", "object": "chat.completion", "created": 0, "model": "returned-model",
        "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    })
    provider = OpenAIProvider(api_key="parser-input-not-a-credential")
    with pytest.raises(LLMCompletionError) as error:
        provider._completion_from_response(envelope, LLMConfig(provider="openai", model="requested-alias"))
    assert error.value.model_identity["requested_model"] == "requested-alias"
    assert error.value.model_identity["response_models"] == ["returned-model"]
    assert error.value.model_identity["response_model_status"] == "consistent"
    state = VisibleStreamAccumulator()
    state.add(ChatCompletionChunk.model_validate({
        "id": "identity-parser", "object": "chat.completion.chunk", "created": 0, "model": "returned-model",
        "choices": [{"index": 0, "finish_reason": "length", "delta": {"content": "partial parser input"}}],
    }))
    with pytest.raises(LLMCompletionError) as truncated:
        state.completion(provider="zhipu", model="requested-alias")
    assert truncated.value.model_identity["response_models"] == ["returned-model"]
    assert truncated.value.model_identity["requested_model"] == "requested-alias"
