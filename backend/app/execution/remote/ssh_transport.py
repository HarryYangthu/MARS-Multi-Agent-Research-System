"""Explicit AsyncSSH connection boundary with pinned known-host verification.

Passwords/passphrases never enter executor configuration, job requests, argv,
receipts, or exception text. No user SSH config, default keys, agent, forwarding,
or authentication fallback is used. This layer does not certify GPU readiness.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
import ipaddress
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import tempfile
from typing import Any, Literal

import asyncssh

from app.execution.remote.transport import TransportResult

AuthenticationMethod = Literal["key", "password"]
_FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")


class SshConnectionError(ValueError):
    """A fixed, credential-free connection diagnostic."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SshCredentials:
    """Ephemeral connection-layer values, deliberately not a dataclass/model."""

    __slots__ = ("method", "_key_path", "_password", "_passphrase")

    def __init__(self, method: AuthenticationMethod, *, key_path: Path | None = None,
                 password: str | None = None, passphrase: str | None = None) -> None:
        if method not in {"key", "password"}:
            raise SshConnectionError("ssh_auth_method_invalid")
        if method == "password" and (not password or key_path is not None or passphrase is not None):
            raise SshConnectionError("ssh_password_credentials_invalid")
        if method == "key" and (key_path is None or password is not None):
            raise SshConnectionError("ssh_key_credentials_invalid")
        self.method = method
        self._key_path = key_path
        self._password = password
        self._passphrase = passphrase

    def __repr__(self) -> str:
        return f"SshCredentials(method={self.method!r}, credentials=[REDACTED])"

    def __reduce__(self) -> Any:
        raise TypeError("SSH credentials cannot be serialized")

    @classmethod
    def from_environment(cls, method: AuthenticationMethod, *, key_path: Path | None = None,
                         environ: Mapping[str, str] | None = None) -> SshCredentials:
        # Never env_or_local/Settings: credentials must not come from task YAML
        # or a repository .env file. Password whitespace is significant.
        source = os.environ if environ is None else environ
        if method == "password":
            return cls(method, password=source.get("MARS_REMOTE_SSH_PASSWORD"))
        return cls(method, key_path=key_path, passphrase=source.get("MARS_REMOTE_SSH_KEY_PASSPHRASE"))

    def _arguments(self) -> dict[str, Any]:
        if self.method == "password":
            return {"password": self._password, "client_keys": None, "password_auth": True,
                    "public_key_auth": False, "preferred_auth": ["password"]}
        if self._key_path is None or not self._key_path.is_file():
            raise SshConnectionError("ssh_private_key_unavailable")
        if os.name != "nt" and stat.S_IMODE(self._key_path.stat().st_mode) & 0o077:
            raise SshConnectionError("ssh_private_key_permissions_unsafe")
        return {"client_keys": [str(self._key_path)], "passphrase": self._passphrase,
                "password_auth": False, "public_key_auth": True, "preferred_auth": ["publickey"]}

    def _redact(self, value: str) -> str:
        for secret in (self._password, self._passphrase):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value


@dataclass(frozen=True)
class SshConnectionPolicy:
    host: str
    port: int
    user: str
    known_hosts_path: Path
    expected_host_key_sha256: str
    connect_timeout_seconds: float = 10.0
    max_output_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", self.host):
                raise ValueError("SSH host must be an explicit hostname or IP address") from None
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9._-]{0,63}", self.user):
            raise ValueError("SSH username is invalid")
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise ValueError("SSH port must be between 1 and 65535")
        if not _FINGERPRINT.fullmatch(self.expected_host_key_sha256):
            raise ValueError("An independently verified SHA256 SSH host-key fingerprint is required")
        if not math.isfinite(self.connect_timeout_seconds) or self.connect_timeout_seconds <= 0:
            raise ValueError("SSH connect timeout must be positive and finite")
        if type(self.max_output_bytes) is not int or self.max_output_bytes <= 0:
            raise ValueError("SSH output limit must be a positive integer")


@dataclass(frozen=True)
class SshPreflight:
    status: Literal["authenticated", "blocked"]
    auth_method: AuthenticationMethod
    host_key_verified: bool
    host_key_sha256: str | None = None
    error_code: str | None = None
    scope: str = "ssh_connection_only"


def _failure_code(error: Exception) -> str:
    if isinstance(error, SshConnectionError):
        return error.code
    if isinstance(error, asyncssh.HostKeyNotVerifiable):
        return "ssh_host_key_rejected"
    if isinstance(error, asyncssh.PermissionDenied):
        return "ssh_authentication_rejected"
    if isinstance(error, (TimeoutError, asyncssh.TimeoutError)):
        return "ssh_timeout_outcome_unknown"
    if isinstance(error, asyncssh.SFTPError):
        return "ssh_transfer_failed"
    if isinstance(error, (asyncssh.KeyImportError, asyncssh.KeyEncryptionError)):
        return "ssh_private_key_unavailable"
    if isinstance(error, OSError):
        return "ssh_network_or_local_io_failed"
    return "ssh_connection_failed"


class _HostVerificationReceipt(asyncssh.SSHClient):
    def __init__(self, expected: str, proof: dict[str, str]) -> None:
        self.expected = expected
        self.proof = proof
        self.connection: asyncssh.SSHClientConnection | None = None

    def connection_made(self, conn: asyncssh.SSHClientConnection) -> None:
        self.connection = conn

    def begin_auth(self, username: str) -> None:
        # Native known_hosts validation has already completed. Record the
        # actual negotiated key before any password/public-key authentication.
        key = self.connection.get_server_host_key() if self.connection else None
        if key is None or key.get_fingerprint("sha256") != self.expected:
            raise SshConnectionError("ssh_host_key_rejected")
        self.proof["host_key_sha256"] = key.get_fingerprint("sha256")


class AsyncSshTransport:
    def __init__(self, policy: SshConnectionPolicy, *, auth_method: AuthenticationMethod,
                 key_path: Path | None = None, credentials: SshCredentials | None = None) -> None:
        if auth_method not in {"key", "password"} or (credentials is not None and credentials.method != auth_method):
            raise ValueError("SSH authentication method and credentials must agree")
        self.policy = policy
        self.auth_method = auth_method
        self._key_path = key_path
        self._credentials = credentials

    def __repr__(self) -> str:
        return f"AsyncSshTransport(auth_method={self.auth_method!r})"

    def _credential(self) -> SshCredentials:
        return self._credentials or SshCredentials.from_environment(self.auth_method, key_path=self._key_path)

    def local_findings(self) -> tuple[str, ...]:
        if not self.policy.known_hosts_path.is_file():
            return ("ssh_known_hosts_unavailable",)
        try:
            asyncssh.read_known_hosts(str(self.policy.known_hosts_path))
            self._credential()._arguments()
        except (OSError, ValueError, asyncssh.Error) as error:
            return (_failure_code(error),)
        return ()

    @asynccontextmanager
    async def _connection(self, credentials: SshCredentials, proof: dict[str, str] | None = None) -> AsyncIterator[asyncssh.SSHClientConnection]:
        if not self.policy.known_hosts_path.is_file():
            raise SshConnectionError("ssh_known_hosts_unavailable")
        known = asyncssh.read_known_hosts(str(self.policy.known_hosts_path))

        def pinned_hosts(host: str, address: str, port: int | None) -> tuple[list[asyncssh.SSHKey], list[asyncssh.SSHKey], list[asyncssh.SSHKey]]:
            matched = asyncssh.match_known_hosts(known, host, address, port)
            trusted = [key for key in matched[0] if key.get_fingerprint("sha256") == self.policy.expected_host_key_sha256]
            # Native AsyncSSH still verifies the negotiated host key before
            # authentication. We narrow its trust set to the independently
            # pinned key and retain native @revoked handling. CA-only trust is
            # deliberately unsupported in this initial raw-host-key contract.
            return trusted, [], list(matched[2])

        async with asyncio.timeout(self.policy.connect_timeout_seconds):
            connection = await asyncssh.connect(
                self.policy.host, port=self.policy.port, username=self.policy.user,
                known_hosts=pinned_hosts, config=None, agent_path=None, agent_forwarding=False,
                client_factory=lambda: _HostVerificationReceipt(self.policy.expected_host_key_sha256, proof if proof is not None else {}),
                host_based_auth=False, kbdint_auth=False, gss_auth=False, gss_kex=False,
                connect_timeout=self.policy.connect_timeout_seconds,
                **credentials._arguments(),
            )
        try:
            yield connection
        finally:
            connection.close()
            await connection.wait_closed()

    async def preflight(self) -> SshPreflight:
        proof: dict[str, str] = {}
        try:
            credentials = self._credential()
            async with self._connection(credentials, proof) as connection:
                key = connection.get_server_host_key()
                if key is None or key.get_fingerprint("sha256") != self.policy.expected_host_key_sha256:
                    raise SshConnectionError("ssh_host_key_rejected")
                return SshPreflight("authenticated", self.auth_method, True, key.get_fingerprint("sha256"))
        except (OSError, ValueError, asyncssh.Error, TimeoutError) as error:
            return SshPreflight("blocked", self.auth_method, bool(proof), proof.get("host_key_sha256"), error_code=_failure_code(error))

    async def run(self, remote_argv: tuple[str, ...], *, timeout_seconds: float) -> TransportResult:
        if not remote_argv or any("\x00" in token for token in remote_argv):
            raise ValueError("Remote command requires nonempty NUL-free arguments")
        self._validate_timeout(timeout_seconds)
        try:
            credentials = self._credential()
            if any(credentials._redact(token) != token for token in remote_argv):
                raise SshConnectionError("ssh_credentials_prohibited_in_command")
            async with asyncio.timeout(timeout_seconds), self._connection(credentials) as connection:
                process: asyncssh.SSHClientProcess[bytes] = await connection.create_process(
                    shlex.join(remote_argv), encoding=None, request_pty=False, env={}, send_env=(),
                )
                async with process:
                    process.stdin.write_eof()
                    total = 0
                    async def read_bounded(reader: asyncssh.SSHReader[bytes]) -> bytes:
                        nonlocal total
                        parts: list[bytes] = []
                        while chunk := await reader.read(16_384):
                            total += len(chunk)
                            if total > self.policy.max_output_bytes:
                                raise SshConnectionError("ssh_output_limit_exceeded")
                            parts.append(chunk)
                        return b"".join(parts)
                    readers = (asyncio.create_task(read_bounded(process.stdout)), asyncio.create_task(read_bounded(process.stderr)))
                    try:
                        stdout, stderr = await asyncio.gather(*readers)
                    finally:
                        for reader in readers:
                            if not reader.done():
                                reader.cancel()
                        await asyncio.gather(*readers, return_exceptions=True)
                    await process.wait_closed()
                    code = process.exit_status if process.exit_status is not None else 255
            return TransportResult(tuple(credentials._redact(item) for item in remote_argv), code,
                credentials._redact(stdout.decode("utf-8", errors="replace")),
                credentials._redact(stderr.decode("utf-8", errors="replace")))
        except (OSError, ValueError, asyncssh.Error, TimeoutError) as error:
            # Do not expose exception text: key loaders and server diagnostics
            # may include paths or authentication material. No SSH secret argv.
            return TransportResult(("ssh", "remote-command"), 255, stderr=_failure_code(error))

    async def upload(self, local_path: Path, remote_path: str, *, timeout_seconds: float) -> TransportResult:
        return await self._transfer(local_path, remote_path, upload=True, timeout_seconds=timeout_seconds)

    async def download(self, remote_path: str, local_path: Path, *, timeout_seconds: float) -> TransportResult:
        return await self._transfer(local_path, remote_path, upload=False, timeout_seconds=timeout_seconds)

    async def _transfer(self, local_path: Path, remote_path: str, *, upload: bool,
                        timeout_seconds: float) -> TransportResult:
        self._validate_timeout(timeout_seconds)
        target = PurePosixPath(remote_path)
        if not target.is_absolute() or ".." in target.parts or "\x00" in remote_path:
            raise ValueError("SFTP path must be absolute and cannot traverse parent directories")
        operation = "upload" if upload else "download"
        try:
            credentials = self._credential()
            if credentials._redact(remote_path) != remote_path:
                raise SshConnectionError("ssh_credentials_prohibited_in_path")
            local_path = _plain_local_path(local_path, allow_missing=not upload)
            async with asyncio.timeout(timeout_seconds), self._connection(credentials) as connection:
                async with connection.start_sftp_client() as sftp:
                    await _plain_remote_path(sftp, target, allow_missing=upload)
                    if upload:
                        await sftp.put(local_path, remote_path, follow_symlinks=False)
                    else:
                        # Never let SFTP follow a local destination symlink.
                        # Write an owned regular file, then atomically publish;
                        # failed/partial transfers cannot replace prior data.
                        descriptor, temporary = tempfile.mkstemp(prefix=".mars-ssh-download-", dir=local_path.parent)
                        try:
                            with os.fdopen(descriptor, "wb") as output:
                                async with sftp.open(remote_path, "rb", encoding=None) as source:
                                    while True:
                                        chunk: bytes = await source.read(65_536)
                                        if not chunk:
                                            break
                                        output.write(chunk)
                                output.flush()
                                os.fsync(output.fileno())
                            _plain_local_path(local_path, allow_missing=True)
                            os.replace(temporary, local_path)
                        finally:
                            Path(temporary).unlink(missing_ok=True)
            return TransportResult(("sftp", operation), 0)
        except (OSError, ValueError, asyncssh.Error, TimeoutError) as error:
            return TransportResult(("sftp", operation), 255, stderr=_failure_code(error))

    @staticmethod
    def _validate_timeout(timeout_seconds: float) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("SSH operation timeout must be positive and finite")


def _plain_local_path(path: Path, *, allow_missing: bool) -> Path:
    """Reject stable symlinks, directories-as-files and missing parents."""
    # abspath normalizes relative paths without resolving away evidence of a
    # symlink. This is a file transport guard, not a filesystem jail against
    # concurrent replacement of an ancestor by another same-privilege process.
    absolute = Path(os.path.abspath(path))
    for part in reversed((absolute, *absolute.parents)):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            if part == absolute and allow_missing:
                return absolute
            raise SshConnectionError("ssh_local_transfer_path_unavailable") from None
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) if part == absolute else stat.S_ISDIR(mode)):
            raise SshConnectionError("ssh_local_transfer_path_unsafe")
    return absolute


async def _plain_remote_path(sftp: asyncssh.SFTPClient, path: PurePosixPath, *, allow_missing: bool) -> None:
    for part in reversed((path, *path.parents)):
        try:
            attributes = await sftp.lstat(str(part))
        except (asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath):
            if part == path and allow_missing:
                return
            raise SshConnectionError("ssh_remote_transfer_path_unavailable") from None
        mode = attributes.permissions
        if mode is None or stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) if part == path else stat.S_ISDIR(mode)):
            raise SshConnectionError("ssh_remote_transfer_path_unsafe")
