from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.harness.llm.openai_provider import DeepSeekProvider, OpenAIProvider
from app.harness.llm.provider_base import LLMCompletionError, LLMConfig, Message


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status={status_code}")
        self.status_code = status_code


class _Usage:
    def model_dump(self, *, exclude_none: bool) -> dict[str, int]:
        assert exclude_none is True
        return {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }


class _FakeCompletions:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _client(completions: _FakeCompletions) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _response(
    *,
    content: str | None = "visible",
    finish_reason: str = "stop",
) -> Any:
    message = SimpleNamespace(
        content=content,
        reasoning_content="hidden chain of thought",
    )
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(
        choices=[choice],
        usage=_Usage(),
        system_fingerprint="fp-test",
    )


def _config(**overrides: Any) -> LLMConfig:
    values: dict[str, Any] = {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "temperature": 0.2,
        "max_tokens": 4096,
        "thinking_enabled": True,
        "reasoning_effort": "high",
        "request_timeout_seconds": 120.0,
        "max_retries": 3,
        "retry_base_delay_seconds": 0.0,
    }
    values.update(overrides)
    return LLMConfig(**values)


@pytest.mark.asyncio
async def test_deepseek_complete_passes_thinking_and_records_safe_metadata() -> None:
    completions = _FakeCompletions([_response()])
    provider = DeepSeekProvider(api_key="test")
    provider._client = _client(completions)

    completion = await provider.complete(
        [Message(role="user", content="question")],
        _config(),
    )

    assert completion.text == "visible"
    assert completion.raw == {
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        },
        "system_fingerprint": "fp-test",
        "finish_reason": "stop",
    }
    assert "hidden chain of thought" not in repr(completion.raw)
    request = completions.calls[0]
    assert request["reasoning_effort"] == "high"
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert request["timeout"] == 120.0
    assert request["max_tokens"] == 4096
    assert "temperature" not in request
    assert "top_p" not in request


@pytest.mark.asyncio
async def test_complete_never_promotes_reasoning_content_to_final_text() -> None:
    completions = _FakeCompletions([_response(content=None)])
    provider = DeepSeekProvider(api_key="test")
    provider._client = _client(completions)

    with pytest.raises(LLMCompletionError) as exc_info:
        await provider.complete(
            [Message(role="user", content="question")],
            _config(),
        )

    assert exc_info.value.reason == {
        "code": "empty_final_content",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "finish_reason": "stop",
        "empty_final": True,
    }
    assert "hidden chain of thought" not in repr(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "partial visible document"])
async def test_complete_fails_closed_on_length_truncation(
    content: str | None,
) -> None:
    completions = _FakeCompletions(
        [_response(content=content, finish_reason="length")]
    )
    provider = DeepSeekProvider(api_key="test")
    provider._client = _client(completions)

    with pytest.raises(LLMCompletionError) as exc_info:
        await provider.complete(
            [Message(role="user", content="question")],
            _config(),
        )

    assert exc_info.value.reason["code"] == "output_truncated"
    assert exc_info.value.reason["finish_reason"] == "length"
    assert exc_info.value.reason["empty_final"] is (content is None)
    assert "hidden chain of thought" not in repr(exc_info.value.reason)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [asyncio.TimeoutError(), _StatusError(429), _StatusError(503)],
)
async def test_complete_retries_retryable_failures(error: Exception) -> None:
    completions = _FakeCompletions([error, _response()])
    provider = DeepSeekProvider(api_key="test")
    provider._client = _client(completions)

    completion = await provider.complete(
        [Message(role="user", content="question")],
        _config(),
    )

    assert completion.text == "visible"
    assert len(completions.calls) == 2


@pytest.mark.asyncio
async def test_complete_bounds_retries_at_three() -> None:
    completions = _FakeCompletions([_StatusError(503) for _ in range(4)])
    provider = DeepSeekProvider(api_key="test")
    provider._client = _client(completions)

    with pytest.raises(_StatusError):
        await provider.complete(
            [Message(role="user", content="question")],
            _config(max_retries=99),
        )

    assert len(completions.calls) == 4


@pytest.mark.asyncio
async def test_complete_fails_closed_without_retry_for_client_error() -> None:
    completions = _FakeCompletions([_StatusError(400), _response()])
    provider = DeepSeekProvider(api_key="test")
    provider._client = _client(completions)

    with pytest.raises(_StatusError):
        await provider.complete(
            [Message(role="user", content="question")],
            _config(),
        )

    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_stream_ignores_reasoning_deltas_and_uses_provider_finish_reason() -> None:
    async def chunks() -> Any:
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="hidden stream reasoning",
                    ),
                    finish_reason=None,
                )
            ]
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="answer"),
                    finish_reason="length",
                )
            ]
        )

    completions = _FakeCompletions([chunks()])
    provider = DeepSeekProvider(api_key="test")
    provider._client = _client(completions)

    deltas = []
    with pytest.raises(LLMCompletionError) as exc_info:
        async for delta in provider.stream(
            [Message(role="user", content="question")],
            _config(),
        ):
            deltas.append(delta)

    assert [delta.text for delta in deltas] == ["answer"]
    assert exc_info.value.reason["code"] == "output_truncated"
    assert exc_info.value.reason["finish_reason"] == "length"
    assert exc_info.value.reason["empty_final"] is False
    assert "hidden stream reasoning" not in repr(exc_info.value.reason)


@pytest.mark.asyncio
async def test_stream_fails_closed_when_only_reasoning_is_returned() -> None:
    async def chunks() -> Any:
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="hidden stream reasoning",
                    ),
                    finish_reason="stop",
                )
            ]
        )

    completions = _FakeCompletions([chunks()])
    provider = DeepSeekProvider(api_key="test")
    provider._client = _client(completions)

    with pytest.raises(LLMCompletionError) as exc_info:
        async for _delta in provider.stream(
            [Message(role="user", content="question")],
            _config(),
        ):
            pass

    assert exc_info.value.reason == {
        "code": "empty_final_content",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "finish_reason": "stop",
        "empty_final": True,
    }
    assert "hidden stream reasoning" not in repr(exc_info.value)


@pytest.mark.asyncio
async def test_openai_compatible_defaults_do_not_enable_deepseek_extensions() -> None:
    completions = _FakeCompletions([_response()])
    provider = OpenAIProvider(api_key="test")
    provider._client = _client(completions)

    completion = await provider.complete(
        [Message(role="user", content="question")],
        LLMConfig(provider="openai", model="gpt-test", max_retries=0),
    )

    assert completion.text == "visible"
    request = completions.calls[0]
    assert "reasoning_effort" not in request
    assert "extra_body" not in request
