"""Real preparation/validation of the standalone Bridge CLI; no fake execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.run_idea_standalone import build_request, parser

ROOT = Path(__file__).resolve().parents[3]


def test_standalone_preparation_preserves_request_and_context(tmp_path: Path) -> None:
    request = tmp_path / "question.md"
    request.write_text("仅提出一个保留基线的优化方案。\n", encoding="utf-8")
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"baseline_code": "original baseline\n"}), encoding="utf-8")
    runs = tmp_path / "runs"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "backend"))),
           "MARS_RUNTIME_MODE": "development", "MARS_IDEA_RUNTIME_PROFILE": "baseline"}
    completed = subprocess.run([sys.executable, "scripts/run_idea_standalone.py", "--project", "pimc",
                                "--request-file", str(request), "--context-json", str(context),
                                "--runs-root", str(runs), "--prepare-only"], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    summary_path = next(runs.glob("*/idea/standalone_summary.json"))
    summary = json.loads(summary_path.read_text())
    assert summary["status"] == "prepared"
    assert summary["standalone"] and not summary["proposal_ready"]
    assert not summary["human_approved"] and not summary["simulation_executed"]
    root = summary_path.parents[1]
    state = json.loads((root / "run_state.json").read_text())
    assert {node["key"] for node in state["graph"]["nodes"]} == {"idea"}
    assert state["request"]["standalone"] is True
    assert state["request"]["auto_approve"] is False
    assert (root / "input/user_request.md").read_text() == request.read_text()
    options = json.loads((root / "input/run_request_options.v1.json").read_text())["extra"]
    assert options["scope"] == "project_proposal"
    assert options["idea_context"]["baseline_code"] == "original baseline\n"
    assert not list(root.glob("agent_traces/*/*/events.jsonl"))
    assert not list(root.glob("idea/idea_proposal*.md"))


@pytest.mark.parametrize("seconds", ["0", "-1", "nan", "inf"])
def test_invalid_deadline_fails_before_run_creation(tmp_path: Path, seconds: str) -> None:
    request = tmp_path / "question.md"
    request.write_text("One idea.")
    args = parser().parse_args(["--project", "pimc", "--request-file", str(request), "--max-seconds", seconds])
    with pytest.raises(ValueError, match="positive and finite"):
        build_request(args)


def test_unknown_requirements_do_not_silently_weaken_contract(tmp_path: Path) -> None:
    request = tmp_path / "question.md"
    request.write_text("One idea.")
    requirements = tmp_path / "requirements.json"
    requirements.write_text('{"invent_success": true}')
    args = parser().parse_args(["--project", "pimc", "--request-file", str(request),
                               "--requirements-json", str(requirements)])
    with pytest.raises(ValueError):
        build_request(args)


def test_missing_real_credentials_archive_failure_without_execution(tmp_path: Path) -> None:
    request = tmp_path / "question.md"
    request.write_text("Only propose an idea.")
    runs = tmp_path / "runs"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "backend"))),
           "MARS_RUNTIME_MODE": "development", "MARS_IDEA_RUNTIME_PROFILE": "baseline",
           "DEEPSEEK_API_KEY": ""}
    completed = subprocess.run([sys.executable, "scripts/run_idea_standalone.py", "--project", "pimc",
                                "--request-file", str(request), "--runs-root", str(runs)],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 2, completed.stderr
    summary_path = next(runs.glob("*/idea/standalone_summary.json"))
    summary = json.loads(summary_path.read_text())
    assert summary["status"] == "configuration_error" and not summary["proposal_ready"]
    state = json.loads((summary_path.parents[1] / "run_state.json").read_text())
    assert state["status"] == "failed" and state["failed_nodes"] == ["idea"]
    assert not list(runs.glob("*/agent_traces/*/*/events.jsonl"))
    assert not list(runs.glob("*/idea/idea_proposal*.md"))
