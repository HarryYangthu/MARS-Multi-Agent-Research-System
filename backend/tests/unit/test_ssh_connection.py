"""Real local OpenSSH authentication/transfer and failure tests, no GPU claims."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import getpass
import json
import os
from pathlib import Path
import pickle
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from typing import Any, cast

import asyncssh
import pytest
import yaml

from app.execution.remote.executor import RemoteExecutor, RemoteExecutorConfig, load_remote_executor_config
from app.execution.remote.ssh_transport import AsyncSshTransport, SshConnectionPolicy, SshCredentials


@dataclass(frozen=True)
class LocalSshd:
    root: Path
    port: int
    key: Path
    encrypted_key: Path
    passphrase: str
    fingerprint: str
    wrong_fingerprint: str
    known_hosts: Path
    wrong_hosts: Path
    revoked_hosts: Path
    log: Path

    def policy(self, **changes: Any) -> SshConnectionPolicy:
        return replace(SshConnectionPolicy(host="127.0.0.1", port=self.port, user=getpass.getuser(),
            known_hosts_path=self.known_hosts, expected_host_key_sha256=self.fingerprint,
            connect_timeout_seconds=3), **changes)

    def transport(self, **changes: Any) -> AsyncSshTransport:
        return AsyncSshTransport(self.policy(**changes), auth_method="key", credentials=SshCredentials("key", key_path=self.key))


@pytest.fixture(scope="module")
def sshd(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LocalSshd]:
    binary = shutil.which("sshd") or ("/usr/sbin/sshd" if Path("/usr/sbin/sshd").is_file() else None)
    if binary is None:
        pytest.skip("actual local OpenSSH sshd unavailable; no SSH success substitute")
    root = tmp_path_factory.mktemp("actual-local-sshd")
    host = asyncssh.generate_private_key("ssh-ed25519")
    wrong = asyncssh.generate_private_key("ssh-ed25519")
    client = asyncssh.generate_private_key("ssh-ed25519")
    key = root / "client_key"
    encrypted = root / "encrypted_client_key"
    passphrase = secrets.token_urlsafe(32)
    for path, data in ((root / "host_key", host.export_private_key()), (key, client.export_private_key()),
                       (encrypted, client.export_private_key(passphrase=passphrase)),
                       (root / "authorized_keys", client.export_public_key())):
        path.write_bytes(data)
        path.chmod(0o600)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    host_line = f"[127.0.0.1]:{port} " + host.export_public_key().decode()
    known, wrong_known, revoked = root / "known_hosts", root / "wrong_known_hosts", root / "revoked_hosts"
    known.write_text(host_line)
    wrong_known.write_text(f"[127.0.0.1]:{port} " + wrong.export_public_key().decode())
    revoked.write_text(host_line + "@revoked " + host_line)
    configuration = root / "sshd_config"
    configuration.write_text(f"""Port {port}
ListenAddress 127.0.0.1
HostKey {root}/host_key
PidFile {root}/pid
AuthorizedKeysFile {root}/authorized_keys
UsePAM no
PasswordAuthentication yes
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers {getpass.getuser()}
LogLevel VERBOSE
Subsystem sftp internal-sftp
AllowTcpForwarding no
X11Forwarding no
""")
    log = root / "sshd.log"
    with log.open("wb") as output:
        process = subprocess.Popen([binary, "-D", "-e", "-f", str(configuration)], stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=output)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.skip("actual sshd cannot start unprivileged on this platform; inspect local daemon configuration")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.02)
        else:
            pytest.fail("actual sshd did not open its owned loopback listener")
        yield LocalSshd(root, port, key, encrypted, passphrase, host.get_fingerprint("sha256"),
                        wrong.get_fingerprint("sha256"), known, wrong_known, revoked, log)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        assert passphrase not in log.read_text()


@pytest.mark.asyncio
async def test_real_key_authentication_and_fixed_command(sshd: LocalSshd) -> None:
    transport = sshd.transport()
    assert transport.local_findings() == ()
    receipt = await transport.preflight()
    assert receipt.status == "authenticated" and receipt.host_key_verified
    assert receipt.scope == "ssh_connection_only" and receipt.host_key_sha256 == sshd.fingerprint
    command = await transport.run(("/usr/bin/id", "-u"), timeout_seconds=3)
    assert command.returncode == 0 and command.stdout.strip() == str(os.getuid())
    assert str(sshd.key) not in repr(command.argv)


@pytest.mark.asyncio
async def test_encrypted_private_key_passphrase_stays_in_connection_layer(sshd: LocalSshd) -> None:
    credentials = SshCredentials("key", key_path=sshd.encrypted_key, passphrase=sshd.passphrase)
    transport = AsyncSshTransport(sshd.policy(), auth_method="key", credentials=credentials)
    receipt = await transport.preflight()
    assert receipt.status == "authenticated"
    assert sshd.passphrase not in repr(credentials) + repr(transport) + repr(receipt) + json.dumps(asdict(receipt))
    refused = AsyncSshTransport(sshd.policy(), auth_method="key", credentials=SshCredentials("key", key_path=sshd.encrypted_key,
                                                                                         passphrase="incorrect-passphrase"))
    failure = await refused.preflight()
    assert failure.status == "blocked" and "incorrect-passphrase" not in repr(failure)


@pytest.mark.parametrize("damage", ["pin", "known_hosts", "revoked", "empty", "missing"])
@pytest.mark.asyncio
async def test_host_trust_failure_precedes_authentication(sshd: LocalSshd, tmp_path: Path, damage: str) -> None:
    changes: dict[str, Any] = {}
    if damage == "pin":
        changes["expected_host_key_sha256"] = sshd.wrong_fingerprint
    elif damage == "known_hosts":
        changes["known_hosts_path"] = sshd.wrong_hosts
    elif damage == "revoked":
        changes["known_hosts_path"] = sshd.revoked_hosts
    else:
        changes["known_hosts_path"] = tmp_path / "hosts"
        if damage == "empty":
            changes["known_hosts_path"].write_text("")
    # A generated, invalid password is never an OS user's credential. In these
    # cases it must not even reach the server's authentication phase.
    password = secrets.token_urlsafe(32)
    before = sshd.log.read_text().count("Failed password")
    transport = AsyncSshTransport(sshd.policy(**changes), auth_method="password",
                                 credentials=SshCredentials("password", password=password))
    receipt = await transport.preflight()
    assert receipt.status == "blocked" and receipt.host_key_verified is False
    assert receipt.error_code in {"ssh_host_key_rejected", "ssh_known_hosts_unavailable"}
    assert sshd.log.read_text().count("Failed password") == before
    assert password not in repr(receipt) + repr(transport) + sshd.log.read_text()


@pytest.mark.asyncio
async def test_password_authentication_uses_real_rejection_without_key_fallback(sshd: LocalSshd) -> None:
    password = secrets.token_urlsafe(32)
    # Unknown OS account: never attempt a guessed password against the user.
    transport = AsyncSshTransport(sshd.policy(user="mars_absent_" + secrets.token_hex(4)), auth_method="password",
                                 credentials=SshCredentials("password", password=password))
    receipt = await transport.preflight()
    assert receipt.status == "blocked" and receipt.error_code == "ssh_authentication_rejected"
    assert receipt.host_key_verified is True and receipt.host_key_sha256 == sshd.fingerprint
    assert password not in repr(receipt) + sshd.log.read_text()


@pytest.mark.asyncio
async def test_remote_command_path_failure_and_shell_metacharacters_are_literal(sshd: LocalSshd) -> None:
    transport = sshd.transport()
    missing = await transport.run((str(sshd.root / "no executable"),), timeout_seconds=3)
    assert missing.returncode != 0
    tripwire = sshd.root / "must_not_execute"
    argument = "literal ; touch " + str(tripwire)
    result = await transport.run(("/usr/bin/printf", "%s", argument), timeout_seconds=3)
    assert result.returncode == 0 and result.stdout == argument and not tripwire.exists()


@pytest.mark.asyncio
async def test_actual_sftp_roundtrip_handles_spaces_without_shell(sshd: LocalSshd, tmp_path: Path) -> None:
    transport = sshd.transport()
    source = tmp_path / "input data"
    source.write_bytes(b"actual SFTP input bytes\n")
    remote = sshd.root / "remote data;literal"
    uploaded = await transport.upload(source, str(remote), timeout_seconds=3)
    assert uploaded.returncode == 0 and remote.read_bytes() == source.read_bytes()
    destination = tmp_path / "downloaded data"
    downloaded = await transport.download(str(remote), destination, timeout_seconds=3)
    assert downloaded.returncode == 0 and destination.read_bytes() == source.read_bytes()
    failed = await transport.download(str(sshd.root / "missing-data"), tmp_path / "absent", timeout_seconds=3)
    assert failed.returncode != 0


@pytest.mark.parametrize("location", ["remote_source", "remote_parent", "local_target", "local_parent"])
@pytest.mark.asyncio
async def test_download_rejects_real_symlinks_without_overwriting_target(sshd: LocalSshd, tmp_path: Path, location: str) -> None:
    transport = sshd.transport()
    assert (await transport.preflight()).status == "authenticated"
    source = sshd.root / ("download-" + secrets.token_hex(6))
    source.write_text("actual regular remote payload")
    protected = tmp_path / "protected.txt"
    protected.write_text("protected original bytes")
    destination = tmp_path / "received.txt"
    if location == "remote_source":
        alias = source.with_name(source.name + "-link")
        alias.symlink_to(source)
        source = alias
    elif location == "remote_parent":
        alias = source.parent / ("directory-link-" + secrets.token_hex(6))
        alias.symlink_to(source.parent, target_is_directory=True)
        source = alias / source.name
    elif location == "local_target":
        destination.symlink_to(protected)
    else:
        alias = tmp_path / "directory-link"
        alias.symlink_to(tmp_path, target_is_directory=True)
        destination = alias / protected.name
    result = await transport.download(str(source), destination, timeout_seconds=3)
    assert result.returncode != 0
    assert protected.read_text() == "protected original bytes"
    assert not list(tmp_path.glob(".mars-ssh-download-*"))
    if location.startswith("remote"):
        assert not destination.exists() and not destination.is_symlink()


@pytest.mark.parametrize("location", ["local_source", "local_parent", "remote_target", "remote_parent"])
@pytest.mark.asyncio
async def test_upload_rejects_real_symlinks_without_overwriting_target(sshd: LocalSshd, tmp_path: Path, location: str) -> None:
    transport = sshd.transport()
    assert (await transport.preflight()).status == "authenticated"
    source = tmp_path / "source.txt"
    source.write_text("actual upload payload")
    destination = sshd.root / ("upload-" + secrets.token_hex(6))
    protected = destination.with_name(destination.name + "-protected")
    protected.write_text("protected remote bytes")
    if location == "local_source":
        alias = tmp_path / "source-link"
        alias.symlink_to(source)
        source = alias
    elif location == "local_parent":
        alias = tmp_path / "directory-link"
        alias.symlink_to(tmp_path, target_is_directory=True)
        source = alias / source.name
    elif location == "remote_target":
        destination.symlink_to(protected)
    else:
        alias = destination.parent / ("directory-link-" + secrets.token_hex(6))
        alias.symlink_to(destination.parent, target_is_directory=True)
        destination = alias / protected.name
    result = await transport.upload(source, str(destination), timeout_seconds=3)
    assert result.returncode != 0
    assert protected.read_text() == "protected remote bytes"


@pytest.mark.asyncio
async def test_actual_command_output_and_wall_time_are_bounded(sshd: LocalSshd) -> None:
    output = await sshd.transport(max_output_bytes=8).run(("/usr/bin/printf", "abcdefghijk"), timeout_seconds=3)
    assert output.returncode != 0 and output.stderr == "ssh_output_limit_exceeded"
    began = time.monotonic()
    timed = await sshd.transport().run(("/bin/sleep", "1"), timeout_seconds=0.15)
    assert timed.returncode != 0 and timed.stderr == "ssh_timeout_outcome_unknown"
    assert time.monotonic() - began < 3


@pytest.mark.asyncio
async def test_unavailable_loopback_listener_never_reports_authentication(tmp_path: Path) -> None:
    key = asyncssh.generate_private_key("ssh-ed25519")
    known = tmp_path / "known_hosts"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    known.write_text(f"[127.0.0.1]:{port} " + key.export_public_key().decode())
    policy = SshConnectionPolicy("127.0.0.1", port, "mars", known, key.get_fingerprint("sha256"), 1)
    transport = AsyncSshTransport(policy, auth_method="password", credentials=SshCredentials("password", password="not-a-real-account"))
    receipt = await transport.preflight()
    assert receipt.status == "blocked" and receipt.host_key_verified is False
    # A real absent listener can be refused or silently dropped by the host
    # firewall. Both remain unsuccessful, with no fabricated authentication.
    assert receipt.error_code in {"ssh_network_or_local_io_failed", "ssh_timeout_outcome_unknown"}


@pytest.mark.asyncio
async def test_untrusted_client_key_is_rejected_after_host_verification(sshd: LocalSshd, tmp_path: Path) -> None:
    key = tmp_path / "unauthorized_key"
    key.write_bytes(asyncssh.generate_private_key("ssh-ed25519").export_private_key())
    key.chmod(0o600)
    transport = AsyncSshTransport(sshd.policy(), auth_method="key", credentials=SshCredentials("key", key_path=key))
    receipt = await transport.preflight()
    assert receipt.status == "blocked" and receipt.host_key_verified is True
    assert receipt.error_code == "ssh_authentication_rejected"


@pytest.mark.asyncio
async def test_credentials_rejected_in_command_and_redacted_in_actual_output(sshd: LocalSshd, tmp_path: Path) -> None:
    credentials = SshCredentials("key", key_path=sshd.encrypted_key, passphrase=sshd.passphrase)
    transport = AsyncSshTransport(sshd.policy(), auth_method="key", credentials=credentials)
    result = await transport.run(("/usr/bin/printf", "%s", sshd.passphrase), timeout_seconds=3)
    assert result.returncode != 0 and result.stderr == "ssh_credentials_prohibited_in_command"
    assert sshd.passphrase not in repr(result)
    # A genuine file and genuine cat process exercise stream redaction, not a
    # fabricated transport result. The test secret is never a command argument.
    source = tmp_path / "test-only-sensitive-content"
    source.write_text(sshd.passphrase)
    result = await transport.run(("/bin/cat", str(source)), timeout_seconds=3)
    assert result.returncode == 0 and result.stdout == "[REDACTED]"
    assert sshd.passphrase not in repr(result) + sshd.log.read_text()


def test_private_key_permissions_are_checked_on_real_file(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_bytes(asyncssh.generate_private_key("ssh-ed25519").export_private_key())
    key.chmod(0o644)
    if os.name == "nt":
        pytest.skip("POSIX permission boundary does not apply on Windows")
    with pytest.raises(ValueError, match="ssh_private_key_permissions_unsafe"):
        SshCredentials("key", key_path=key)._arguments()
    key.chmod(0o600)
    assert SshCredentials("key", key_path=key)._arguments()["public_key_auth"] is True


@pytest.mark.parametrize("changes", [
    {"host": "host; touch leak"}, {"user": "user\npassword"}, {"port": True}, {"port": 0},
    {"expected_host_key_sha256": "invalid-secret"}, {"connect_timeout_seconds": float("nan")},
    {"connect_timeout_seconds": float("inf")}, {"connect_timeout_seconds": 0}, {"max_output_bytes": True},
])
def test_connection_policy_rejects_unsafe_configuration(tmp_path: Path, changes: dict[str, Any]) -> None:
    policy = SshConnectionPolicy("127.0.0.1", 22, "mars", tmp_path / "hosts",
                                asyncssh.generate_private_key("ssh-ed25519").get_fingerprint("sha256"))
    with pytest.raises(ValueError):
        replace(policy, **changes)


def test_credentials_are_not_serializable_or_loaded_from_dotenv(tmp_path: Path) -> None:
    secret = secrets.token_urlsafe(32)
    credentials = SshCredentials.from_environment("password", environ={"MARS_REMOTE_SSH_PASSWORD": " " + secret + " "})
    assert secret not in repr(credentials)
    assert credentials._arguments()["password"] == " " + secret + " "
    assert credentials._redact("echo: " + secret) == "echo: " + secret  # whitespace is part of this password
    assert credentials._redact("echo:  " + secret + " ") == "echo: [REDACTED]"
    for encode in (pickle.dumps, json.dumps, yaml.safe_dump, asdict):
        with pytest.raises((TypeError, yaml.representer.RepresenterError)):
            encode(cast(Any, credentials))  # Deliberately invalid serializer input.
    (tmp_path / ".env").write_text("MARS_REMOTE_SSH_PASSWORD=" + secret)
    with pytest.raises(ValueError):
        SshCredentials.from_environment("password", environ={})


def test_asyncssh_configuration_has_only_references_and_requires_explicit_selection(tmp_path: Path) -> None:
    configuration = tmp_path / "execution.yaml"
    configuration.write_text("""execution:
  remote_gpu:
    transport: asyncssh
    auth_method: password
    env:
      host: SSH_HOST
      user: SSH_USER
      known_hosts: SSH_KNOWN
      host_key_sha256: SSH_PIN
      remote_root: SSH_ROOT
""")
    pin = asyncssh.generate_private_key("ssh-ed25519").get_fingerprint("sha256")
    config = load_remote_executor_config(configuration, environ={"SSH_HOST": "127.0.0.1", "SSH_USER": "mars",
        "SSH_KNOWN": str(tmp_path / "hosts"), "SSH_PIN": pin, "SSH_ROOT": "/srv/mars"})
    assert config.auth_method == "password" and config.transport == "asyncssh"
    assert "MARS_REMOTE_SSH_KEY_PATH" not in config.missing_fields()
    assert set(asdict(config)).isdisjoint({"password", "passphrase", "credentials"})
    assert RemoteExecutorConfig().transport == "system_ssh"
    with pytest.raises(ValueError):
        RemoteExecutorConfig(auth_method="password")
    configuration.write_text("execution:\n  remote_gpu:\n    password: secret-must-not-load\n")
    with pytest.raises(ValueError) as failure:
        load_remote_executor_config(configuration, environ={})
    assert "secret-must-not-load" not in str(failure.value)


@pytest.mark.asyncio
async def test_existing_executor_can_select_new_transport_without_gpu_success_claim(sshd: LocalSshd) -> None:
    config = RemoteExecutorConfig(enabled=True, transport="asyncssh", auth_method="key", host="127.0.0.1",
        port=sshd.port, user=getpass.getuser(), key_path=sshd.key, known_hosts_path=sshd.known_hosts,
        host_key_sha256=sshd.fingerprint, remote_root=str(sshd.root), python="/missing/mars-python")
    report = await RemoteExecutor(config).readiness()
    assert report.status == "blocked" and report.error_code == "remote_runner_unavailable"
