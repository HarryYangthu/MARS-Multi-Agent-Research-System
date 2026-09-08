"""OpenAI-compatible provider (used for openai/qwen/local-vllm/custom)."""
from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
    ToolCall,
)


_T = TypeVar("_T")


@dataclass
class VisibleStreamAccumulator:
    """Pure public-output parser; reasoning fields are never copied or retained."""

    pieces: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    fingerprint: str | None = None
    chunks: int = 0
    visible_chars: int = 0

    def add(self, chunk: Any) -> None:
        self.chunks += 1
        usage = _usage_payload(getattr(chunk, "usage", None))
        if usage is not None:
            self.usage = usage
        fingerprint = getattr(chunk, "system_fingerprint", None)
        if fingerprint:
            self.fingerprint = str(fingerprint)
        if not chunk.choices:
            return
        choice = chunk.choices[0]
        piece = str(choice.delta.content or "")
        if piece:
            self.pieces.append(piece)
            self.visible_chars += len(piece)
        reason = getattr(choice, "finish_reason", None)
        if reason is not None:
            self.finish_reason = str(reason)

    def completion(self, *, provider: str, model: str) -> Completion:
        text = "".join(self.pieces)
        code = ("output_truncated" if self.finish_reason == "length" else
                "incomplete_stream" if self.finish_reason != "stop" else
                "empty_final_content" if not text.strip() else "")
        if code:
            raise LLMCompletionError(code=code, provider=provider, model=model,
                                     finish_reason=self.finish_reason, empty_final=not bool(text.strip()), usage=self.usage)
        return Completion(text=text, provider=provider, model=model,
                          raw={"usage": self.usage, "finish_reason": self.finish_reason,
                               "system_fingerprint": self.fingerprint, "streamed": True, "stream_chunks": self.chunks})


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

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

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
                message.to_wire()
                for message in messages
            ],
            "timeout": config.request_timeout_seconds,
        }
        if not (self.name == "deepseek" and thinking_enabled):
            kwargs["temperature"] = config.temperature
            kwargs["top_p"] = config.top_p
        if config.tools:
            if self.name == "zhipu":
                raise ValueError("native tool streaming is not implemented for zhipu")
            if thinking_enabled:
                raise ValueError("native tools currently require explicit non-thinking mode")
            kwargs["tools"] = list(config.tools)
            kwargs["parallel_tool_calls"] = False
        if config.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if stream:
            kwargs["stream"] = True

        reasoning_effort = config.reasoning_effort or self._default_reasoning_effort
        if self.name == "deepseek" and thinking_enabled is False and config.extra.get("review_format_repair") is True:
            # Host-only format repair marker; never forward it or inherit the
            # provider's reasoning default for this explicitly non-thinking call.
            reasoning_effort = None
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        if self.name == "zhipu":
            forced = config.model.lower().startswith("glm-5.3")
            if forced and config.thinking_enabled is False:
                raise ValueError("GLM-5.3 requires thinking enabled; use reasoning_effort=low")
            if forced and reasoning_effort not in {None, "low", "high", "max"}:
                raise ValueError("GLM-5.3 reasoning_effort must be low, high or max")
            if forced or config.thinking_enabled is not None:
                kwargs["extra_body"] = {"thinking": {"type": "enabled" if forced or thinking_enabled else "disabled"}}
        elif self.name == "deepseek" and thinking_enabled is False:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        elif thinking_enabled:
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
            if config.attempt_observer:
                config.attempt_observer("sdk_attempt_started", {"attempt": attempt + 1})
            try:
                result = await operation()
                if config.attempt_observer:
                    config.attempt_observer("sdk_attempt_succeeded", {"attempt": attempt + 1})
                return result
            except Exception as exc:
                details = public_error_details(exc)
                if config.attempt_observer:
                    config.attempt_observer("sdk_attempt_failed", {"attempt": attempt + 1,
                                                                  "error": _safe_error_label(exc), "details": details})
                if attempt >= max_retries or not _is_retryable_error(exc, provider_name=self.name):
                    logger.error("LLM request stopped provider={} model={} reason={}",
                                 self.name, config.model, _safe_error_label(exc))
                    raise
                delay = base_delay * (2**attempt)
                if details.get("retry_after_seconds", 0) > delay:
                    # Do not retry earlier than the provider requested or extend
                    # the caller's configured backoff/deadline behind its back.
                    if config.attempt_observer:
                        config.attempt_observer("sdk_retry_deferred", {
                            "reason": "provider Retry-After exceeds this call's backoff budget",
                            "retry_after_seconds": details["retry_after_seconds"],
                        })
                    logger.error("LLM retry deferred provider={} model={} retry_after_seconds={}",
                                 self.name, config.model, details["retry_after_seconds"])
                    raise
                if config.attempt_observer:
                    config.attempt_observer("sdk_retry_scheduled", {"next_attempt": attempt + 2, "delay": delay})
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
        return self._completion_from_response(resp, config)

    def _completion_from_response(self, resp: Any, config: LLMConfig) -> Completion:
        """Pure SDK-envelope parsing; it performs no model/service execution."""
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
        calls = tuple(ToolCall(id=call.id, name=call.function.name,
                               arguments=call.function.arguments)
                      for call in (getattr(message, "tool_calls", None) or ()))
        if calls and not config.tools:
            raise ValueError("unsolicited tool calls without configured tools")
        self._check_final(config, finish_reason, bool(text.strip()) or bool(calls),
                          usage=_usage_payload(getattr(resp, "usage", None)))
        return Completion(
            text=text,
            tool_calls=calls,
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

    def _check_final(self, config: LLMConfig, finish_reason: str | None, visible_content_seen: bool,
                     *, usage: dict[str, Any] | None = None) -> None:
        if finish_reason == "length" or not visible_content_seen:
            raise LLMCompletionError(
                code="output_truncated" if finish_reason == "length" else "empty_final_content",
                provider=self.name, model=config.model, finish_reason=finish_reason,
                empty_final=not visible_content_seen, usage=usage,
            )

    @staticmethod
    def _visible_stream_delta(chunk: Any) -> tuple[str, str | None]:
        if not chunk.choices:
            return "", None
        choice = chunk.choices[0]
        return str(choice.delta.content or ""), _optional_string(getattr(choice, "finish_reason", None))

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
            piece, chunk_finish_reason = self._visible_stream_delta(chunk)
            if chunk_finish_reason is not None:
                finish_reason = chunk_finish_reason
            if piece:
                visible_content_seen = visible_content_seen or bool(piece.strip())
                yield Delta(text=piece)
        self._check_final(config, finish_reason, visible_content_seen)
        yield Delta(text="", finish_reason=finish_reason or "stop")


class ZhipuProvider(_OpenAICompatProvider):
    def __init__(self, *, api_key: str, base_url: str = "https://open.bigmodel.cn/api/paas/v4") -> None:
        super().__init__(api_key=api_key, base_url=base_url, provider_name="zhipu")

    async def complete(self, messages: list[Message], config: LLMConfig) -> Completion:
        """Consume real SSE to avoid waiting for a complete long response at a gateway.

        Each retry owns a fresh accumulator. Partial content from an unsuccessful
        attempt can never be concatenated with a subsequent attempt's answer.
        """
        client = self._get_client()
        kwargs = self._request_kwargs(messages, config, stream=True)

        async def consume() -> Completion:
            state = VisibleStreamAccumulator()
            stream = await client.chat.completions.create(**kwargs)
            async with stream:
                async for chunk in stream:
                    state.add(chunk)
                    if config.attempt_observer and (state.chunks == 1 or state.chunks % 128 == 0):
                        config.attempt_observer("sdk_stream_progress", {
                            "chunks": state.chunks, "visible_chars": state.visible_chars,
                        })
            return state.completion(provider=self.name, model=config.model)

        return await self._request_with_retries(consume, config=config)


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


def retry_after_seconds(value: object, *, now: datetime | None = None) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                return None
            seconds = (target - (now or datetime.now(timezone.utc))).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def public_error_details(exc: Exception) -> dict[str, Any]:
    """Only bounded codes and Retry-After; never response messages, bodies or auth headers."""
    details: dict[str, Any] = {"exception_type": type(exc).__name__}
    status = _status_code(exc)
    if status is not None:
        details["http_status"] = status
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, Mapping) else {}
    code = error.get("code") if isinstance(error, Mapping) else None
    if code is None:
        code = getattr(exc, "code", None)
    if type(code) in {str, int} and re.fullmatch(r"(?:[0-9]{1,8}|[a-z][a-z0-9_]{0,63})", str(code)):
        details["api_error_code"] = str(code)
    headers = getattr(getattr(exc, "response", None), "headers", None)
    delay = retry_after_seconds(headers.get("retry-after")) if isinstance(headers, Mapping) else None
    if delay is not None:
        details["retry_after_seconds"] = delay
    return details


def _is_retryable_error(exc: Exception, *, provider_name: str = "") -> bool:
    code = public_error_details(exc).get("api_error_code")
    if code in {"insufficient_quota", "billing_hard_limit_reached"}:
        return False
    # Official BigModel account/quota/access errors also use HTTP 429.
    if provider_name == "zhipu" and code in {
        "1113", "1304", "1308", "1309", "1310", "1311", "1313", "1314", "1315",
        "1316", "1317", "1318", "1319", "1320", "1321",
    }:
        return False
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
    code = public_error_details(exc).get("api_error_code")
    suffix = f":code={code}" if code is not None else ""
    if status_code is not None:
        return f"{type(exc).__name__}:status={status_code}{suffix}"
    return type(exc).__name__
