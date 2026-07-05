from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.harness.schema.validator import validate_document

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_idea_standalone_script_writes_research_pack(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "backend")
    env["MARS_MOCK_MODE"] = "always"
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "CUSTOM_ENDPOINT_URL",
        "CUSTOM_ENDPOINT_API_KEY",
    ):
        env.pop(key, None)
    env["LOCAL_VLLM_BASE_URL"] = ""

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_idea_agent_standalone.py",
            "--mock-mode",
            "always",
            "--runs-root",
            str(runs_root),
            "--question",
            "单独测试 Idea Agent 的完整调研产物。",
            "--preview-chars",
            "0",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "[research artifacts]" in result.stdout
    assert "research_summary.v1.md" in result.stdout
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    run_root = run_dirs[0]

    research_dir = run_root / "idea" / "research"
    assert (research_dir / "research_plan.v1.md").exists()
    assert (research_dir / "research_notes.v1.md").exists()
    assert (research_dir / "research_summary.v1.md").exists()
    assert (research_dir / "evidence_index.v1.json").exists()

    proposal = run_root / "idea" / "idea_proposal.v1.md"
    validation = validate_document(
        proposal.read_text(encoding="utf-8"),
        expected_schema="proposal.v1",
    )
    assert validation.valid, validation.errors
