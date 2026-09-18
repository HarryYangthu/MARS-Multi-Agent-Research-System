"""Anthropic provider (real)."""
from __future__ import annotations

from collections.abc import AsyncIterator
import inspect
import json
import os
from typing import Any

from app.harness.llm.provider_base import (
    Completion,
    Delta,
    LLMConfig,
    LLMProvider,
    Message,
    ToolCall,
    LLMCompletionError,
    public_endpoint_url,
)
from app.harness.llm.retry import request_with_retries, stream_with_retries


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for AnthropicProvider")
        self._api_key = api_key
        self._base_url = base_url if base_url is not None else (
            os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com")
        self._client: Any = None

    @property
    def base_url(self) -> str:
        endpoint = str(self._client.base_url) if self._client is not None else self._base_url
        return public_endpoint_url(endpoint)

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=self._api_key, max_retries=0, base_url=self._base_url)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        system = ""
        chat: list[dict[str, Any]] = []
        pending: set[str] = set()
        seen: set[str] = set()
        for m in messages:
            m.to_wire()  # Enforce the same role contract as OpenAI messages.
            if m.role == "system":
                system = (system + "\n" + m.content).strip() if system else m.content
                continue
            elif m.role == "tool":
                if not m.tool_call_id or m.tool_call_id not in pending:
                    raise ValueError("Anthropic tool result has no unresolved preceding call")
                pending.remove(m.tool_call_id)
                role = "user"
                blocks: list[dict[str, Any]] = [{"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}]
            else:
                if pending:
                    raise ValueError("Anthropic tool results must immediately follow their calls")
                role = m.role
                blocks = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for call in m.tool_calls:
                    if not call.id or not call.name or call.id in seen:
                        raise ValueError("Anthropic tool calls require unique nonempty IDs and names")
                    args = json.loads(call.arguments)
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be an object")
                    json.dumps(args, allow_nan=False)
                    pending.add(call.id)
                    seen.add(call.id)
                    blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": args})
                if not blocks:
                    raise ValueError("Anthropic chat messages require text or tool calls")
            if chat and chat[-1]["role"] == role:
                chat[-1]["content"].extend(blocks)
            else:
                chat.append({"role": role, "content": blocks})
        if pending:
            raise ValueError("Anthropic tool calls are missing results")
        return system, chat

    def _request_kwargs(self, messages: list[Message], config: LLMConfig, *, stream: bool = False) -> dict[str, Any]:
        if config.thinking_enabled:
            raise ValueError("Anthropic thinking blocks are not supported by this public-observation adapter; use thinking_enabled=false")
        system, chat = self._split_system(messages)
        if config.json_mode:
            system += "\nReturn one complete valid JSON object without Markdown fences."
        kwargs: dict[str, Any] = {"model": config.model, "max_tokens": config.max_tokens,
            "system": system or "You are a helpful assistant.",
            "messages": chat, "timeout": config.request_timeout_seconds}
        from anthropic.resources.messages import AsyncMessages

        method = AsyncMessages.stream if stream else AsyncMessages.create
        if "temperature" in inspect.signature(method).parameters:
            kwargs["temperature"] = config.temperature
        elif config.temperature != LLMConfig(provider=self.name, model=config.model).temperature:
            raise ValueError("installed Anthropic SDK does not support custom temperature; use the default configuration")
        if config.tools:
            kwargs["tools"] = [{"name": spec["function"]["name"],
                "description": spec["function"].get("description", ""),
                "input_schema": spec["function"]["parameters"]} for spec in config.tools]
        return kwargs

    def _completion_from_response(self, response: Any, config: LLMConfig) -> Completion:
        text = "".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", "") == "text")
        calls = tuple(ToolCall(str(block.id), str(block.name), json.dumps(block.input, ensure_ascii=False))
                      for block in response.content if getattr(block, "type", "") == "tool_use")
        raw_usage = response.usage.model_dump(exclude_none=True) if response.usage is not None else {}
        usage = self._usage(raw_usage)
        finish = response.stop_reason
        if finish not in {"end_turn", "stop_sequence", "tool_use"} or not (text.strip() or calls):
            raise LLMCompletionError(code="output_truncated" if finish == "max_tokens" else "unusable_completion",
                provider=self.name, model=config.model, finish_reason=finish,
                empty_final=not bool(text.strip() or calls), usage=usage)
        return Completion(text=text, provider=self.name, model=config.model, tool_calls=calls,
                          raw={"usage": usage, "provider_usage": raw_usage, "finish_reason": finish})

    @staticmethod
    def _usage(raw: dict[str, Any]) -> dict[str, int] | None:
        keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        values: list[int] = []
        for key in keys:
            value = raw.get(key, 0) if key.startswith("cache_") else raw.get(key)
            if type(value) is not int or value < 0:
                return None
            values.append(value)
        prompt = values[0] + values[2] + values[3]
        completion = values[1]
        return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}

    async def complete(
        self, messages: list[Message], config: LLMConfig
    ) -> Completion:
        kwargs = self._request_kwargs(messages, config)
        client = self._get_client()
        response = await request_with_retries(lambda: client.messages.create(**kwargs), config)
        return self._completion_from_response(response, config)

    async def stream(
        self, messages: list[Message], config: LLMConfig
    ) -> AsyncIterator[Delta]:
        if config.tools:
            raise ValueError("stream() is text-only; use complete() for native tool calls")
        kwargs = self._request_kwargs(messages, config, stream=True)
        client = self._get_client()

        async def attempt() -> AsyncIterator[Delta]:
            async with client.messages.stream(**kwargs) as stream:
                async for text_chunk in stream.text_stream:
                    yield Delta(text=text_chunk)
                result = self._completion_from_response(await stream.get_final_message(), config)
            yield Delta(text="", finish_reason="stop", usage=result.raw.get("usage"))

        async for delta in stream_with_retries(attempt, config):
            yield delta
