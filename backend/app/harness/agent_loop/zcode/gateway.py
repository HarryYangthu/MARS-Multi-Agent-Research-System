"""Private model/MCP gateway: actual MARS budget and ToolRegistry enforcement."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
import json
from pathlib import Path
from typing import Any, cast

from aiohttp import web

from app.harness.agent_loop.executor import LoopInput
from app.harness.agent_loop.native_protocol import native_specs, wire_name
from app.harness.agent_loop.trace import LoopTrace, canonical, atomic_json
from app.harness.agent_loop.zcode.config import ZCodeConfig
from app.harness.llm.accounting import guarded_complete
from app.harness.llm.provider_base import Completion, Delta, LLMConfig, LLMProvider, Message
from app.harness.schema.frontmatter_parser import dumps

WireCall = Callable[[dict[str, Any], LLMConfig], Awaitable[dict[str, Any]]]


class WireProvider(LLMProvider):
    """Budget-visible real forwarding provider, not another model configuration."""
    budget_attempts_observable = True

    def __init__(self, selected: LLMProvider, payload: dict[str, Any]) -> None:
        method = getattr(selected, "complete_wire", None)
        if not callable(method) or not selected.budget_attempts_observable:
            raise ValueError("ZCode requires a budget-observable OpenAI-compatible provider")
        self.method = cast(WireCall, method)
        self.selected, self.payload = selected, payload
        self.name = selected.name

    @property
    def base_url(self) -> str | None:
        return self.selected.base_url

    async def complete(self, messages: list[Message], config: LLMConfig) -> Completion:
        payload = {**self.payload, "stream": False, "model": config.model, "max_tokens": config.max_tokens}
        payload.pop("max_completion_tokens", None)
        if config.thinking_enabled is not None:
            payload["thinking"] = {"type": "enabled" if config.thinking_enabled else "disabled"}
        if config.reasoning_effort:
            payload["reasoning_effort"] = config.reasoning_effort
        response = await self.method(payload, config)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Upstream completion has no choices")
        message = choices[0].get("message", {})
        return Completion(text=str(message.get("content") or ""), provider=self.name,
                          model=str(response.get("model") or ""),
                          raw={"usage": response.get("usage"), "wire_response": response})

    async def stream(self, messages: list[Message], config: LLMConfig) -> AsyncIterator[Delta]:
        result = await self.complete(messages, config)
        reason = result.raw["wire_response"]["choices"][0].get("finish_reason")
        yield Delta(text=result.text, finish_reason=reason, usage=result.raw.get("usage"))


def wire_tools(request: LoopInput) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for name in request.tools:
        spec = request.registry.spec(name)
        if spec is None or not request.registry.has(name) or spec.bridge_only:
            raise ValueError("ZCode tool is not registered: " + name)
        specs.append({"name": name, "description": spec.description, "args_schema": spec.input_schema})
    return [{"name": item["function"]["name"], "description": item["function"]["description"],
             "inputSchema": item["function"]["parameters"]}
            for item in native_specs(specs, request.final_schema)]


def protocol_tool_name(name: str) -> str:
    # Official MCP adapter collapses repeated underscores in each name part.
    import re
    return "mcp__mars__" + re.sub(r"_+", "_", name)


def stream_envelope(response: dict[str, Any]) -> str:
    """Translate a real upstream completion to OpenAI SSE, preserving tool IDs.

    No model content or usage is invented. Upstream runs non-streaming so the
    accounting boundary gets a complete usage receipt before returning data.
    """
    common: dict[str, Any] = {key: response[key] for key in ("id", "created", "model") if key in response}
    choices: list[dict[str, Any]] = []
    for choice in response["choices"]:
        delta = dict(choice["message"])
        if "tool_calls" in delta:
            delta["tool_calls"] = [{**call, "index": i} for i, call in enumerate(delta["tool_calls"])]
        choices.append({"index": choice.get("index", 0), "delta": delta, "finish_reason": None})
    chunks: list[dict[str, Any]] = [{**common, "object": "chat.completion.chunk", "choices": choices},
              {**common, "object": "chat.completion.chunk", "choices": [
                  {"index": c.get("index", 0), "delta": {}, "finish_reason": c.get("finish_reason")}
                  for c in response["choices"]], "usage": response.get("usage")}]
    return "".join("data: " + canonical(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"


class ZCodeGateway:
    def __init__(self, request: LoopInput, config: ZCodeConfig, token: str,
                 state: dict[str, Any], trace: LoopTrace) -> None:
        self.request, self.config, self.token = request, config, token
        self.state, self.trace = state, trace
        self.tools = wire_tools(request)
        self.names = {wire_name(name): name for name in request.tools}
        self.allowed_model_tools = {protocol_tool_name(item["name"]) for item in self.tools}
        self.done = asyncio.Event()
        self.lock = asyncio.Lock()
        self.runner: web.AppRunner | None = None
        self.endpoint = ""
        self.handlers: set[asyncio.Task[Any]] = set()

    async def progress(self, kind: str, **data: Any) -> None:
        if self.request.progress_sink:
            await self.request.progress_sink({"kind": kind, "phase": "zcode", **data})

    def save(self) -> None:
        self.trace.snapshot(self.state)

    def stop(self, status: str, error: str) -> None:
        self.state.update(status=status, error=error)
        self.save()
        self.done.set()

    async def start(self) -> None:
        @web.middleware
        async def track(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
            task = asyncio.current_task()
            if task is not None:
                self.handlers.add(task)
            try:
                return await handler(request)
            finally:
                if task is not None:
                    self.handlers.discard(task)

        app = web.Application(client_max_size=self.config.max_request_bytes, middlewares=[track])
        app.router.add_post("/v1/chat/completions", self.model)
        app.router.add_post("/mcp", self.mcp)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.endpoint = "http://127.0.0.1:" + str(sock.getsockname()[1])
        await web.SockSite(self.runner, sock).start()

    async def close(self) -> None:
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.runner:
            await self.runner.cleanup()

    def authenticate(self, request: web.Request) -> None:
        import secrets
        if not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + self.token):
            raise web.HTTPUnauthorized()
        if self.done.is_set() or self.state["status"] != "running":
            raise web.HTTPConflict(text="MARS invocation is stopped")

    async def model(self, http_request: web.Request) -> web.Response:
        self.authenticate(http_request)
        payload = await http_request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            raise web.HTTPBadRequest(text="Invalid model request")
        async with self.lock:
            counts = self.state["counts"]
            if self.state["status"] != "running":
                raise web.HTTPConflict(text="MARS invocation stopped")
            if (counts["model_requests"] >= self.config.max_model_calls
                    or not self.request.policy.allows_model_calls(counts["model_requests"])):
                self.stop("budget_exhausted", "ZCode model-call limit reached")
                raise web.HTTPTooManyRequests(text="MARS model budget exhausted")
            tools = payload.get("tools", [])
            if (not isinstance(tools, list) or any(
                    not isinstance(t, dict) or not isinstance(t.get("function"), dict)
                    or t["function"].get("name") not in self.allowed_model_tools
                    for t in tools)):
                self.stop("blocked", "ZCode advertised tools outside the MARS allowlist")
                raise web.HTTPForbidden(text="MARS tools only")
            counts["model_requests"] += 1
            self.state["pending"] = "model"
            public_messages = [{k: m[k] for k in ("role", "content", "tool_calls", "tool_call_id") if k in m}
                               for m in payload["messages"] if isinstance(m, dict)]
            self.trace.emit("model_request", {"backend": "zcode", "model": self.request.config.model},
                            visible={"messages": public_messages, "tools": tools})
            self.save()
            await self.progress("action", tool="model", reason="ZCode 调用模型")
            try:
                config = replace(self.request.config, max_retries=0, tools=tuple(tools),
                    attempt_observer=lambda kind, data: self.trace.record_attempt(self.state, kind, data))
                # Entire raw wire payload is reserved conservatively, including
                # tool metadata and any private history required by the SDK.
                response = await guarded_complete(WireProvider(self.request.provider, payload),
                    [Message("user", canonical(payload))], config,
                    run_root=run_root(self.request))
                result = response.raw["wire_response"]
                counts["model_responses"] += 1
                usage = result.get("usage")
                if isinstance(usage, dict):
                    for key in self.state["usage"]:
                        self.state["usage"][key] += int(usage.get(key) or 0)
                else:
                    self.state["usage_complete"] = False
                self.state["pending"] = None
                # Do not save upstream reasoning_content in the MARS trace.
                visible = [{"content": c.get("message", {}).get("content"),
                            "tool_calls": c.get("message", {}).get("tool_calls")}
                           for c in result["choices"]]
                self.trace.emit("model_response", {"model": response.model, "usage": usage}, visible=visible)
                self.save()
                if payload.get("stream"):
                    return web.Response(text=stream_envelope(result), content_type="text/event-stream")
                return web.json_response(result)
            except asyncio.CancelledError:
                self.state["usage_complete"] = False
                self.save()
                raise
            except Exception as exc:
                self.state["pending"] = None
                self.state["usage_complete"] = False
                self.stop("model_error", "ZCode model gateway: " + type(exc).__name__)
                return web.json_response({"error": {"message": "MARS model gateway rejected request", "type": "mars_error"}}, status=502)

    async def mcp(self, http_request: web.Request) -> web.Response:
        self.authenticate(http_request)
        payload = await http_request.json()
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest()
        method, key = payload.get("method"), payload.get("id")
        if key is None:
            return web.Response(status=202)
        if method == "initialize":
            result: Any = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "MARS", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": self.tools}
        elif method == "ping":
            result = {}
        elif method == "tools/call":
            params = payload.get("params", {})
            if not isinstance(params, dict) or not isinstance(params.get("arguments", {}), dict):
                raise web.HTTPBadRequest()
            async with self.lock:
                result = await self.call_tool(str(params.get("name", "")), params.get("arguments", {}))
        else:
            return web.json_response({"jsonrpc": "2.0", "id": key, "error": {"code": -32601, "message": "Unknown method"}})
        return web.json_response({"jsonrpc": "2.0", "id": key, "result": result})

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self.state["status"] != "running":
            return mcp_result({"error": "MARS invocation stopped"}, error=True)
        if name == "mars_submit_document" and self.request.final_schema:
            if set(args) != {"metadata", "body"} or not isinstance(args["metadata"], dict) or not isinstance(args["body"], str):
                return mcp_result({"error": "Supply metadata and body"}, error=True)
            if args["body"].lstrip().startswith("---\n"):
                return mcp_result({"error": "Body is Markdown only; put frontmatter fields in metadata, without duplicate YAML"}, error=True)
            candidate = dumps(args["metadata"], args["body"])
            await self.progress("candidate")
            errors = await self.request.validate(candidate, self.state["history"])
            self.trace.emit("validation", {"errors": errors}, visible=candidate)
            if errors:
                self.state["counts"]["validation_repairs"] += 1
                if self.state["counts"]["validation_repairs"] > self.config.max_validation_repairs:
                    self.stop("validation_failed", "Candidate did not pass MARS validation")
                self.save()
                return mcp_result({"accepted": False, "errors": errors}, error=True)
            self.state.update(candidate=candidate, pending=None, status="passed")
            atomic_json(self.request.trace_root / "submission.json", args)
            self.save()
            self.done.set()
            return mcp_result({"accepted": True, "review_required": True})
        tool = self.names.get(name)
        if tool is None:
            return mcp_result({"error": "Tool is outside MARS allowlist"}, error=True)
        counts = self.state["counts"]
        if (counts["tool_dispatches"] >= self.config.max_tool_calls
                or not self.request.policy.allows_tool_calls(counts["tool_dispatches"])):
            self.stop("budget_exhausted", "ZCode tool-call limit reached")
            return mcp_result({"error": "MARS tool budget exhausted"}, error=True)
        self.state["pending"] = "tool"
        counts["tool_dispatches"] += 1
        decision = {"tool": tool, "args": args}
        self.trace.emit("tool_dispatch", {"tool": tool, "step": counts["tool_dispatches"]}, visible=decision)
        self.save()
        await self.progress("action", tool=tool)
        result = await self.request.registry.dispatch(tool, args, self.request.tool_context)
        observation = {**decision, "ok": result.ok, "status": result.status, "output": result.output,
                       "error": result.error, "blocked_by_gate": result.blocked_by_gate}
        self.state["history"].append(observation)
        counts["observations"] += 1
        self.state["pending"] = None
        self.trace.emit("observation", {"tool": tool, "ok": result.ok}, visible=observation)
        self.save()
        await self.progress("observation", tool=tool, ok=result.ok)
        if result.requires_approval or result.blocked_by_gate:
            self.stop("blocked", result.error or "MARS approval required")
        rendered = canonical(observation)
        if len(rendered) > self.config.max_observation_chars:
            path = self.request.trace_root / "tools" / (str(counts["tool_dispatches"]) + ".json")
            atomic_json(path, observation)
            rendered = canonical({"tool": tool, "ok": result.ok, "status": result.status,
                                  "preview": rendered[:self.config.max_observation_chars], "raw_ref": str(path)})
        return {"content": [{"type": "text", "text": rendered}], "isError": not result.ok}


def run_root(request: LoopInput) -> Path:
    value = request.tool_context.extra.get("run_root")
    if not value:
        raise ValueError("ZCode requires a MARS run root")
    return Path(value)


def mcp_result(value: dict[str, Any], *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": canonical(value)}], "isError": error}
