"""Actual temporary-file discovery; these checks never execute a model runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


def _workspace(root: Path) -> Path:
    source = Path(__file__).resolve().parents[3]
    (root / "configs").mkdir(parents=True)
    for name in ("agents.yaml", "local_runtime.yaml", "cli_research.yaml", "windows_native.yaml", "zcode.yaml"):
        shutil.copyfile(source / "configs" / name, root / "configs" / name)
    (root / "templates/artifacts").mkdir(parents=True)
    (root / "backend/app/harness/schema/schemas").mkdir(parents=True)
    return source


def _portable(root: Path, relative: str) -> tuple[str, str]:
    tools = root / relative / "tools"
    script = tools / "zcode/glm/zcode.cjs"
    # Path discovery checks actual files; no claim is made that this fixture
    # implements the ZCode protocol or can make a model request.
    script.parent.mkdir(parents=True)
    script.write_text("// discovery-only text fixture\n", encoding="utf-8")
    node = tools / "node/node-v22.23.2-win-x64/node.exe"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"discovery-only file, not an executable runtime\n")
    return str(node), str(script)


def _discover(root: Path, source: Path, *, command: tuple[str, ...] = (), path: str = "") -> tuple[str, ...]:
    environment = dict(os.environ)
    environment.update(MARS_RUNTIME_ROOT=str(root), PYTHONPATH=str(source / "backend"), PATH=path)
    code = (
        "from dataclasses import replace; import json,sys; "
        "from app.harness.agent_loop.zcode.config import ZCodeConfig; "
        "print(json.dumps(replace(ZCodeConfig.load(), command=tuple(json.loads(sys.argv[1]))).resolve_command()))"
    )
    result = subprocess.run([sys.executable, "-c", code, json.dumps(command)], env=environment,
                            capture_output=True, text=True, check=True, timeout=30)
    value: object = json.loads(result.stdout)
    assert isinstance(value, list) and all(isinstance(part, str) for part in value)
    return tuple(value)


@pytest.mark.parametrize("relative", ["dependencies/windows", "local/windows"])
def test_discovers_actual_portable_files_in_new_and_legacy_layout(tmp_path: Path, relative: str) -> None:
    source = _workspace(tmp_path)
    expected = _portable(tmp_path, relative)
    assert _discover(tmp_path, source) == expected


def test_new_dependency_layout_precedes_legacy_runtime(tmp_path: Path) -> None:
    source = _workspace(tmp_path)
    _portable(tmp_path, "local/windows")
    expected = _portable(tmp_path, "dependencies/windows")
    assert _discover(tmp_path, source) == expected


def test_explicit_and_path_commands_keep_priority_over_portable_files(tmp_path: Path) -> None:
    source = _workspace(tmp_path)
    _portable(tmp_path, "dependencies/windows")
    assert _discover(tmp_path, source, command=(sys.executable, "--version")) == (sys.executable, "--version")
    directory = tmp_path / "command-bin"
    directory.mkdir()
    name = "zcode.exe" if os.name == "nt" else "zcode"
    selected = directory / name
    shutil.copyfile(sys.executable, selected)
    selected.chmod(0o755)
    assert _discover(tmp_path, source, path=str(directory)) == (str(selected),)
