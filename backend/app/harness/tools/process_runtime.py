"""Bounded local processes for trusted commands, not an OS security sandbox."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import signal
import subprocess
from typing import Any

_SECRET_MARKERS = (
    "api_key", "apikey", "access_key", "auth_token", "credential", "password",
    "passwd", "private_key", "secret", "session_token", "ssh_key", "token",
)
_BLOCKED_NAMES = frozenset({
    "all_proxy", "gpg_agent_info", "http_proxy", "https_proxy", "no_proxy",
    "pythonhome", "pythoninspect", "pythonpath", "pythonstartup", "ssh_auth_sock",
})


def sanitized_subprocess_environment(
    *, inherited: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    def secret(name: str) -> bool:
        normalized = name.strip().casefold()
        return normalized.startswith("mars_remote_ssh_") or any(item in normalized for item in _SECRET_MARKERS)

    def blocked(name: str) -> bool:
        normalized = name.strip().casefold()
        return normalized in _BLOCKED_NAMES or normalized.startswith("dyld_") or normalized == "ld_preload"

    source = os.environ if inherited is None else inherited
    result = {name: value for name, value in source.items() if not blocked(name) and not secret(name)}
    result.update({str(name): str(value) for name, value in (overrides or {}).items() if not secret(name)})
    return result


def require_process_backend(backend: str = "local_process", *, require_isolation: bool = False) -> None:
    if backend != "local_process" or require_isolation:
        raise ValueError("OS isolation backend unavailable; local_process only supports trusted commands")


async def start_process(
    argv: Sequence[str], *, cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    stdin: Any = asyncio.subprocess.PIPE,
    stdout: Any = asyncio.subprocess.PIPE,
    stderr: Any = asyncio.subprocess.PIPE,
    backend: str = "local_process", require_isolation: bool = False,
    credential_env: Mapping[str, str] | None = None,
) -> asyncio.subprocess.Process:
    """Start a private group; env and credential_env must be host-owned.

    Explicit credential_env is reserved for MCP servers requiring named keys.
    No model or API arguments may select credential environment variables.
    """
    require_process_backend(backend, require_isolation=require_isolation)
    if not argv:
        raise ValueError("process argv must not be empty")
    child_env = sanitized_subprocess_environment(overrides=env)
    child_env.update(credential_env or {})
    options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    }
    return await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=child_env, stdin=stdin, stdout=stdout, stderr=stderr, **options,
    )


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Kill the group even if its leader exited but descendants retained pipes."""
    if os.name == "nt":
        if process.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


async def communicate_process(
    process: asyncio.subprocess.Process, *, input_data: bytes | None = None, timeout: float,
) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(input_data), timeout=timeout)
    except BaseException:
        await terminate_process_tree(process)
        raise


async def wait_process(process: asyncio.subprocess.Process, *, timeout: float) -> int:
    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout)
    except BaseException:
        await terminate_process_tree(process)
        raise
