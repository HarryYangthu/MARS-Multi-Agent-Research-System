"""Anthropic provider (real)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.harness.llm.provider_base import (
    Completion,
    Delta,
    LLMConfig,
    LLMProvider,
    Message,
)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, api_key: str) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for AnthropicProvider")
        self._api_key = api_key
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str, list[dict[str, str]]]:
        system = ""
        chat: list[dict[str, str]] = []
        for m in messages:
            if m.role == "system":
                system = (system + "\n" + m.content).strip() if system else m.content
            else:
                chat.append({"role": m.role, "content": m.content})
        return system, chat

    async def complete(
        self, messages: list[Message], config: LLMConfig
    ) -> Completion:
        client = self._get_client()
        system, chat = self._split_system(messages)
        resp = await client.messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            system=system or "You are a helpful assistant.",
            messages=chat,
        )
        text_blocks = []
        for block in resp.content:
            text_attr = getattr(block, "text", None)
            if text_attr:
                text_blocks.append(text_attr)
        return Completion(
            text="".join(text_blocks),
            provider="anthropic",
            model=config.model,
            is_mock=False,
        )

    async def stream(
        self, messages: list[Message], config: LLMConfig
    ) -> AsyncIterator[Delta]:
        client = self._get_client()
        system, chat = self._split_system(messages)
        async with client.messages.stream(
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            system=system or "You are a helpful assistant.",
            messages=chat,
        ) as stream:
            async for text_chunk in stream.text_stream:
                yield Delta(text=text_chunk)
        yield Delta(text="", finish_reason="stop")
