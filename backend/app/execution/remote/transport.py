"""System OpenSSH transport used by the remote GPU executor.

All commands are constructed as argv tuples and executed without a shell.  The
only remote command is the fixed ``app.execution.remote.runner`` module; job
payloads and candidate content travel in uploaded files instead of argv.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Protocol


@dataclass(frozen=True)
class TransportResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class RemoteTransport(Protocol):
    def local_findings(self) -> tuple[str, ...]: ...

    async def run(
        self,
        remote_argv: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> TransportResult: ...

    async def upload(
        self,
        local_path: Path,
        remote_path: str,
        *,
        timeout_seconds: float,
    ) -> TransportResult: ...

    async def download(
        self,
        remote_path: str,
        local_path: Path,
        *,
        timeout_seconds: float,
    ) -> TransportResult: ...


@dataclass(frozen=True)
class SystemSshTransport:
    host: str
    port: int
    user: str
    key_path: Path
    known_hosts_path: Path
    connect_timeout_seconds: float = 10.0
    ssh_binary: str = "ssh"
    scp_binary: str = "scp"

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"

    def local_findings(self) -> tuple[str, ...]:
        findings: list[str] = []
        if shutil.which(self.ssh_binary) is None:
            findings.append(f"ssh binary not found: {self.ssh_binary}")
        if shutil.which(self.scp_binary) is None:
            findings.append(f"scp binary not found: {self.scp_binary}")
        if not self.key_path.is_file():
            findings.append(f"SSH key not found: {self.key_path}")
        if not self.known_hosts_path.is_file():
            findings.append(f"known_hosts not found: {self.known_hosts_path}")
        return tuple(findings)

    async def run(
        self,
        remote_argv: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> TransportResult:
        if not remote_argv:
            raise ValueError("remote argv must not be empty")
        argv = (
            self.ssh_binary,
            *self._ssh_options(),
            "--",
            self.target,
            *remote_argv,
        )
        return await _run_argv(argv, timeout_seconds=timeout_seconds)

    async def upload(
        self,
        local_path: Path,
        remote_path: str,
        *,
        timeout_seconds: float,
    ) -> TransportResult:
        argv = (
            self.scp_binary,
            *self._scp_options(),
            "--",
            str(local_path),
            f"{self._scp_target()}:{remote_path}",
        )
        return await _run_argv(argv, timeout_seconds=timeout_seconds)

    async def download(
        self,
        remote_path: str,
        local_path: Path,
        *,
        timeout_seconds: float,
    ) -> TransportResult:
        argv = (
            self.scp_binary,
            *self._scp_options(),
            "--",
            f"{self._scp_target()}:{remote_path}",
            str(local_path),
        )
        return await _run_argv(argv, timeout_seconds=timeout_seconds)

    def _ssh_options(self) -> tuple[str, ...]:
        return (
            "-p",
            str(self.port),
            "-i",
            str(self.key_path),
            "-o",
            f"UserKnownHostsFile={self.known_hosts_path}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={max(1, int(self.connect_timeout_seconds))}",
        )

    def _scp_options(self) -> tuple[str, ...]:
        ssh_options = self._ssh_options()
        return ("-P", ssh_options[1], *ssh_options[2:])

    def _scp_target(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.user}@{host}"


async def _run_argv(
    argv: tuple[str, ...],
    *,
    timeout_seconds: float,
) -> TransportResult:
    """Execute one local OpenSSH argv with bounded output and no shell."""

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return TransportResult(
            argv=argv,
            returncode=124,
            stderr=f"command exceeded {timeout_seconds}s",
        )
    return TransportResult(
        argv=argv,
        returncode=process.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace")[-1_000_000:],
        stderr=stderr.decode("utf-8", errors="replace")[-16_000:],
    )
