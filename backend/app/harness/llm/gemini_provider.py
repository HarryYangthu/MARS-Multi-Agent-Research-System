"""Gemini REST adapter: explicit tool messages, usage, retries, and real SSE."""
from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any
import uuid

import httpx

from app.harness.llm.provider_base import Completion, Delta, LLMCompletionError, LLMConfig, LLMProvider, Message, ToolCall
from app.harness.llm.provider_base import public_endpoint_url
from app.harness.llm.retry import request_with_retries, stream_with_retries


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, *, api_key: str,
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta") -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY required")
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return public_endpoint_url(self._base)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _format_contents(messages: list[Message]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        call_names: dict[str, str] = {}
        pending: set[str] = set()
        for message in messages:
            message.to_wire()
            if message.role == "system":
                continue
            parts: list[dict[str, Any]] = []
            role = "model" if message.role == "assistant" else "user"
            if message.role == "tool":
                if not message.tool_call_id or message.tool_call_id not in pending:
                    raise ValueError("Gemini tool result has no preceding call")
                pending.remove(message.tool_call_id)
                try:
                    output = json.loads(message.content)
                except ValueError:
                    output = {"result": message.content}
                if not isinstance(output, dict):
                    output = {"result": output}
                parts.append({"functionResponse": {"id": message.tool_call_id,
                    "name": call_names[message.tool_call_id], "response": output}})
            else:
                if pending:
                    raise ValueError("Gemini tool results must immediately follow their calls")
                if message.content:
                    parts.append({"text": message.content})
            for call in message.tool_calls:
                if not call.id or not call.name or call.id in call_names:
                    raise ValueError("Gemini function calls require unique nonempty IDs and names")
                args = json.loads(call.arguments)
                if not isinstance(args, dict):
                    raise ValueError("Gemini function arguments must be an object")
                json.dumps(args, allow_nan=False)
                call_names[call.id] = call.name
                pending.add(call.id)
                parts.append({"functionCall": {"id": call.id, "name": call.name, "args": args}})
            if parts:
                if contents and contents[-1]["role"] == role:
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": role, "parts": parts})
        if pending:
            raise ValueError("Gemini function calls are missing results")
        return contents

    @staticmethod
    def _system_instruction(messages: list[Message]) -> dict[str, Any] | None:
        text = "\n".join(m.content for m in messages if m.role == "system").strip()
        return {"parts": [{"text": text}]} if text else None

    def _request_body(self, messages: list[Message], config: LLMConfig) -> dict[str, Any]:
        if config.tools and config.thinking_enabled is not False:
            raise ValueError("Gemini native tools require explicit thinking_enabled=false; thought-signature continuation is unsupported")
        if config.thinking_enabled:
            raise ValueError("Gemini reasoning controls are model-specific; use explicit non-thinking configuration in this adapter")
        generation: dict[str, Any] = {"temperature": config.temperature, "topP": config.top_p,
                                      "maxOutputTokens": config.max_tokens, "candidateCount": 1}
        if config.json_mode and not config.tools:
            generation["responseMimeType"] = "application/json"
        if config.thinking_enabled is False:
            generation["thinkingConfig"] = {"thinkingBudget": 0}
        body: dict[str, Any] = {"contents": self._format_contents(messages), "generationConfig": generation}
        system = self._system_instruction(messages)
        if system:
            body["systemInstruction"] = system
        if config.tools:
            body["tools"] = [{"functionDeclarations": [{"name": spec["function"]["name"],
                "description": spec["function"].get("description", ""),
                "parametersJsonSchema": spec["function"]["parameters"]} for spec in config.tools]}]
        return body

    @staticmethod
    def _usage(data: dict[str, Any]) -> dict[str, Any] | None:
        usage = data.get("usageMetadata")
        if not isinstance(usage, dict):
            return None
        result: dict[str, int] = {}
        for source, target in (("promptTokenCount", "prompt_tokens"), ("totalTokenCount", "total_tokens")):
            value = usage.get(source)
            if type(value) is int and value >= 0:
                result[target] = value
        candidate, thought = usage.get("candidatesTokenCount"), usage.get("thoughtsTokenCount", 0)
        if type(candidate) is int and candidate >= 0 and type(thought) is int and thought >= 0:
            result["completion_tokens"] = candidate + thought
        return result or None

    def _completion_from_response(self, data: dict[str, Any], config: LLMConfig) -> Completion:
        candidates = data.get("candidates", [])
        if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
            raise LLMCompletionError(code="unusable_completion", provider=self.name, model=config.model,
                finish_reason=None, empty_final=True, usage=self._usage(data))
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        content = candidate.get("content")
        parts = content.get("parts", []) if isinstance(content, dict) else []
        if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
            raise LLMCompletionError(code="invalid_response_parts", provider=self.name, model=config.model,
                                     finish_reason=finish, empty_final=True, usage=self._usage(data))
        text = "".join(part["text"] for part in parts if isinstance(part.get("text"), str) and not part.get("thought"))
        calls: list[ToolCall] = []
        for part in parts:
            if "functionCall" in part:
                if part.get("thoughtSignature"):
                    raise LLMCompletionError(code="unsupported_thought_signature", provider=self.name, model=config.model,
                                             finish_reason=finish, empty_final=not bool(text.strip()), usage=self._usage(data))
                function = part["functionCall"]
                if (not isinstance(function, dict) or not isinstance(function.get("name"), str)
                        or not function.get("name") or not isinstance(function.get("args", {}), dict)):
                    raise LLMCompletionError(code="invalid_function_call", provider=self.name, model=config.model,
                                             finish_reason=finish, empty_final=not bool(text.strip()), usage=self._usage(data))
                calls.append(ToolCall(str(function.get("id") or uuid.uuid4().hex), str(function["name"]),
                                      json.dumps(function.get("args", {}), ensure_ascii=False)))
        if finish != "STOP" or not (text.strip() or calls):
            raise LLMCompletionError(code="output_truncated" if finish == "MAX_TOKENS" else "unusable_completion",
                provider=self.name, model=config.model, finish_reason=finish,
                empty_final=not bool(text.strip() or calls), usage=self._usage(data))
        return Completion(text=text, provider=self.name, model=config.model, tool_calls=tuple(calls),
                          raw={"usage": self._usage(data), "finish_reason": finish,
                               "provider_usage": data.get("usageMetadata"),
                               "model_version": data.get("modelVersion")})

    async def complete(self, messages: list[Message], config: LLMConfig) -> Completion:
        body = self._request_body(messages, config)
        client = self._get_client()

        async def send() -> dict[str, Any]:
            response = await client.post(f"{self._base}/models/{config.model}:generateContent",
                headers={"x-goog-api-key": self._api_key}, json=body, timeout=config.request_timeout_seconds)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise LLMCompletionError(code="invalid_response", provider=self.name, model=config.model,
                                         finish_reason=None, empty_final=True)
            return result

        return self._completion_from_response(await request_with_retries(send, config), config)

    async def stream(self, messages: list[Message], config: LLMConfig) -> AsyncIterator[Delta]:
        if config.tools:
            raise ValueError("stream() is text-only; use complete() for native tool calls")
        body = self._request_body(messages, config)
        async for delta in stream_with_retries(lambda: self._stream_once(body, config), config):
            yield delta

    async def _stream_once(self, body: dict[str, Any], config: LLMConfig) -> AsyncIterator[Delta]:
        client = self._get_client()
        finish: str | None = None
        usage: dict[str, Any] | None = None
        visible = False
        async with client.stream("POST", f"{self._base}/models/{config.model}:streamGenerateContent",
            params={"alt": "sse"}, headers={"x-goog-api-key": self._api_key},
            json=body, timeout=config.request_timeout_seconds) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = json.loads(line[5:].strip())
                usage = self._usage(data) or usage
                for candidate in data.get("candidates", []):
                    finish = candidate.get("finishReason") or finish
                    for part in candidate.get("content", {}).get("parts", []):
                        if "functionCall" in part:
                            raise ValueError("unexpected tool call in text stream")
                        if part.get("text") and not part.get("thought"):
                            visible = True
                            yield Delta(text=part["text"])
        if finish != "STOP" or not visible:
            raise LLMCompletionError(code="output_truncated" if finish == "MAX_TOKENS" else "incomplete_stream",
                provider=self.name, model=config.model, finish_reason=finish, empty_final=not visible, usage=usage)
        yield Delta(text="", finish_reason="stop", usage=usage)
