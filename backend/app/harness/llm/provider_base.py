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
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    is_mock: bool = False
    debate_role: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


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
