"""Chat response assembly shared by streaming calls, gateway probes and non-streaming SSE compatibility."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Iterable

import httpx
import litellm
from litellm.llms.custom_llm import CustomLLM


def parse_body(text: str) -> dict[str, Any]:
    body = text.strip()
    if not body.startswith(("data:", "event:", ":")):
        data = json.loads(body)
        return data["data"] if "choices" not in data and isinstance(data.get("data"), dict) else data
    payloads = (line.strip()[5:].strip() for line in body.splitlines() if line.strip().startswith("data:"))
    return assemble(json.loads(payload) for payload in payloads if payload and payload != "[DONE]")


def assemble(chunks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    content, reasoning, calls, usage, finish = [], [], {}, None, None
    for chunk in chunks:
        if chunk.get("error"):
            raise RuntimeError(f"upstream stream error: {chunk['error']}")
        usage = chunk.get("usage") or usage
        for choice in chunk.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or choice.get("message") or {}
            content.append(delta.get("content") or "")
            reasoning.append(delta.get("reasoning_content") or delta.get("reasoning") or "")
            for call in delta.get("tool_calls") or []:
                slot = calls.setdefault(call.get("index", len(calls)), {"id": None, "type": "function",
                                                                         "function": {"name": "", "arguments": ""}})
                slot["id"] = call.get("id") or slot["id"]
                slot["function"]["name"] += (call.get("function") or {}).get("name") or ""
                slot["function"]["arguments"] += (call.get("function") or {}).get("arguments") or ""
            finish = choice.get("finish_reason") or finish
    if not finish:
        raise ValueError("incomplete stream: no finish_reason (empty response or connection ended early)")
    message = {"role": "assistant", "content": "".join(content), "reasoning_content": "".join(reasoning) or None}
    if calls: message["tool_calls"] = [calls[i] for i in sorted(calls)]
    return {"choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": usage}


def read_stream(chunks: Iterable[Any], timeout: float, started: float) -> tuple[dict[str, Any], float | None, int]:
    first, count = None, 0
    def record(chunk: Any) -> dict[str, Any]:
        nonlocal first, count
        elapsed = time.monotonic() - started
        if elapsed > timeout:
            raise TimeoutError(f"exceeded total budget {timeout}s")
        row = chunk if isinstance(chunk, dict) else chunk.model_dump(exclude_none=True)
        delta = (row.get("choices") or [{}])[0].get("delta") or {}
        if first is None and any(delta.get(key) for key in ("content", "reasoning_content", "reasoning", "tool_calls")):
            first = round(elapsed, 1)
        count += 1
        return row
    return assemble(map(record, chunks)), first, count


def complete(api_base: str, api_key: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = {**{k: v for k, v in body.items() if k not in ("stream", "stream_options", "extra_body")},
            **(body.get("extra_body") or {}), "stream": False}
    response = httpx.post(api_base.rstrip("/") + "/chat/completions", json=body, timeout=timeout,
                          headers={"Authorization": f"Bearer {api_key}"})
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
    return parse_body(response.text)


class NoStream(CustomLLM):
    async def acompletion(self, *, model: str, messages: list, api_base: str, api_key: str, optional_params: dict,
                          timeout: Any = None, **_: Any) -> Any:
        data = await asyncio.to_thread(complete, api_base, api_key, {"model": model, "messages": messages, **optional_params},
                                       float(timeout or 600))
        return litellm.ModelResponse(**data)

    async def astreaming(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        response = await self.acompletion(**kwargs)
        choice, usage = response.choices[0], response.usage
        if choice.message.content:
            yield {"text": choice.message.content, "tool_use": None, "is_finished": False, "finish_reason": "", "usage": None}
        for index, call in enumerate(choice.message.tool_calls or []):
            yield {"text": "", "is_finished": False, "finish_reason": "", "usage": None,
                   "tool_use": {"id": call.id, "type": "function", "index": index,
                                "function": {"name": call.function.name, "arguments": call.function.arguments}}}
        yield {"text": "", "tool_use": None, "is_finished": True, "finish_reason": choice.finish_reason or "stop",
               "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
                         "total_tokens": usage.total_tokens} if usage else None}


handler = NoStream()
