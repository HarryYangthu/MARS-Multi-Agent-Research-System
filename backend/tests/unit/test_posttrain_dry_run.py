from __future__ import annotations

import json
from pathlib import Path

from app.harness.evaluation.aggregation import write_scorecard
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore
from mars_posttrain.dry_run import DryRunOptions, run_dry_run


def _proposal_text() -> str:
    metadata = {
        "schema": "proposal.v1",
        "project": "pimc",
        "agent": "idea",
        "research_question": "Can hard routing reduce compute while preserving RES?",
        "hypothesis": "Hard top-2 routing preserves RES within 1.5 dB.",
        "novelty": "Compares hard routing against the soft baseline.",
        "constraints": ["baseline_compat: required"],
        "related_literature": [{"title": "Routing Survey"}],
        "testable_predictions": [
            {
                "prediction": "RES degradation remains below 1.5 dB.",
                "metric": "RES",
                "expected_direction": "lte_degradation",
                "success_threshold": "<=1.5 dB",
            }
        ],
        "experiment_hint": {
            "variables": ["router_type"],
            "metrics": ["RES", "PIM", "APE"],
            "minimal_ablations": [
                {"name": "soft", "config": {"router_type": "soft"}},
                {"name": "hard", "config": {"router_type": "hard-top2"}},
            ],
        },
        "downstream_requirements": ["Compare routers under identical 8L settings."],
    }
    return fm_dumps(metadata, "# Proposal\n\nHard routing is tested against baseline.\n")


def test_posttrain_dry_run_writes_traceable_mock_checkpoint(tmp_path: Path) -> None:
    run = RunStore(tmp_path / "runs").create(task="posttrain-dry-run", project="pimc")
    store = ArtifactStore(run)
    draft = store.write(text=_proposal_text())
    store.approve(draft)
    hitl_log = run.subdir("hitl") / "review_log.jsonl"
    hitl_log.write_text(
        json.dumps(
            {
                "run_id": run.run_id,
                "agent": "idea",
                "action": "approve",
                "detail": {"version": "v1"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_scorecard(run_root=run.root, run_id=run.run_id, project=run.project)

    output_root = tmp_path / "posttrain"
    manifest = run_dry_run(
        DryRunOptions(
            run_root=run.root,
            output_root=output_root,
        )
    )

    assert manifest["schema"] == "posttrain_dry_run_report.v1"
    assert manifest["eligible_count"] == 1
    assert manifest["preference_pair_count"] == 1
    assert manifest["acceptance"]["requires_gpu"] is False
    assert manifest["acceptance"]["preference_pairs_traceable_to_hitl"] is True
    assert set(manifest["acceptance"]["reward_families"]) == {
        "schema_validity",
        "baseline_preservation",
        "downstream_metric",
    }

    report_path = output_root / manifest["report_path"]
    checkpoint_path = output_root / manifest["checkpoint_path"]
    assert report_path.is_file()
    assert checkpoint_path.is_file()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["mode"] == "dry_run"
    assert checkpoint["example_count"] == 1
