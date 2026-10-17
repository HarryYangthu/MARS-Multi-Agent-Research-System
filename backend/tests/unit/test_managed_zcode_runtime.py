"""Actual temporary archives and configuration; no model or service substitutes."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
from typing import Any

import pytest
import yaml

from app.harness.agent_loop.zcode.managed_runtime import RuntimeArtifact, confined_path, file_sha256, managed_command
from app.harness.agent_loop.zcode.config import ZCodeConfig

ROOT = Path(__file__).resolve().parents[3]
MODULE_SPEC = importlib.util.spec_from_file_location("ensure_zcode", ROOT / "deploy/windows-native/ensure_zcode.py")
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
installer = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(installer)


def archive_fixture(tmp_path: Path, names: list[str], *, link: bool = False) -> tuple[RuntimeArtifact, Path]:
    archive = tmp_path / "runtime.tar.gz"
    payload = b"archive integrity fixture; never executed"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in names:
            member = tarfile.TarInfo(name)
            if link:
                member.type = tarfile.SYMTYPE
                member.linkname = "../outside"
                bundle.addfile(member)
            else:
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
    spec = RuntimeArtifact(1, "fixture", "https://example.invalid/not-requested", file_sha256(archive),
                           "zcode.cjs", hashlib.sha256(payload).hexdigest(), 1048576, 1048576, 100)
    return spec, archive


def test_official_manifest_and_launcher_include_managed_runtime() -> None:
    spec = RuntimeArtifact.load(ROOT)
    assert spec.release == "3.15.1"
    assert spec.url.startswith("https://cdn-zcode.z.ai/") and "%2B" in spec.url
    launcher = (ROOT / "deploy/windows-native/Mars.ps1").read_text(encoding="utf-8")
    install = launcher.split("function Install-NativeDependencies", 1)[1].split("function Test-InstallationCurrent", 1)[0]
    start = launcher.split("function Start-NativeServices", 1)[1].split("function Stop-NativeServices", 1)[0]
    assert "ensure_zcode.py" in install and "ensure_zcode.py" in start


def test_verified_cache_extracts_offline_and_keeps_existing_archive(tmp_path: Path) -> None:
    spec, source = archive_fixture(tmp_path, ["zcode.cjs", "THIRD-PARTY-NOTICES.md"])
    cached = tmp_path / f"zcode-{spec.sha256}.tar.gz"
    source.rename(cached)
    stamp = cached.stat().st_mtime_ns
    assert installer.download_artifact(spec, tmp_path) == cached
    assert cached.stat().st_mtime_ns == stamp
    target = tmp_path / "installed"
    installer.extract_artifact(spec, cached, target)
    assert file_sha256(target / "zcode.cjs") == spec.entrypoint_sha256
    assert (target / "THIRD-PARTY-NOTICES.md").is_file()
    cached.write_bytes(b"corrupt download")
    with pytest.raises(ValueError, match="checksum"):
        installer.download_artifact(spec, tmp_path)


@pytest.mark.parametrize("names,link", [
    (["../outside"], False), (["C:/outside"], False), (["directory\\outside"], False),
    (["zcode.cjs:stream"], False), (["CON.txt"], False), (["trailing. "], False),
    (["Case", "case"], False), (["escape"], True),
])
def test_archive_rejects_windows_escape_and_links_before_writing(tmp_path: Path, names: list[str], link: bool) -> None:
    spec, source = archive_fixture(tmp_path, names, link=link)
    target = tmp_path / "installed"
    with pytest.raises(ValueError):
        installer.extract_artifact(spec, source, target)
    assert not target.exists()
    assert not (tmp_path / "outside").exists()


def test_archive_enforces_limits_and_entry_integrity(tmp_path: Path) -> None:
    spec, source = archive_fixture(tmp_path, ["zcode.cjs", "notice"])
    with pytest.raises(ValueError, match="limits"):
        installer.extract_artifact(replace(spec, max_members=1), source, tmp_path / "count")
    with pytest.raises(ValueError, match="limits"):
        installer.extract_artifact(replace(spec, max_unpack_bytes=1), source, tmp_path / "size")
    with pytest.raises(ValueError, match="entry point checksum"):
        installer.extract_artifact(replace(spec, entrypoint_sha256="0" * 64), source, tmp_path / "hash")


@pytest.mark.parametrize("field,value", [("url", "http://example.invalid/runtime"),
    ("sha256", "invalid"), ("entrypoint", "../escape"), ("max_members", True), ("schema_version", 2)])
def test_manifest_rejects_invalid_download_contract(tmp_path: Path, field: str, value: Any) -> None:
    spec, _ = archive_fixture(tmp_path, ["zcode.cjs"])
    config = tmp_path / "configs/zcode_runtime.yaml"
    config.parent.mkdir()
    values = asdict(spec)
    values[field] = value
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    with pytest.raises(ValueError):
        RuntimeArtifact.load(tmp_path)


def test_receipt_is_portable_and_rejects_changed_entry(tmp_path: Path) -> None:
    spec, archive = archive_fixture(tmp_path, ["zcode.cjs"])
    config = tmp_path / "configs"
    config.mkdir()
    (config / "zcode_runtime.yaml").write_text(yaml.safe_dump(asdict(spec)), encoding="utf-8")
    (config / "windows_native.yaml").write_text("node_executable: node.exe\n", encoding="utf-8")
    node, entry = spec.paths(tmp_path)
    node.parent.mkdir(parents=True)
    # A real binary file for path resolution only. No child is executed by this test.
    node.write_bytes(Path(sys.executable).read_bytes())
    installer.extract_artifact(spec, archive, entry.parent)
    assert managed_command(tmp_path) is None
    receipt = tmp_path / "local/windows/zcode-runtime.json"
    receipt.write_text(json.dumps({"schema_version": 1, "release": spec.release, "archive_sha256": spec.sha256,
        "entrypoint_sha256": spec.entrypoint_sha256, "protocol": "app-server"}), encoding="utf-8")
    assert managed_command(tmp_path) == (str(node), str(entry))
    moved = tmp_path.with_name(tmp_path.name + "-moved")
    tmp_path.rename(moved)
    try:
        command = managed_command(moved)
        assert command is not None and all(str(moved) in arg for arg in command)
        Path(command[1]).write_bytes(b"changed entry")
        with pytest.raises(ValueError, match="需要修复"):
            managed_command(moved)
    finally:
        moved.rename(tmp_path)


def test_confined_path_rejects_absolute_and_traversal(tmp_path: Path) -> None:
    for path in ("../escape", "/absolute", "C:\\absolute", "a//b", "a:stream", "a/./b"):
        with pytest.raises(ValueError):
            confined_path(tmp_path, path)


def test_explicit_command_keeps_precedence_over_project_installation() -> None:
    # Resolution of an actual executable only; this test does not launch a runtime.
    config = replace(ZCodeConfig.load(), command=(sys.executable, "explicit-entry.cjs"))
    command = config.resolve_command()
    assert Path(command[0]).samefile(sys.executable)
    assert command[1] == "explicit-entry.cjs"
