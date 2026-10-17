"""Install and probe the pinned ZCode app-server; never submit a model request."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path, PureWindowsPath
import sys
import tarfile
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from loguru import logger

from app.harness.agent_loop.trace import atomic_json
from app.harness.agent_loop.zcode.config import ZCodeConfig, model_config, runtime_environment
from app.harness.agent_loop.zcode.managed_runtime import RuntimeArtifact, confined_path, file_sha256
from app.harness.agent_loop.zcode.protocol import ZCodeClient


def download_artifact(spec: RuntimeArtifact, downloads: Path) -> Path:
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / f"zcode-{spec.sha256}.tar.gz"
    if archive.is_file():
        if file_sha256(archive) != spec.sha256:
            raise ValueError(f"ZCode cached archive checksum mismatch: {archive}")
        return archive
    logger.info("Downloading official ZCode {} runtime", spec.release)
    with tempfile.NamedTemporaryFile(dir=downloads, suffix=".part", delete=False) as output:
        temporary = Path(output.name)
        try:
            with urlopen(spec.url, timeout=60) as response:
                if not response.geturl().startswith("https://"):
                    raise ValueError("ZCode download redirected away from HTTPS")
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > spec.max_download_bytes:
                        raise ValueError("ZCode download exceeds the manifest size limit")
                    output.write(chunk)
            output.close()
            if file_sha256(temporary) != spec.sha256:
                raise ValueError("ZCode download checksum mismatch")
            temporary.replace(archive)
        finally:
            output.close()
            temporary.unlink(missing_ok=True)
    return archive


def extract_artifact(spec: RuntimeArtifact, archive: Path, destination: Path) -> None:
    """Validate the entire tar before writing any member, including on Windows."""
    if file_sha256(archive) != spec.sha256:
        raise ValueError("ZCode archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > spec.max_members or sum(member.size for member in members) > spec.max_unpack_bytes:
            raise ValueError("ZCode archive exceeds the manifest extraction limits")
        seen: set[str] = set()
        for member in members:
            if not (member.isfile() or member.isdir()) or member.issparse():
                raise ValueError("ZCode archive must contain only ordinary files and directories")
            name = member.name.rstrip("/")
            if "\\" in name or any(part.endswith((".", " ")) or PureWindowsPath(part).is_reserved()
                                    for part in name.split("/")):
                raise ValueError("Invalid Windows archive path")
            target = confined_path(destination, name)
            normalized = str(target).casefold()
            if normalized in seen:
                raise ValueError("Duplicate ZCode archive path")
            seen.add(normalized)
        bundle.extractall(destination, members=members, filter="data")
    if file_sha256(confined_path(destination, spec.entrypoint)) != spec.entrypoint_sha256:
        raise ValueError("ZCode entry point checksum mismatch")


async def probe_runtime(command: tuple[str, ...], config: ZCodeConfig, directory: Path) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zcode-check-", dir=directory) as name:
        root = Path(name)
        # A real app-server handshake with no model invocation or external credentials.
        provider = model_config("installation-probe", 8, "http://127.0.0.1:1", "protocol-only", context_window=4096)
        client = ZCodeClient(command=command, cwd=root, env=runtime_environment(root, provider=provider),
                             timeout=config.rpc_timeout_seconds, max_line_bytes=config.max_protocol_line_bytes)
        try:
            await client.start()
            result = await client.call("runtime/capabilities", {})
            if not isinstance(result, dict):
                raise ValueError("ZCode returned invalid app-server capabilities")
            return sorted(result)
        finally:
            await client.close()


def ensure_runtime(root: Path = ROOT) -> None:
    runtime = root / "local/windows"
    config = ZCodeConfig.load(root / "configs/zcode.yaml")
    if config.command:
        # Explicit team/user configuration takes precedence; never overwrite it.
        asyncio.run(probe_runtime(config.resolve_command(), config, runtime / "checks"))
        logger.info("Configured ZCode app-server handshake passed")
        return
    spec = RuntimeArtifact.load(root)
    node, entry = spec.paths(root)
    if not node.is_file():
        raise ValueError("Install the project's Windows Node runtime before ZCode")
    target = confined_path(root, f"local/windows/tools/zcode/{spec.sha256}")
    if not entry.is_file() or file_sha256(entry) != spec.entrypoint_sha256:
        archive = download_artifact(spec, runtime / "downloads")
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".install-", dir=parent) as name:
            staging = Path(name)
            extract_artifact(spec, archive, staging)
            asyncio.run(probe_runtime((str(node), str(staging / spec.entrypoint)), config, runtime / "checks"))
            ready = entry.is_file() and file_sha256(entry) == spec.entrypoint_sha256
            if target.exists() and not ready:
                # Preserve the damaged installation for inspection, inside this tool directory.
                quarantine = parent / f"{spec.sha256}.previous-{os.urandom(6).hex()}"
                if target.resolve().parent != parent.resolve() or quarantine.resolve().parent != parent.resolve():
                    raise ValueError("Unsafe ZCode replacement path")
                target.rename(quarantine)
            if not ready:
                staging.rename(target)
    capabilities = asyncio.run(probe_runtime((str(node), str(entry)), config, runtime / "checks"))
    atomic_json(runtime / "zcode-runtime.json", {"schema_version": 1, "release": spec.release,
        "archive_sha256": spec.sha256, "entrypoint_sha256": spec.entrypoint_sha256,
        "protocol": "app-server", "capability_keys": capabilities})
    logger.info("ZCode {} ready: official runtime, Windows Node, app-server verified", spec.release)


if __name__ == "__main__":
    ensure_runtime()
