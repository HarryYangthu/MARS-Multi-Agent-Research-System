"""Provider serialization/parsing and actual transport failures; no SDK/service replacements."""
from __future__ import annotations

import asyncio
import socket
from typing import Any, cast

import httpx
import pytest
from openai import APIConnectionError, APIStatusError
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from app.harness.llm.openai_provider import DeepSeekProvider, OpenAIProvider, _is_retryable_error
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
async def test_actual_connection_refusal_obeys_retry_cap() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    def observe(kind: str, row: dict[str, Any]) -> None:
        events.append((kind, row))
    # Reserve a real local port without listening: the actual SDK must encounter
    # connection refusal. No server returns a response and no execution succeeds.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        provider = DeepSeekProvider(api_key="transport-contract-not-a-credential",
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
