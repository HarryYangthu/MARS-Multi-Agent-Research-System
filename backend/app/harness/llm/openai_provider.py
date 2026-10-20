"""OpenAI-compatible provider (used for openai/qwen/local-vllm/custom)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

from loguru import logger

from app.harness.llm.model_capabilities import ModelCompatibilityError, requires_glm_thinking, supports_glm_observation_tools
from app.harness.llm.request_evidence import record_provider_request
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
    public_endpoint_url,
)


_T = TypeVar("_T")


def _envelope_field(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


@dataclass
class ResponseModelIdentity:
    """Only bounded public model identifiers from actual SDK envelope fields."""

    models: set[str] = field(default_factory=set)
    missing_fields: int = 0
    invalid_fields: int = 0

    def add(self, value: Any) -> None:
        if value is None or value == "":
            self.missing_fields += 1
        elif (not isinstance(value, str)
              or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}", value) is None):
            self.invalid_fields += 1
        else:
            self.models.add(value)

    @property
    def model(self) -> str:
        # Never turn the requested name into claimed response identity.
        return next(iter(self.models)) if len(self.models) == 1 and not self.invalid_fields else ""

    def metadata(self, requested: str) -> dict[str, Any]:
        status = ("inconsistent" if len(self.models) > 1 else "invalid" if self.invalid_fields
                  else "missing" if not self.models else "partial" if self.missing_fields else "consistent")
        return {"requested_model": requested, "response_models": sorted(self.models),
                "response_model_status": status, "response_model_missing_fields": self.missing_fields,
                "response_model_invalid_fields": self.invalid_fields}


def _unwrap_completion(value: Any) -> Any:
    for wrapper in ("data", "result"):
        nested = _envelope_field(value, wrapper)
        if _envelope_field(nested, "choices") is not None:
            return nested
    return value


def _public_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(block.get("text", "")) for block in content
                       if isinstance(block, dict) and block.get("type") in {"text", "output_text"})
    return ""


def _decode_gateway_body(body: str | bytes) -> tuple[Any, bool]:
    """Decode a completed HTTP body when a gateway ignores the requested format.

    SSE event data may span lines; comments, heartbeat events and [DONE] are
    transport markers, never final-output evidence. Invalid events fail visibly
    rather than accepting a prefix that might omit an execution instruction.
    """
    text = body.decode("utf-8-sig") if isinstance(body, bytes) else body.lstrip("\ufeff")
    try:
        return json.loads(text), False
    except json.JSONDecodeError:
        pass
    events: list[Any] = []
    data: list[str] = []
    done = False

    def flush() -> None:
        nonlocal done
        if not data:
            return
        payload = "\n".join(data)
        data.clear()
        if payload.strip() == "[DONE]":
            done = True
            return
        if done:
            raise ValueError("Invalid upstream SSE: data after end marker")
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            raise ValueError("Invalid upstream SSE event JSON") from None
        if not isinstance(event, dict):
            raise ValueError("Invalid upstream SSE event envelope")
        events.append(event)

    for line in text.splitlines():
        if not line:
            flush()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif line.startswith((":", "event:", "id:", "retry:")):
            continue
        else:
            raise ValueError("Invalid upstream completion transport")
    flush()
    if not events:
        raise ValueError("Empty upstream SSE body")
    return events, True


@dataclass
class VisibleStreamAccumulator:
    """Assemble public text and indexed tool deltas, never private reasoning."""

    pieces: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    fingerprint: str | None = None
    chunks: int = 0
    visible_chars: int = 0
    identity: ResponseModelIdentity = field(default_factory=ResponseModelIdentity)
    tool_fragments: dict[int, dict[str, str]] = field(default_factory=dict)

    def add(self, chunk: Any) -> None:
        chunk = _unwrap_completion(chunk)
        if _envelope_field(chunk, "error"):
            raise ValueError("Upstream SSE returned an error event")
        self.chunks += 1
        self.identity.add(_envelope_field(chunk, "model"))
        usage = _usage_payload(_envelope_field(chunk, "usage"))
        if usage is not None:
            self.usage = usage
        fingerprint = _envelope_field(chunk, "system_fingerprint")
        if fingerprint:
            self.fingerprint = str(fingerprint)
        choices = _envelope_field(chunk, "choices") or ()
        for choice in choices:
            if (_envelope_field(choice, "index") or 0) != 0:
                continue
            delta = _envelope_field(choice, "delta")
            # Some gateways wrap a complete message in a single SSE event.
            if delta is None:
                delta = _envelope_field(choice, "message")
            piece = _public_content(_envelope_field(delta, "content"))
            if piece:
                self.pieces.append(piece)
                self.visible_chars += len(piece)
            calls = _envelope_field(delta, "tool_calls") or ()
            legacy = _envelope_field(delta, "function_call")
            if legacy and not calls:
                calls = ({"index": 0, "function": legacy},)
            for call in calls:
                index = _envelope_field(call, "index")
                if index is None and len(calls) == 1:
                    index = 0
                if not isinstance(index, int) or index < 0:
                    raise ValueError("Invalid upstream tool call index")
                parts = self.tool_fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                call_id = _envelope_field(call, "id")
                if call_id:
                    if parts["id"] and parts["id"] != call_id:
                        raise ValueError("Conflicting upstream tool call identity")
                    parts["id"] = str(call_id)
                function = _envelope_field(call, "function")
                for key in ("name", "arguments"):
                    value = _envelope_field(function, key)
                    if value is None:
                        continue
                    if key == "arguments" and isinstance(value, dict):
                        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
                    if not isinstance(value, str):
                        raise ValueError("Invalid upstream tool call fragment")
                    parts[key] += value
            reason = _envelope_field(choice, "finish_reason")
            if reason is not None:
                self.finish_reason = str(reason)

    def completion(self, *, provider: str, model: str, allow_tools: bool = False) -> Completion:
        text = "".join(self.pieces)
        code = ("output_truncated" if self.finish_reason == "length" else
                "incomplete_stream" if self.finish_reason not in {"stop", "tool_calls", "function_call"} else
                "empty_final_content" if not text.strip() and not self.tool_fragments else "")
        if code:
            raise LLMCompletionError(code=code, provider=provider, model=model,
                                     finish_reason=self.finish_reason, empty_final=not bool(text.strip() or self.tool_fragments),
                                     usage=self.usage, model_identity=self.identity.metadata(model))
        if self.tool_fragments and not allow_tools:
            raise ValueError("unsolicited tool calls without configured tools")
        calls = []
        for _, parts in sorted(self.tool_fragments.items()):
            if not parts["name"] or not parts["arguments"]:
                raise ValueError("Incomplete upstream streamed tool call")
            call_id = parts["id"] or "compat_" + hashlib.sha256(
                (parts["name"] + parts["arguments"]).encode()).hexdigest()[:24]
            calls.append(ToolCall(id=call_id, name=parts["name"], arguments=parts["arguments"]))
        return Completion(text=text, tool_calls=tuple(calls), provider=provider, model=self.identity.model,
                          raw={"usage": self.usage, "finish_reason": self.finish_reason,
                               "system_fingerprint": self.fingerprint, "streamed": True, "stream_chunks": self.chunks,
                               **self.identity.metadata(model)})


class _OpenAICompatProvider(LLMProvider):
    name = "openai"
    # SDK-level retries are disabled; every explicit attempt invokes the observer.
    budget_attempts_observable = True

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
        # Freeze the SDK environment default so checkpoint identity and the
        # later lazy client cannot resolve different endpoints.
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        if provider_name:
            self.name = provider_name
        self._default_thinking_enabled = default_thinking_enabled
        self._default_reasoning_effort = default_reasoning_effort
        self._client: Any = None

    @property
    def base_url(self) -> str:
        endpoint = str(self._client.base_url) if self._client is not None else self._base_url
        return public_endpoint_url(endpoint)

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
        glm_thinking = requires_glm_thinking(self.name, config.model)
        if glm_thinking:
            if config.thinking_enabled is False:
                raise ModelCompatibilityError("GLM-5.3 requires thinking enabled; use reasoning_effort=low")
            thinking_enabled = True
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
            if thinking_enabled:
                # Each request is a new reasoning turn over public observations.
                # Never replay incomplete assistant/tool turns without the private
                # reasoning history. GLM and DeepSeek use the same explicit
                # observation-only contract; no hidden reasoning is replayed.
                if ((self.name != "deepseek" and not supports_glm_observation_tools(self.name, config.model))
                        or config.extra.get("native_observation_history") is not True
                        or any(m.role in {"assistant", "tool"} or m.tool_calls for m in messages)):
                    raise ModelCompatibilityError("native thinking tools require supported observation-only history")
            kwargs["tools"] = list(config.tools)
            kwargs["parallel_tool_calls"] = False
            if self.name == "deepseek" and config.thinking_enabled is False and len(config.tools) == 1:
                tool = config.tools[0]
                function = tool.get("function")
                if (tool.get("type") == "function" and isinstance(function, dict)
                        and function.get("name") == "mars_submit_document"):
                    # The terminal submission has no competing action. DeepSeek
                    # allows a named tool choice only outside thinking mode.
                    kwargs["tool_choice"] = {"type": "function", "function": {"name": function["name"]}}
        if config.json_mode and glm_thinking:
            # Verified GLM-5.3 JSON mode removes literal `json` from string
            # values. Keep exact output and downstream JSON/schema checks;
            # request JSON text instead of altering the returned evidence.
            kwargs["messages"].append({"role": "system", "content":
                "Output exactly one valid JSON object. Do not include Markdown code fences, "
                "preambles, or text outside the object. Preserve every character in string values, "
                "including complete filenames ending in .json or .jsonl."})
        elif config.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if stream:
            kwargs["stream"] = True

        reasoning_effort = config.reasoning_effort or self._default_reasoning_effort
        if glm_thinking:
            reasoning_effort = reasoning_effort or "low"
            if reasoning_effort not in {"low", "high", "max"}:
                raise ModelCompatibilityError("GLM-5.3 reasoning_effort must be low, high or max")
        if self.name == "deepseek" and thinking_enabled is False:
            # DeepSeek effort values enable thinking, so a disabled mode must
            # take precedence over both call-specific and inherited effort.
            reasoning_effort = None
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        if self.name == "zhipu":
            if glm_thinking or config.thinking_enabled is not None:
                kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
        elif self.name == "deepseek" and thinking_enabled is False:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        elif thinking_enabled:
            # DeepSeek exposes thinking mode as an OpenAI-compatible extension.
            # Keep it in extra_body so other compatible endpoints are unchanged
            # unless their own config explicitly enables it.
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        if config.extra.get("gateway_plain_request") is True and not config.tools:
            for optional in ("extra_body", "reasoning_effort", "response_format", "parallel_tool_calls", "top_p"):
                kwargs.pop(optional, None)
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
        record_provider_request(config, self.name, request_kwargs)
        resp = await self._request_with_retries(
            lambda: client.chat.completions.create(**request_kwargs),
            config=config,
        )
        return self._completion_from_response(resp, config)

    async def complete_wire(self, payload: dict[str, Any], config: LLMConfig) -> dict[str, Any]:
        """Forward an owned external harness request under guarded_complete.

        Preserve the external harness's tool/history wire format. The caller
        must reserve the entire payload and enforce model/tools/output limits.
        SDK retries remain disabled; attempts use the same accounting observer.
        Credentials never leave this selected provider.
        """
        from openai.types.chat import ChatCompletion

        client = self._get_client()
        record_provider_request(config, self.name, payload)
        response = await self._request_with_retries(
            lambda: client.post("/chat/completions", body=payload, cast_to=ChatCompletion,
                                options={"timeout": config.request_timeout_seconds}), config=config)
        return self._wire_from_response(response, payload, config)

    def _wire_from_response(self, response: Any, payload: dict[str, Any], config: LLMConfig) -> dict[str, Any]:
        if isinstance(response, (str, bytes)):
            decoded, is_sse = _decode_gateway_body(response)
            if is_sse:
                state = VisibleStreamAccumulator()
                for chunk in decoded:
                    state.add(chunk)
                result_config = replace(config, tools=tuple(payload.get("tools") or ()))
                completion = state.completion(provider=self.name, model=result_config.model,
                                              allow_tools=bool(result_config.tools))
                message: dict[str, Any] = {"role": "assistant", "content": completion.text or None}
                if completion.tool_calls:
                    message["tool_calls"] = [call.to_wire() for call in completion.tool_calls]
                return {"object": "chat.completion", "model": completion.model,
                        "choices": [{"index": 0, "message": message, "finish_reason": state.finish_reason}],
                        "usage": state.usage}
            response = decoded
        result = (_unwrap_completion(response) if isinstance(response, Mapping)
                  else response.model_dump(exclude_none=True))
        if not isinstance(result, dict):
            raise ValueError("Invalid upstream completion envelope")
        return result

    def _completion_from_response(self, resp: Any, config: LLMConfig) -> Completion:
        """Pure SDK-envelope parsing; it performs no model/service execution."""
        if isinstance(resp, (str, bytes)):
            resp, is_sse = _decode_gateway_body(resp)
            if is_sse:
                state = VisibleStreamAccumulator()
                for chunk in resp:
                    state.add(chunk)
                return state.completion(provider=self.name, model=config.model, allow_tools=bool(config.tools))
        resp = _unwrap_completion(resp)
        identity = ResponseModelIdentity()
        identity.add(_envelope_field(resp, "model"))
        choices = _envelope_field(resp, "choices")
        if not choices:
            raise LLMCompletionError(code="empty_final_content", provider=self.name, model=config.model,
                                     finish_reason=None, empty_final=True,
                                     usage=_usage_payload(_envelope_field(resp, "usage")),
                                     model_identity=identity.metadata(config.model))
        response_choice = choices[0]
        message = _envelope_field(response_choice, "message")
        # Never surface or persist reasoning_content. It may contain hidden
        # chain-of-thought and is not the provider's final answer.
        content = _envelope_field(message, "content") if message is not None else _envelope_field(response_choice, "text")
        text = _public_content(content)
        finish_reason = _optional_string(
            _envelope_field(response_choice, "finish_reason")
        )
        calls_list: list[ToolCall] = []
        raw_calls = _envelope_field(message, "tool_calls") or ()
        legacy = _envelope_field(message, "function_call")
        if not raw_calls and legacy:
            raw_calls = ({"function": legacy},)
        for call in raw_calls:
            function = call.get("function", {}) if isinstance(call, dict) else call.function
            name = function.get("name") if isinstance(function, dict) else getattr(function, "name", None)
            arguments = function.get("arguments", {}) if isinstance(function, dict) else getattr(function, "arguments", "{}")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
            if not isinstance(name, str) or not name or not isinstance(arguments, str):
                raise ValueError("Invalid upstream completion envelope")
            call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
            if not call_id:
                call_id = "compat_" + hashlib.sha256((name + arguments).encode()).hexdigest()[:24]
            calls_list.append(ToolCall(id=call_id, name=name, arguments=arguments))
        calls = tuple(calls_list)
        if calls and not config.tools:
            raise ValueError("unsolicited tool calls without configured tools")
        self._check_final(config, finish_reason, bool(text.strip()) or bool(calls),
                          usage=_usage_payload(_envelope_field(resp, "usage")),
                          model_identity=identity.metadata(config.model))
        return Completion(
            text=text,
            tool_calls=calls,
            provider=self.name,
            model=identity.model,
            is_mock=False,
            raw={
                "usage": _usage_payload(_envelope_field(resp, "usage")),
                "system_fingerprint": _optional_string(
                    _envelope_field(resp, "system_fingerprint")
                ),
                "finish_reason": finish_reason,
                **identity.metadata(config.model),
            },
        )

    def _check_final(self, config: LLMConfig, finish_reason: str | None, visible_content_seen: bool,
                     *, usage: dict[str, Any] | None = None, model_identity: dict[str, Any] | None = None) -> None:
        if finish_reason == "length" or not visible_content_seen:
            raise LLMCompletionError(
                code="output_truncated" if finish_reason == "length" else "empty_final_content",
                provider=self.name, model=config.model, finish_reason=finish_reason,
                empty_final=not visible_content_seen, usage=usage, model_identity=model_identity,
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
        record_provider_request(config, self.name, request_kwargs)
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
        record_provider_request(config, self.name, kwargs)

        async def consume() -> Completion:
            state = VisibleStreamAccumulator()
            stream = await client.chat.completions.create(**kwargs)
            if isinstance(stream, (str, bytes, Mapping)) or _envelope_field(stream, "choices") is not None:
                return self._completion_from_response(stream, config)
            async with stream:
                async for chunk in stream:
                    state.add(chunk)
                    if config.attempt_observer and (state.chunks == 1 or state.chunks % 128 == 0):
                        config.attempt_observer("sdk_stream_progress", {
                            "chunks": state.chunks, "visible_chars": state.visible_chars,
                        })
            return state.completion(provider=self.name, model=config.model, allow_tools=bool(config.tools))

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
