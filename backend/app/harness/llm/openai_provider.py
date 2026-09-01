"""OpenAI-compatible provider (used for openai/qwen/local-vllm/custom)."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, TypeVar

from loguru import logger

from app.harness.llm.provider_base import (
    Completion,
    Delta,
    LLMConfig,
    LLMCompletionError,
    LLMProvider,
    MAX_LLM_RETRIES,
    Message,
    ReasoningEffort,
)


_T = TypeVar("_T")


class _OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        provider_name: str | None = None,
        default_thinking_enabled: bool = False,
        default_reasoning_effort: ReasoningEffort | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(f"{provider_name or 'openai'} provider requires API key")
        self._api_key = api_key
        self._base_url = base_url
        if provider_name:
            self.name = provider_name
        self._default_thinking_enabled = default_thinking_enabled
        self._default_reasoning_effort = default_reasoning_effort
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            # The SDK retries twice by default. Disable those retries so the
            # bounded policy below is the single source of retry behaviour.
            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "max_retries": 0,
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _request_kwargs(
        self,
        messages: list[Message],
        config: LLMConfig,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        thinking_enabled = config.thinking_enabled
        if thinking_enabled is None:
            thinking_enabled = self._default_thinking_enabled
        kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "timeout": config.request_timeout_seconds,
        }
        if not (self.name == "deepseek" and thinking_enabled):
            kwargs["temperature"] = config.temperature
            kwargs["top_p"] = config.top_p
        if stream:
            kwargs["stream"] = True

        reasoning_effort = config.reasoning_effort or self._default_reasoning_effort
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        if thinking_enabled:
            # DeepSeek exposes thinking mode as an OpenAI-compatible extension.
            # Keep it in extra_body so other compatible endpoints are unchanged
            # unless their own config explicitly enables it.
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        return kwargs

    async def _request_with_retries(
        self,
        operation: Callable[[], Awaitable[_T]],
        *,
        config: LLMConfig,
    ) -> _T:
        max_retries = min(max(config.max_retries, 0), MAX_LLM_RETRIES)
        base_delay = max(config.retry_base_delay_seconds, 0.0)
        for attempt in range(max_retries + 1):
            try:
                return await operation()
            except Exception as exc:
                if attempt >= max_retries or not _is_retryable_error(exc):
                    raise
                delay = base_delay * (2**attempt)
                logger.warning(
                    "LLM request retry {}/{} provider={} model={} reason={} "
                    "delay_seconds={}",
                    attempt + 1,
                    max_retries,
                    self.name,
                    config.model,
                    _safe_error_label(exc),
                    delay,
                )
                if delay:
                    await asyncio.sleep(delay)
        raise RuntimeError("unreachable retry loop")

    async def complete(
        self, messages: list[Message], config: LLMConfig
    ) -> Completion:
        client = self._get_client()
        request_kwargs = self._request_kwargs(messages, config)
        resp = await self._request_with_retries(
            lambda: client.chat.completions.create(**request_kwargs),
            config=config,
        )
        if not resp.choices:
            raise RuntimeError(f"{self.name} returned no completion choices")
        response_choice = resp.choices[0]
        message = response_choice.message
        # Never surface or persist reasoning_content. It may contain hidden
        # chain-of-thought and is not the provider's final answer.
        text = str(message.content or "")
        finish_reason = _optional_string(
            getattr(response_choice, "finish_reason", None)
        )
        if finish_reason == "length":
            raise LLMCompletionError(
                code="output_truncated",
                provider=self.name,
                model=config.model,
                finish_reason=finish_reason,
                empty_final=not bool(text.strip()),
            )
        if not text.strip():
            raise LLMCompletionError(
                code="empty_final_content",
                provider=self.name,
                model=config.model,
                finish_reason=finish_reason,
                empty_final=True,
            )
        return Completion(
            text=text,
            provider=self.name,
            model=config.model,
            is_mock=False,
            raw={
                "usage": _usage_payload(getattr(resp, "usage", None)),
                "system_fingerprint": _optional_string(
                    getattr(resp, "system_fingerprint", None)
                ),
                "finish_reason": finish_reason,
            },
        )

    async def stream(
        self, messages: list[Message], config: LLMConfig
    ) -> AsyncIterator[Delta]:
        client = self._get_client()
        request_kwargs = self._request_kwargs(messages, config, stream=True)
        stream = await self._request_with_retries(
            lambda: client.chat.completions.create(**request_kwargs),
            config=config,
        )
        finish_reason: str | None = None
        visible_content_seen = False
        async for chunk in stream:
            if not chunk.choices:
                continue
            response_choice = chunk.choices[0]
            chunk_finish_reason = _optional_string(
                getattr(response_choice, "finish_reason", None)
            )
            if chunk_finish_reason is not None:
                finish_reason = chunk_finish_reason
            delta = response_choice.delta
            piece = str(delta.content or "")
            if piece:
                visible_content_seen = visible_content_seen or bool(piece.strip())
                yield Delta(text=piece)
        if finish_reason == "length":
            raise LLMCompletionError(
                code="output_truncated",
                provider=self.name,
                model=config.model,
                finish_reason=finish_reason,
                empty_final=not visible_content_seen,
            )
        if not visible_content_seen:
            raise LLMCompletionError(
                code="empty_final_content",
                provider=self.name,
                model=config.model,
                finish_reason=finish_reason,
                empty_final=True,
            )
        yield Delta(text="", finish_reason=finish_reason or "stop")


class OpenAIProvider(_OpenAICompatProvider):
    def __init__(self, *, api_key: str) -> None:
        super().__init__(api_key=api_key, provider_name="openai")


class QwenProvider(_OpenAICompatProvider):
    """Qwen via DashScope's OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, provider_name="qwen")


class LocalVllmProvider(_OpenAICompatProvider):
    """Local vLLM serve (OpenAI-compatible, optional API key)."""

    def __init__(self, *, base_url: str, api_key: str = "EMPTY") -> None:
        super().__init__(
            api_key=api_key or "EMPTY",
            base_url=base_url,
            provider_name="local_vllm",
        )


class CustomEndpointProvider(_OpenAICompatProvider):
    def __init__(self, *, api_key: str, base_url: str) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider_name="custom",
        )


class DeepSeekProvider(_OpenAICompatProvider):
    """DeepSeek via its OpenAI-compatible endpoint.

    Default base URL is ``https://api.deepseek.com/v1``. Models follow the
    DeepSeek catalogue (``deepseek-chat``, ``deepseek-reasoner``, ...).
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        default_thinking_enabled: bool = False,
        default_reasoning_effort: ReasoningEffort | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider_name="deepseek",
            default_thinking_enabled=default_thinking_enabled,
            default_reasoning_effort=default_reasoning_effort,
        )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _usage_payload(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    model_dump = getattr(usage, "model_dump", None)
    if callable(model_dump):
        dumped_usage = model_dump(exclude_none=True)
        if isinstance(dumped_usage, Mapping):
            return {str(key): value for key, value in dumped_usage.items()}
    if isinstance(usage, Mapping):
        return {str(key): value for key, value in usage.items()}

    usage_fields: dict[str, Any] = {}
    for name in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_tokens_details",
        "completion_tokens_details",
    ):
        value = getattr(usage, name, None)
        if value is not None:
            usage_fields[name] = value
    return usage_fields or None


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True

    status_code = _status_code(exc)
    if status_code == 429 or (status_code is not None and status_code >= 500):
        return True

    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return True
    except ImportError:
        pass

    try:
        import openai

        retryable_types = (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
        )
        if isinstance(exc, retryable_types):
            return True
    except ImportError:
        pass
    return False


def _safe_error_label(exc: Exception) -> str:
    status_code = _status_code(exc)
    if status_code is not None:
        return f"{type(exc).__name__}:status={status_code}"
    return type(exc).__name__
