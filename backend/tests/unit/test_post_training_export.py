from __future__ import annotations

import json
from pathlib import Path

from app.harness.evaluation.aggregation import write_scorecard
from app.harness.evaluation.post_training_export import (
    PostTrainingExportOptions,
    write_post_training_export,
)
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


def _strong_proposal_text() -> str:
    metadata = {
        "schema": "proposal.v1",
        "project": "pimc",
        "agent": "idea",
        "research_question": "How can routing be simplified while preserving RES?",
        "hypothesis": "Hard top-2 routing keeps RES degradation below 1.5 dB.",
        "novelty": (
            "Stream-aware hard routing is explicitly compared with the existing "
            "soft router baseline and prior run archive behavior."
        ),
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
            "variables": ["router_type", "expert_count"],
            "metrics": ["RES", "PIM", "APE"],
            "minimal_ablations": [
                {"name": "soft", "config": {"router_type": "soft"}},
                {"name": "hard", "config": {"router_type": "hard-top2"}},
            ],
        },
        "downstream_requirements": [
            "Compare soft and hard routers under identical 8L settings."
        ],
    }
    return fm_dumps(metadata, "# Proposal\n\nThe experiment directly tests routing.\n")


def test_post_training_export_writes_labeled_approved_records(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="post-training-export", project="pimc")
    store = ArtifactStore(run)
    draft = store.write(text=_strong_proposal_text())
    store.approve(draft)
    write_scorecard(run_root=run.root, run_id=run.run_id, project=run.project)

    manifest = write_post_training_export(
        run_root=run.root,
        run_id=run.run_id,
        project=run.project,
        options=PostTrainingExportOptions(),
    )

    assert manifest["schema"] == "post_training_export_manifest.v1"
    assert manifest["record_count"] == 1
    assert manifest["eligible_count"] == 1
    export_path = run.root / manifest["path"]
    records = [
        json.loads(line)
        for line in export_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["schema"] == "post_training_example.v1"
    assert record["artifact"]["ref"] == "idea/idea_proposal.approved.md"
    assert record["artifact"]["approved"] is True
    assert record["training_eligible"] is True
    assert record["preference_candidate"]["pair_constructed"] is False
    assert record["preference_candidate"]["sibling_candidate_refs"] == [
        "idea/idea_proposal.v1.md"
    ]

    labels = record["labels"]
    schema_label = labels["schema_validity"]
    assert schema_label["value"] is True
    assert schema_label["evaluator_versions"]["contract.schema_validity"] == 1
    assert schema_label["evidence_refs"] == ["idea/idea_proposal.approved.md"]
    assert labels["artifact_score"]["score"] >= 0.8
    assert labels["outcome_passed"]["evidence_refs"] == [
        "events/evaluation_scorecard.json"
    ]


def test_post_training_export_can_include_drafts(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="post-training-export-drafts", project="pimc")
    ArtifactStore(run).write(text=_strong_proposal_text())

    manifest = write_post_training_export(
        run_root=run.root,
        run_id=run.run_id,
        project=run.project,
        options=PostTrainingExportOptions(include_drafts=True),
    )

    assert manifest["record_count"] == 1
    assert manifest["eligible_count"] == 0
    record = manifest["records_preview"][0]
    assert record["artifact"]["ref"] == "idea/idea_proposal.v1.md"
    assert record["artifact"]["approved"] is False
    assert record["training_eligible"] is False
