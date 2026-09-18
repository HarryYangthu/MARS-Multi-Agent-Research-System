"""Provider-neutral bounded retry transport; never retries parsing failures."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

import httpx

from app.harness.llm.provider_base import LLMConfig, MAX_LLM_RETRIES

T = TypeVar("T")


async def request_with_retries(action: Callable[[], Awaitable[T]], config: LLMConfig) -> T:
    attempts = min(max(config.max_retries, 0), MAX_LLM_RETRIES) + 1
    for index in range(attempts):
        if config.attempt_observer:
            config.attempt_observer("sdk_attempt_started", {"attempt": index + 1})
        try:
            result = await action()
        except asyncio.CancelledError:
            _record_failure(config, index, "CancelledError", retryable=False)
            raise
        except Exception as exc:
            retryable = _retryable(exc)
            _record_failure(config, index, type(exc).__name__, retryable=retryable, status=_status(exc))
            if index + 1 == attempts or not retryable:
                raise
            await asyncio.sleep(max(0, config.retry_base_delay_seconds) * 2**index)
        else:
            if config.attempt_observer:
                config.attempt_observer("sdk_attempt_succeeded", {"attempt": index + 1})
            return result
    raise RuntimeError("unreachable retry state")


async def stream_with_retries(action: Callable[[], AsyncIterator[T]], config: LLMConfig) -> AsyncIterator[T]:
    """Retry only before a visible delta; never replay already-delivered output."""
    attempts = min(max(config.max_retries, 0), MAX_LLM_RETRIES) + 1
    for index in range(attempts):
        emitted = False
        if config.attempt_observer:
            config.attempt_observer("sdk_attempt_started", {"attempt": index + 1})
        try:
            async for item in action():
                emitted = True
                yield item
        except asyncio.CancelledError:
            _record_failure(config, index, "CancelledError", retryable=False)
            raise
        except Exception as exc:
            retryable = not emitted and _retryable(exc)
            _record_failure(config, index, type(exc).__name__, retryable=retryable, status=_status(exc))
            if index + 1 == attempts or not retryable:
                raise
            await asyncio.sleep(max(0, config.retry_base_delay_seconds) * 2**index)
        else:
            if config.attempt_observer:
                config.attempt_observer("sdk_attempt_succeeded", {"attempt": index + 1})
            return


def _status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None and isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    return status if isinstance(status, int) else None


def _retryable(exc: Exception) -> bool:
    status = _status(exc)
    return (isinstance(exc, (httpx.TransportError, TimeoutError))
            or type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}
            or status in {408, 409, 429} or status is not None and 500 <= status < 600)


def _record_failure(config: LLMConfig, index: int, error: str, *, retryable: bool, status: int | None = None) -> None:
    if config.attempt_observer:
        payload: dict[str, Any] = {"attempt": index + 1, "error": error, "retryable": retryable,
                                   "details": {"status_code": status}}
        config.attempt_observer("sdk_attempt_failed", payload)
