"""Stream real execution logs into durable files and the event bus."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout_lines: int
    stderr_lines: int


async def stream_lines(
    lines: Iterable[str],
    *,
    bus_publish: Any | None,
    channel: str,
) -> AsyncIterator[str]:
    for line in lines:
        if bus_publish is not None:
            await bus_publish(channel, {"event": "execution.log_line", "line": line})
        yield line


async def stream_subprocess(
    spec: CommandSpec,
    *,
    bus_publish: Any | None,
    channel: str,
    log_path: Path | None = None,
) -> CommandResult:
    """Run a command and stream stdout/stderr as line events.

    The command is executed without a shell. Each emitted event includes a
    stream name so the frontend can render stdout and stderr distinctly.
    """
    if not spec.argv:
        raise ValueError("command argv must not be empty")

    process = await asyncio.create_subprocess_exec(
        *spec.argv,
        cwd=str(spec.cwd) if spec.cwd is not None else None,
        env=spec.env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if bus_publish is not None:
        await bus_publish(
            channel,
            {
                "event": "execution.command_started",
                "argv": list(spec.argv),
                "pid": process.pid,
            },
        )

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    stdout_count = 0
    stderr_count = 0

    async def _consume(reader: asyncio.StreamReader | None, stream: str) -> int:
        if reader is None:
            return 0
        count = 0
        while True:
            raw = await reader.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            count += 1
            if log_path is not None:
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{stream}] {line}\n")
            if bus_publish is not None:
                await bus_publish(
                    channel,
                    {
                        "event": "execution.log_line",
                        "stream": stream,
                        "line": line,
                    },
                )
        return count

    stdout_count, stderr_count = await asyncio.gather(
        _consume(process.stdout, "stdout"),
        _consume(process.stderr, "stderr"),
    )
    returncode = await process.wait()

    if bus_publish is not None:
        await bus_publish(
            channel,
            {
                "event": "execution.command_completed",
                "returncode": returncode,
                "stdout_lines": stdout_count,
                "stderr_lines": stderr_count,
            },
        )

    return CommandResult(
        returncode=returncode,
        stdout_lines=stdout_count,
        stderr_lines=stderr_count,
    )
