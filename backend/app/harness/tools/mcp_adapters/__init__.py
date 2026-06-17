"""Optional MCP adapter metadata and health checks for MARS tools.

MARS tools remain the public capability boundary. These adapters describe and
health-check optional MCP backends; tool dispatch, Gate checks, HITL and run
sedimentation stay in the local ToolRegistry path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import shutil
import shlex
from typing import Literal


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
        available = _command_executable(command) if command else configured
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
            available=_command_executable(command) if command else bool(roots),
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
        available=(_command_executable(command) if command else token_configured) and enabled,
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
) -> dict[str, object]:
    command = _require_adapter_command(kind)
    async with _StdioMCPClient(command=command, timeout_seconds=timeout_seconds) as client:
        return await client.request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )


class _StdioMCPClient:
    def __init__(self, *, command: str, timeout_seconds: float) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def __aenter__(self) -> "_StdioMCPClient":
        argv = shlex.split(self.command)
        if not argv:
            raise MCPTransportError("MCP command is empty")
        self._process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self.request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mars", "version": "v1"},
            },
        )
        await self.notify("notifications/initialized", {})
        return self

    async def __aexit__(self, *_exc: object) -> None:
        process = self._process
        if process is None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

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
        return await self._read_response(request_id)

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
