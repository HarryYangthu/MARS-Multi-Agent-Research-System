"""The toy example project (examples/toy) copied into a temporary folder, with its source as a real git repository."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def make_toy(folder: Path) -> Path:
    shutil.copytree(ROOT / "examples/toy", folder)
    source = folder / "source"
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "baseline"]):
        subprocess.run(["git", *args], cwd=source, check=True)
    return folder


@pytest.fixture
def toy(tmp_path: Path, monkeypatch) -> Path:
    models = tmp_path / "models.yaml"  # independent of the machine's model configuration (none on a fresh install)
    models.write_text("providers: {}\nroles: {}\n", encoding="utf-8")
    monkeypatch.setenv("MARS_MODELS_CONFIG", str(models))
    monkeypatch.setenv("MARS_MACHINE_CONFIG", str(tmp_path / "no-machine.yaml"))
    monkeypatch.delenv("MARS_EXECUTION_ENVIRONMENT", raising=False)
    return make_toy(tmp_path / "toy project")
