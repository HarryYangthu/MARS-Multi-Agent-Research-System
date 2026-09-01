"""LLM provider abstraction.

Every provider implements ``complete()`` (one-shot) and ``stream()`` (delta
iterator). The same interface is used by real APIs, by ``mock_provider``,
and by ``local_vllm_provider`` (OpenAI-compatible).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["system", "user", "assistant"]
ReasoningEffort = Literal["low", "medium", "high", "max"]
MAX_LLM_RETRIES = 3
LLM_CALL_DEADLINE_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass
class LLMConfig:
    provider: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0
    response_schema: str | None = None  # informs mock_provider what to fake
    thinking_enabled: bool | None = None
    reasoning_effort: ReasoningEffort | None = None
    request_timeout_seconds: float = 120.0
    max_retries: int = 3
    retry_base_delay_seconds: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    is_mock: bool = False
    debate_role: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class LLMCompletionError(RuntimeError):
    """Structured, content-free reason for an unusable provider completion."""

    def __init__(
        self,
        *,
        code: str,
        provider: str,
        model: str,
        finish_reason: str | None,
        empty_final: bool,
    ) -> None:
        self.reason: dict[str, str | bool | None] = {
            "code": code,
            "provider": provider,
            "model": model,
            "finish_reason": finish_reason,
            "empty_final": empty_final,
        }
        super().__init__(
            f"{code}: provider={provider} model={model} "
            f"finish_reason={finish_reason or 'unknown'} empty_final={empty_final}"
        )


@dataclass
class Delta:
    text: str
    finish_reason: str | None = None


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def complete(
        self, messages: list[Message], config: LLMConfig
    ) -> Completion: ...

    @abstractmethod
    def stream(
        self, messages: list[Message], config: LLMConfig
    ) -> AsyncIterator[Delta]:
        """Subclasses implement as ``async def`` with ``yield`` (async generator).

        The non-async signature here is required by mypy: an async generator
        function's declared return type is ``AsyncIterator[T]``, not
        ``Coroutine[..., AsyncIterator[T]]``.
        """
        ...


def llm_call_deadline_seconds(
    config: LLMConfig,
    *,
    minimum_seconds: float = 0.0,
) -> float:
    """Outer deadline that cannot preempt configured attempts/backoff."""

    max_retries = min(max(config.max_retries, 0), MAX_LLM_RETRIES)
    request_timeout = max(config.request_timeout_seconds, 0.0)
    base_delay = max(config.retry_base_delay_seconds, 0.0)
    backoff_seconds = sum(base_delay * (2**attempt) for attempt in range(max_retries))
    provider_budget = (
        request_timeout * (max_retries + 1)
        + backoff_seconds
        + LLM_CALL_DEADLINE_GRACE_SECONDS
    )
    return float(max(minimum_seconds, provider_budget))
