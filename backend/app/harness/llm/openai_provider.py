"""OpenAI-compatible provider (used for openai/qwen/local-vllm/custom)."""
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


class _OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        provider_name: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(f"{provider_name or 'openai'} provider requires API key")
        self._api_key = api_key
        self._base_url = base_url
        if provider_name:
            self.name = provider_name
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def complete(
        self, messages: list[Message], config: LLMConfig
    ) -> Completion:
        client = self._get_client()
        resp = await client.chat.completions.create(
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        choice = resp.choices[0].message.content or ""
        return Completion(
            text=choice,
            provider=self.name,
            model=config.model,
            is_mock=False,
        )

    async def stream(
        self, messages: list[Message], config: LLMConfig
    ) -> AsyncIterator[Delta]:
        client = self._get_client()
        stream = await client.chat.completions.create(
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            stream=True,
        )
        async for chunk in stream:
            piece = chunk.choices[0].delta.content if chunk.choices else None
            if piece:
                yield Delta(text=piece)
        yield Delta(text="", finish_reason="stop")


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
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, provider_name="deepseek")
