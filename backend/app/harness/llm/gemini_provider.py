"""Gemini provider (REST via httpx — light SDK, no extra dep).

V0 supports Google's Generative Language API REST endpoint with the
``v1beta/models/{model}:generateContent`` shape.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.harness.llm.provider_base import (
    Completion,
    Delta,
    LLMConfig,
    LLMProvider,
    Message,
)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY required")
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self._client

    @staticmethod
    def _format_contents(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue  # system handled via systemInstruction
            role = "user" if m.role == "user" else "model"
            out.append({"role": role, "parts": [{"text": m.content}]})
        return out

    @staticmethod
    def _system_instruction(messages: list[Message]) -> dict[str, Any] | None:
        sys = "\n".join(m.content for m in messages if m.role == "system").strip()
        if not sys:
            return None
        return {"parts": [{"text": sys}]}

    async def complete(
        self, messages: list[Message], config: LLMConfig
    ) -> Completion:
        client = self._get_client()
        url = f"{self._base}/models/{config.model}:generateContent"
        body: dict[str, Any] = {
            "contents": self._format_contents(messages),
            "generationConfig": {
                "temperature": config.temperature,
                "maxOutputTokens": config.max_tokens,
            },
        }
        sys_inst = self._system_instruction(messages)
        if sys_inst:
            body["systemInstruction"] = sys_inst
        r = await client.post(url, params={"key": self._api_key}, json=body)
        r.raise_for_status()
        data = r.json()
        text = ""
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                text += part.get("text", "")
        return Completion(text=text, provider="gemini", model=config.model)

    async def stream(
        self, messages: list[Message], config: LLMConfig
    ) -> AsyncIterator[Delta]:
        # V0 simplification: no SSE — yield the completion in one chunk.
        completion = await self.complete(messages, config)
        yield Delta(text=completion.text, finish_reason="stop")
