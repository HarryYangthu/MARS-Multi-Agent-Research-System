"""Real AI Native detection, binding roles and job records; no fakes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.bridge import ainative_service as service
from app.bridge.project_onboarding import bind_code_folder
from app.harness.project_workspace import open_folder


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proj-root"
    (root / ".mars").mkdir(parents=True)
    monkeypatch.setattr(service, "project_root", lambda name: root / ".mars")
    monkeypatch.setattr(service, "_project_base", lambda name: root)
    return root


def _write_minimal_repo(root: Path) -> Path:
    repo = root / "plain-repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')\n")
    return repo


def test_detect_real_pimc_repo_is_native() -> None:
    repo = Path(__file__).resolve().parents[3] / "workspace/repos/pimc"
    if not repo.is_dir():
        pytest.skip("workspace pimc repo not present on this machine")
    report = service.detect_ainative(repo)
    assert report["is_native"] is True, report
    assert all(check["passed"] for check in report["checks"])


def test_detect_plain_repo_lists_missing_standards(tmp_path: Path) -> None:
    repo = _write_minimal_repo(tmp_path)
    report = service.detect_ainative(repo)
    assert report["is_native"] is False
    failed = {check["name"] for check in report["checks"] if not check["passed"]}
    assert {"torch_dependency", "agents_md", "tensorboard_integration", "checkpoint_mechanism"} <= failed


def test_detect_generated_torch_repo_is_native(tmp_path: Path) -> None:
    repo = tmp_path / "generated"
    (repo / "libs").mkdir(parents=True)
    (repo / "configs").mkdir()
    (repo / "train.py").write_text(
        "import torch\nimport torch.nn as nn\n"
        "from torch.utils.tensorboard import SummaryWriter\n"
        "class Net(nn.Module):\n"
        "    def forward(self, x):\n"
        "        # x: (B, T)\n"
        "        return x  # (B, T)\n"
        "if __name__ == '__main__':\n"
        "    torch.save({'model': Net().state_dict()}, 'best.pt')\n"
        "    SummaryWriter('runs/tb')\n")
    (repo / "configs" / "static.yaml").write_text("epochs: 1\n")
    (repo / "AGENTS.md").write_text("# 规约\n1. 冻结 forward 接口\n")
    report = service.detect_ainative(repo)
    assert report["is_native"] is True, report


def test_start_generation_skips_native_and_rejects_dirty_target(
    tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = tmp_path / "native-repo"
    (native / "libs").mkdir(parents=True)
    (native / "configs").mkdir()
    (native / "train.py").write_text(
        "import torch\nfrom torch.utils.tensorboard import SummaryWriter\n"
        "if __name__ == '__main__':\n"
        "    torch.save({'model': torch.nn.Linear(1, 1).state_dict()}, 'best.pt')\n"
        "    SummaryWriter('runs/tb')\n")
    (native / "configs" / "static.yaml").write_text("epochs: 1\n")
    (native / "AGENTS.md").write_text("# 规约\n")
    monkeypatch.setattr(service, "_bound_repo", lambda name: native)
    started = service.start_generation("proj")
    assert started["skipped"] is True
    assert started["detection"]["is_native"] is True

    plain = _write_minimal_repo(tmp_path)
    monkeypatch.setattr(service, "_bound_repo", lambda name: plain)
    target = project / "ainative"
    target.mkdir()
    (target / "user_edit.md").write_text("researcher edits")
    with pytest.raises(ValueError, match="已存在且非空"):
        service.start_generation("proj")


def test_job_record_roundtrip(project: Path) -> None:
    out = service._job_dir("proj", "b" * 32)
    out.mkdir(parents=True)
    record = {"id": "b" * 32, "project": "proj", "status": "generating",
              "created_at": "2026-09-29T10:00:00+00:00", "step": "survey", "error": "",
              "steps": [], "model_calls": []}
    from app.harness.agent_loop.trace import atomic_json
    atomic_json(out / "record.json", record)
    loaded = service.read_job("proj", "b" * 32)
    assert loaded["id"] == "b" * 32 and loaded["status"] == "interrupted"
    status = service.generation_status("proj")
    assert status["has_job"] is True and status["job"]["id"] == "b" * 32
    assert status["job"]["status"] == "interrupted"
    with pytest.raises(ValueError, match="无效"):
        service.read_job("proj", "zz")


def test_bind_code_folder_ainative_role_is_writable_and_keeps_baseline(tmp_path: Path) -> None:
    code = tmp_path / "code"
    code.mkdir()
    (code / "baseline.py").write_text("VALUE = 1\n")
    folder = open_folder(str(code), registry=tmp_path / "registry.json")
    baseline = tmp_path / "baseline-repo"
    baseline.mkdir()
    bind_code_folder(folder, str(baseline))
    generated = tmp_path / "ainative"
    generated.mkdir()
    bind_code_folder(folder, str(generated), role="ainative")
    link = yaml.safe_load((folder.metadata_root / "repo_link.yaml").read_text())
    assert link["repo_path"] == str(generated)
    assert link["repo_role"] == "ainative" and link["read_only"] is False
    assert link["baseline_repo_path"] == str(baseline)
    with pytest.raises(ValueError, match="未知"):
        bind_code_folder(folder, str(baseline), role="bogus")


def test_verify_really_executes_and_fails_loudly(tmp_path: Path) -> None:
    repo = tmp_path / "bad"
    repo.mkdir()
    (repo / "broken.py").write_text("def f(:\n")
    with pytest.raises(ValueError, match="验证步骤 compileall 失败"):
        service._verify(repo)
