"""Resolve a verified project-local runtime without editing user configuration."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def confined_path(root: Path, relative: str) -> Path:
    """Reject absolute paths, traversal, alternate streams and directory links out."""
    parts = relative.replace("\\", "/").split("/")
    if not relative or any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise ValueError("Runtime path must be a confined relative path")
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Runtime path escapes its installation directory")
    return path


@dataclass(frozen=True)
class RuntimeArtifact:
    schema_version: int
    release: str
    url: str
    sha256: str
    entrypoint: str
    entrypoint_sha256: str
    max_download_bytes: int
    max_unpack_bytes: int
    max_members: int

    @classmethod
    def load(cls, root: Path) -> RuntimeArtifact:
        raw = yaml.safe_load((root / "configs/zcode_runtime.yaml").read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid managed ZCode manifest")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise ValueError("Unsupported managed ZCode manifest version")
        for name in ("release", "url", "sha256", "entrypoint", "entrypoint_sha256"):
            if not isinstance(raw[name], str) or not raw[name]:
                raise ValueError(f"Invalid ZCode manifest field: {name}")
        for name in ("sha256", "entrypoint_sha256"):
            if not re.fullmatch(r"[a-f0-9]{64}", raw[name]):
                raise ValueError(f"Invalid ZCode checksum: {name}")
        for name in ("max_download_bytes", "max_unpack_bytes", "max_members"):
            if type(raw[name]) is not int or raw[name] <= 0:
                raise ValueError(f"Invalid ZCode archive limit: {name}")
        url = urlsplit(raw["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("ZCode runtime download requires an HTTPS URL")
        confined_path(root, raw["entrypoint"])
        return cls(**raw)

    def paths(self, root: Path) -> tuple[Path, Path]:
        native = yaml.safe_load((root / "configs/windows_native.yaml").read_text(encoding="utf-8"))
        if not isinstance(native, dict) or not isinstance(native.get("node_executable"), str):
            raise ValueError("Windows Node runtime is not configured")
        node = confined_path(root, "local/windows/tools/node/" + native["node_executable"])
        entry = confined_path(root, f"local/windows/tools/zcode/{self.sha256}/{self.entrypoint}")
        return node, entry


def managed_command(root: Path) -> tuple[str, ...] | None:
    receipt = root / "local/windows/zcode-runtime.json"
    if not receipt.is_file():
        return None
    try:
        spec = RuntimeArtifact.load(root)
        data = json.loads(receipt.read_text(encoding="utf-8"))
        node, entry = spec.paths(root)
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or data.get("archive_sha256") != spec.sha256
                or data.get("entrypoint_sha256") != spec.entrypoint_sha256
                or data.get("release") != spec.release or data.get("protocol") != "app-server"):
            raise ValueError("Managed ZCode installation does not match the pinned release")
        if not node.is_file() or not entry.is_file() or file_sha256(entry) != spec.entrypoint_sha256:
            raise ValueError("Managed ZCode runtime is missing or changed")
        return str(node), str(entry)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError("项目内 ZCode 需要修复：请重新运行 Windows 启动或安装入口。") from exc
