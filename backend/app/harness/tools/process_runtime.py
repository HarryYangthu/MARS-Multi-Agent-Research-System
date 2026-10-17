"""Bounded local processes for trusted commands, not an OS security sandbox."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
    stream_limit: int = 65536,
    replace_environment: bool = False,
) -> asyncio.subprocess.Process:
    """Start a private group; env and credential_env must be host-owned.

    Explicit credential_env is reserved for MCP servers requiring named keys.
    No model or API arguments may select credential environment variables.
    """
    require_process_backend(backend, require_isolation=require_isolation)
    if not argv:
        raise ValueError("process argv must not be empty")
    if replace_environment and env is None:
        raise ValueError("replace_environment requires a complete host-owned environment")
    child_env = (sanitized_subprocess_environment(inherited=env) if replace_environment
                 else sanitized_subprocess_environment(overrides=env))
    child_env.update(credential_env or {})
    options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    }
    return await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=child_env, stdin=stdin, stdout=stdout, stderr=stderr, limit=stream_limit, **options,
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


@dataclass
class CapturedProcessStream:
    """A disk prefix and bounded memory tail, including interrupted captures."""
    path: Path
    max_log_bytes: int
    max_tail_bytes: int
    observed_bytes: int = 0
    written_bytes: int = 0
    truncated: bool = False
    complete: bool = False
    tail: bytearray = field(default_factory=bytearray)

    def __post_init__(self) -> None:
        if (type(self.max_log_bytes) is not int or not 1024 <= self.max_log_bytes <= 16777216
                or type(self.max_tail_bytes) is not int
                or not 0 < self.max_tail_bytes <= min(self.max_log_bytes, 65536)):
            raise ValueError('process log limit must be 1024-16777216 bytes and tail limit must fit inside it (max 65536)')

    def retain_tail(self, block: bytes) -> None:
        self.observed_bytes += len(block)
        self.tail.extend(block)
        if len(self.tail) > self.max_tail_bytes:
            del self.tail[:-self.max_tail_bytes]

    async def consume(self, reader: asyncio.StreamReader | None) -> None:
        marker = b'\n[MARS: log truncated at configured byte limit; see execution_receipt.json]\n'
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            if reader is None:
                self.complete = True
                return
            while True:
                block = await reader.read(65536)
                if not block:
                    self.complete = True
                    return
                self.retain_tail(block)
                if not self.truncated:
                    remaining = self.max_log_bytes - self.written_bytes
                    prefix = block[:remaining]
                    output.write(prefix)
                    self.written_bytes += len(prefix)
                    if len(block) > remaining:
                        self.truncated = True
                        prefix_size = self.max_log_bytes - len(marker)
                        output.seek(prefix_size)
                        output.truncate(prefix_size)
                        output.write(marker)
                        self.written_bytes = self.max_log_bytes
                    output.flush()

    def metadata(self) -> dict[str, Any]:
        return {'path': str(self.path), 'observed_bytes': self.observed_bytes,
                'written_bytes': self.written_bytes, 'tail_bytes': len(self.tail),
                'truncated': self.truncated, 'complete': self.complete}


@dataclass
class ProcessOutputCapture:
    stdout: CapturedProcessStream
    stderr: CapturedProcessStream

    def metadata(self) -> dict[str, Any]:
        return {'max_log_bytes_per_stream': self.stdout.max_log_bytes,
                'max_tail_bytes_per_stream': self.stdout.max_tail_bytes,
                'stdout': self.stdout.metadata(), 'stderr': self.stderr.metadata()}


async def _drain_after_capture_failure(reader: asyncio.StreamReader | None, stream: CapturedProcessStream) -> None:
    if reader is not None:
        while block := await reader.read(65536):
            stream.retain_tail(block)


async def stream_process_output(
    process: asyncio.subprocess.Process, *, capture: ProcessOutputCapture, timeout: float,
) -> tuple[bytes, bytes]:
    """Drain both pipes to bounded files, using the existing timeout/kill policy."""
    readers: list[asyncio.Task[None]] = []
    completion: asyncio.Future[Any] | None = None
    try:
        # communicate(None) closed stdin; preserve that EOF behavior here.
        if process.stdin is not None:
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        readers = [asyncio.create_task(capture.stdout.consume(process.stdout)),
                   asyncio.create_task(capture.stderr.consume(process.stderr))]
        completion = asyncio.gather(*readers, process.wait())
        # Keep draining while terminating the group on timeout/cancellation.
        # Cancelling readers first can leave full pipes blocking process.wait().
        await asyncio.wait_for(asyncio.shield(completion), timeout=timeout)
        return bytes(capture.stdout.tail), bytes(capture.stderr.tail)
    except BaseException:
        for index, (reader, stream) in enumerate(((process.stdout, capture.stdout),
                                                 (process.stderr, capture.stderr))):
            if (index >= len(readers) or (readers[index].done() and not readers[index].cancelled()
                                         and readers[index].exception() is not None)):
                # A disk error must not leave its pipe full while group cleanup
                # waits for process exit. Drain only to the same bounded tail.
                readers.append(asyncio.create_task(_drain_after_capture_failure(reader, stream)))
        try:
            await terminate_process_tree(process)
        finally:
            for task in readers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if completion is not None:
                await asyncio.gather(completion, return_exceptions=True)
            capture.stdout.complete = capture.stderr.complete = False
        raise
