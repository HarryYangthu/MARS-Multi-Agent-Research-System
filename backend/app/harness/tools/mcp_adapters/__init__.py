"""MCP transport plus host-configured ToolRegistry bindings.

Server-discovered capabilities do not grant permissions. Public invocation is
through ToolRegistry; this module's transport helpers are for trusted callers.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import shutil
import shlex
import re
from typing import Literal, cast

from jsonschema import Draft202012Validator

from app.harness.tools.config import ToolConfig
from app.harness.tools.process_runtime import require_process_backend, start_process, terminate_process_tree
from app.harness.tools.registry import ToolContext, ToolFn, ToolResult


AdapterKind = Literal["chroma", "filesystem", "git", "github"]
_MCP_PROTOCOL_VERSION = "2025-06-18"
ADAPTER_KINDS: tuple[AdapterKind, ...] = ("chroma", "filesystem", "git", "github")


@dataclass(frozen=True)
class AdapterStatus:
    kind: AdapterKind
    configured: bool
    available: bool
    detail: str
    fallback: str
    tools: tuple[str, ...] = ()


class MCPTransportError(RuntimeError):
    pass


TOOL_ADAPTERS: dict[str, AdapterKind] = {
    "knowledge.kb_query": "chroma",
    "knowledge.experiment_memory": "chroma",
    "knowledge.code_assets": "chroma",
    "knowledge.methodology": "chroma",
    "knowledge.run_archive": "chroma",
    "knowledge.ingest_document": "chroma",
    "search.local_docs": "filesystem",
    "code.repo_reader": "filesystem",
    "code.write_file": "filesystem",
    "code.delete_file": "filesystem",
    "code.apply_patch": "git",
    "code.rollback_patch": "git",
}


def all_adapter_statuses() -> list[AdapterStatus]:
    return [adapter_status(kind) for kind in ADAPTER_KINDS]


def adapter_for_tool(tool_name: str) -> AdapterStatus | None:
    kind = TOOL_ADAPTERS.get(tool_name)
    if kind is None:
        return None
    return adapter_status(kind)


def adapter_status(kind: AdapterKind) -> AdapterStatus:
    tools = tuple(sorted(name for name, adapter in TOOL_ADAPTERS.items() if adapter == kind))
    command = _adapter_command(kind)
    if kind == "chroma":
        configured = bool(command) or _truthy(os.environ.get("MARS_MCP_CHROMA_ENABLED", ""))
        try:
            import chromadb  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on optional env
            return AdapterStatus(
                kind=kind,
                configured=configured,
                available=bool(command and _command_executable(command)),
                detail=str(exc),
                fallback="local JSON/Chroma-compatible KB store",
                tools=tools,
            )
        available = bool(command and _command_executable(command))
        return AdapterStatus(
            kind=kind,
            configured=configured,
            available=available,
            detail=(
                f"MCP command configured: {command}"
                if command
                else "chromadb import ok; MCP adapter enabled"
                if configured
                else "chromadb import ok; MCP adapter disabled"
            ),
            fallback="local JSON/Chroma-compatible KB store",
            tools=tools,
        )
    if kind == "filesystem":
        roots = tuple(_split_env("MARS_MCP_FILESYSTEM_ROOTS"))
        configured = bool(command or roots)
        return AdapterStatus(
            kind=kind,
            configured=configured,
            available=bool(command and _command_executable(command)),
            detail=(
                f"MCP command configured: {command}"
                if command
                else
                f"filesystem MCP roots configured: {', '.join(roots)}"
                if configured
                else "filesystem MCP roots are not configured"
            ),
            fallback="repo_link.yaml local filesystem resolver",
            tools=tools,
        )
    if kind == "git":
        configured = bool(command)
        return AdapterStatus(
            kind=kind,
            configured=configured,
            available=_command_executable(command),
            detail=(
                f"git MCP command configured: {command}"
                if configured
                else "git MCP command is not configured or not executable"
            ),
            fallback="local git CLI guarded by MARS ToolRegistry",
            tools=tools,
        )
    token_configured = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    enabled = bool(command) or _truthy(os.environ.get("MARS_MCP_GITHUB_ENABLED", ""))
    return AdapterStatus(
        kind=kind,
        configured=enabled,
        available=bool(command and _command_executable(command)) and enabled,
        detail=(
            f"GitHub MCP command configured: {command}"
            if command
            else "GitHub MCP enabled and token present"
            if enabled and token_configured
            else "GitHub MCP is disabled or token is missing"
        ),
        fallback="local MARS tool implementation",
        tools=tools,
    )


async def list_mcp_tools(kind: AdapterKind, *, timeout_seconds: float = 5.0) -> dict[str, object]:
    command = _require_adapter_command(kind)
    async with _StdioMCPClient(command=command, timeout_seconds=timeout_seconds) as client:
        return await client.request("tools/list", {})


async def call_mcp_tool(
    kind: AdapterKind,
    *,
    tool_name: str,
    arguments: dict[str, object],
    timeout_seconds: float = 10.0,
    credential_env_names: tuple[str, ...] = (),
) -> dict[str, object]:
    command = _require_adapter_command(kind)
    async with _StdioMCPClient(command=command, timeout_seconds=timeout_seconds,
                              credential_env_names=credential_env_names) as client:
        return await client.request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )


def validate_mcp_config(name: str, cfg: ToolConfig) -> None:
    if not name.startswith("mcp.") or cfg.mcp_kind not in ADAPTER_KINDS or not cfg.mcp_tool:
        raise ValueError("MCP bindings require mcp.* name, known mcp_kind and explicit mcp_tool")
    if not cfg.allowed_agents or cfg.input_schema is None or cfg.output_schema is None:
        raise ValueError("MCP bindings require explicit allowed_agents and input/output schemas")
    if cfg.mutation_level not in {"read", "write"}:
        raise ValueError("MCP mutation_level must explicitly describe read or write access")
    if cfg.mutation_level == "write" and not cfg.requires_approval:
        raise ValueError("MCP write tools require approval; server annotations cannot bypass it")
    if cfg.timeout_seconds <= 0:
        raise ValueError("MCP timeout must be positive")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in cfg.mcp_env):
        raise ValueError("mcp_env contains invalid environment variable names")
    Draft202012Validator.check_schema(cfg.input_schema)
    Draft202012Validator.check_schema(cfg.output_schema)


def configured_mcp_handler(cfg: ToolConfig) -> ToolFn:
    async def invoke(args: dict[str, object], ctx: ToolContext) -> ToolResult:
        if not ctx.run_id or not ctx.project or not ctx.agent or not ctx.extra.get("run_root"):
            return ToolResult(ok=False, status="not_allowed", error="MCP execution requires run/project/agent context")
        require_process_backend(cfg.process_backend, require_isolation=cfg.require_isolation)
        result = await call_mcp_tool(
            cast(AdapterKind, cfg.mcp_kind), tool_name=cfg.mcp_tool,
            arguments={key: value for key, value in args.items() if key != "_approval_id"},
            timeout_seconds=cfg.timeout_seconds, credential_env_names=cfg.mcp_env,
        )
        failed = result.get("isError") is True
        return ToolResult(ok=not failed, output=result,
                          error="MCP server reported a tool error" if failed else None,
                          metadata={"adapter": cfg.mcp_kind, "remote_tool": cfg.mcp_tool,
                                    "execution_backend": "local_process", "os_isolated": False})
    return invoke


class _StdioMCPClient:
    def __init__(self, *, command: str, timeout_seconds: float,
                 credential_env_names: tuple[str, ...] = ()) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self.credential_env_names = credential_env_names

    async def __aenter__(self) -> "_StdioMCPClient":
        argv = shlex.split(self.command)
        if not argv:
            raise MCPTransportError("MCP command is empty")
        self._process = await start_process(
            argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            credential_env={name: os.environ[name] for name in self.credential_env_names if name in os.environ},
        )
        try:
            await self.request(
                "initialize",
                {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mars", "version": "v1"},
                },
            )
            await self.notify("notifications/initialized", {})
        except BaseException:
            await terminate_process_tree(self._process)
            raise
        return self

    async def __aexit__(self, *_exc: object) -> None:
        process = self._process
        if process is None:
            return
        await terminate_process_tree(process)

    async def notify(self, method: str, params: dict[str, object]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            return await asyncio.wait_for(self._read_response(request_id), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            raise MCPTransportError("MCP request exceeded its total time budget") from exc

    async def _write(self, payload: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPTransportError("MCP process is not running")
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        process.stdin.write(encoded)
        await process.stdin.drain()

    async def _read_response(self, request_id: int) -> dict[str, object]:
        process = self._process
        if process is None or process.stdout is None:
            raise MCPTransportError("MCP process is not running")
        while True:
            raw = await asyncio.wait_for(process.stdout.readline(), timeout=self.timeout_seconds)
            if not raw:
                stderr = ""
                if process.stderr is not None:
                    try:
                        stderr_bytes = await asyncio.wait_for(process.stderr.read(), timeout=0.2)
                        stderr = stderr_bytes.decode("utf-8", errors="replace")
                    except asyncio.TimeoutError:
                        stderr = ""
                raise MCPTransportError(f"MCP process exited before response: {stderr}")
            try:
                message = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                raise MCPTransportError(json.dumps(message["error"], ensure_ascii=False))
            result = message.get("result", {})
            if not isinstance(result, dict):
                return {"value": result}
            return result


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _adapter_command(kind: AdapterKind) -> str:
    env_by_kind = {
        "chroma": "MARS_MCP_CHROMA_COMMAND",
        "filesystem": "MARS_MCP_FILESYSTEM_COMMAND",
        "git": "MARS_MCP_GIT_COMMAND",
        "github": "MARS_MCP_GITHUB_COMMAND",
    }
    return os.environ.get(env_by_kind[kind], "").strip()


def _require_adapter_command(kind: AdapterKind) -> str:
    command = _adapter_command(kind)
    if not command:
        raise MCPTransportError(f"{kind} MCP command is not configured")
    if not _command_executable(command):
        raise MCPTransportError(f"{kind} MCP command is not executable: {command}")
    return command


def _command_executable(command: str) -> bool:
    if not command:
        return False
    argv = shlex.split(command)
    return bool(argv and shutil.which(argv[0]))
