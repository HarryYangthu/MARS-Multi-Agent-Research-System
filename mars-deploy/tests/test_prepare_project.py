"""Real Git repositories and project bindings exercise imports without training or model calls."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

spec = importlib.util.spec_from_file_location("prepare_project", Path(__file__).parents[1] / "install/prepare_project.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def command(root, *args):
    return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def study(tmp_path, monkeypatch):
    root = tmp_path / "研究 Proj"
    repo = root / "source"
    for name in ["src", "configs"]:
        (repo / name).mkdir(parents=True, exist_ok=True)
    (repo / "src/candidate.py").write_text("value = 1\n")
    (repo / "src/train.py").write_text("# fixture for file and Git binding checks only\n")
    (repo / "configs/base.yaml").write_text("learning_rate: 0.001\n")
    command(repo, "init", "-q")
    command(repo, "add", ".")
    command(repo, "commit", "-qm", "baseline")
    base = command(repo, "rev-parse", "HEAD")
    (repo / "src/candidate.py").write_text("value = 2\n")
    command(repo, "commit", "-qam", "candidate")
    commit = command(repo, "rev-parse", "HEAD")
    (root / "data").mkdir()
    (root / "data/capture.bin").write_bytes(b"binding fixture, never used for training")
    models = tmp_path / "models.yaml"
    models.write_text("providers: {}\nroles: {}\n")
    monkeypatch.setenv("MARS_MODELS_CONFIG", str(models))
    (root / ".mars").mkdir()
    (root / ".mars/project.yaml").write_text(yaml.safe_dump({"schema_version": 2, "name": "study", "python": sys.executable,
        "repo": {"path": "source", "commit": base, "editable_files": ["src/candidate.py"]},
        "datasets": [{"name": "main", "config": "configs/base.yaml", "data": "data/capture.bin"}],
        "evaluate": {"command": "{python} -c pass"}, "metrics": {"primary": "error", "fields": {"error": {"better": "lower"}}},
        "remote": {"enabled": False}}))
    record = root / "runs/history/step2/P00/i1/record.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"iteration": 1, "branch": "cand/history-P00-i1", "commit": commit,
                                 "task": {"summary": "imported exact code"}}))
    (root / "runs/history/contract.yaml").write_text("title: history\n")
    (root / "runs/history/report.md").write_text("Historical report retained.")
    (root / ".mars/candidate-imports.json").write_text(json.dumps({"source": "source", "candidates": [
        {"run_id": "history", "route": "P00", "iteration": 1, "commit": commit}]}))
    return root, commit


def test_exact_candidates_are_reusable_and_project_can_move(study):
    from mars import api
    root, commit = study
    assert module.prepare(root)["prepared"] == 1
    candidates = api.route_summaries("history", project_root=root)[0]["iterations"]
    directory = Path(candidates[0]["candidate_dir"])
    assert command(directory, "rev-parse", "HEAD") == commit
    assert (directory / "src/candidate.py").read_text() == "value = 2\n"
    original = (root / "runs/history/report.md").read_bytes()
    assert module.prepare(root)["prepared"] == 1
    moved = root.with_name("迁移 Study B")
    root.rename(moved)
    assert module.prepare(moved)["prepared"] == 1
    directory = Path(api.route_summaries("history", project_root=moved)[0]["iterations"][0]["candidate_dir"])
    assert directory.is_relative_to(moved)
    assert command(directory, "rev-parse", "HEAD") == commit
    assert (moved / "runs/history/report.md").read_bytes() == original
    assert json.loads((moved / "runs/history/execution.json").read_text())["imported_history"]


def test_history_commit_mismatch_is_refused(study):
    root, _ = study
    path = root / "runs/history/step2/P00/i1/record.json"
    record = json.loads(path.read_text())
    record["commit"] = "0" * 40
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="不一致"):
        module.prepare(root)
    assert not (root / "runs/history/execution.json").exists()
