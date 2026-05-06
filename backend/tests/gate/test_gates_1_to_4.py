"""Gates 1-4 — flow checkpoint gates."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.gates import (
    conclusion_output,
    experiment_launch,
    large_refactor,
    plan_finalized,
)
from app.storage.run_store import RunStore


def test_gate_1_blocks_until_idea_approved(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="t", project="moe-pimc")
    out = plan_finalized.check(run.root)
    assert out.triggered and out.requires_human

    (run.subdir("idea") / "idea_proposal.approved.md").write_text("ok")
    out = plan_finalized.check(run.root)
    assert not out.triggered


def test_gate_2_large_refactor_threshold() -> None:
    md = {"files_changed": [{"path": f"f{i}.py", "type": "modified"} for i in range(6)]}
    out = large_refactor.check(md, threshold=5)
    assert out.triggered and out.blocking

    md = {"files_changed": [{"path": "f.py", "type": "modified"}]}
    out = large_refactor.check(md, threshold=5)
    assert not out.triggered


def test_gate_3_experiment_launch_gpu_threshold() -> None:
    out = experiment_launch.check({"estimated_gpu_hours": 24}, threshold=12)
    assert out.triggered

    out = experiment_launch.check({"estimated_gpu_hours": 6}, threshold=12)
    assert not out.triggered


def test_gate_4_conclusion_output_blocks_without_approved_report(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="t", project="moe-pimc")
    out = conclusion_output.check(run.root)
    assert out.triggered

    (run.subdir("writing") / "research_report.approved.md").write_text("ok")
    out = conclusion_output.check(run.root)
    assert not out.triggered
