"""Real CLI preflight is reviewable and never invents a completed proposal."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("prepare", [True, False])
def test_headless_cli_preflight_and_missing_credential(tmp_path: Path, prepare: bool) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(REPO_ROOT / x) for x in ("", "backend", "posttrain/src"))
    env.pop("ZHIPU_API_KEY", None)
    command = [sys.executable, "scripts/run_idea_lut_live.py", "--runs-root", str(tmp_path)]
    if prepare:
        command.append("--prepare-only")
    result = subprocess.run(command, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == (0 if prepare else 2), result.stderr
    assert not list(tmp_path.rglob("idea_proposal*.md"))
    assert not list(tmp_path.rglob("events.jsonl"))
    if prepare:
        summary = json.loads(next(tmp_path.rglob("summary.json")).read_text())
        assert summary["status"] == "prepared" and not summary["material_ready"]
        request = json.loads(next(tmp_path.rglob("request.json")).read_text())
        assert request["credential_persisted"] is False
        assert request["scenario"]["requirements"]["max_parameter_ratio"] == 1.2
    else:
        assert "ZHIPU_API_KEY is missing; no request made" in result.stderr
