from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_document
from app.harness.evaluation.aggregation import write_scorecard
from app.harness.evaluation.artifacts import read_reports_for_artifact
from app.storage.artifact_store import ArtifactStore, ArtifactValidationError
from app.storage.run_store import RunStore


def _proposal_text() -> str:
    md = {
        "schema": "proposal.v1",
        "project": "moe-pimc",
        "agent": "idea",
        "research_question": "How to simplify the router?",
        "hypothesis": "Hard top-2 keeps RES within 1.5 dB.",
        "novelty": "Stream-aware routing absent in surveys.",
    }
    return fm_dumps(md, "Body of proposal\n")


def test_write_validates_and_versions(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="t", project="moe-pimc")
    art = ArtifactStore(run)

    ref1 = art.write(text=_proposal_text())
    assert ref1.version == "v1"
    assert ref1.path.name == "idea_proposal.v1.md"

    ref2 = art.write(text=_proposal_text())
    assert ref2.version == "v2"


def test_write_rejects_invalid(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="t", project="moe-pimc")
    art = ArtifactStore(run)
    bad = "---\nschema: proposal.v1\nagent: idea\n---\n"  # missing fields
    with pytest.raises(ArtifactValidationError) as exc:
        art.write(text=bad)
    assert exc.value.result.errors


def test_approve_creates_approved_md(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="t", project="moe-pimc")
    art = ArtifactStore(run)
    ref = art.write(text=_proposal_text())
    approved = art.approve(ref)
    assert approved.path.name == "idea_proposal.approved.md"
    assert approved.path.read_text() == ref.path.read_text()


def test_write_and_approve_create_evaluation_reports(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="t", project="moe-pimc")
    art = ArtifactStore(run)

    ref = art.write(text=_proposal_text())
    reports = read_reports_for_artifact(
        run_root=run.root,
        agent_dir="idea",
        stem="idea_proposal",
        version="v1",
    )
    assert {r["metadata"]["evaluator"] for r in reports} == {
        "contract.schema_validity",
        "contract.provenance",
        "artifact_quality.rubric",
    }
    for report in reports:
        result = validate_document(report["text"], expected_schema="evaluation_report.v1")
        assert result.valid, result.errors

    approved = art.approve(ref)
    approved_reports = read_reports_for_artifact(
        run_root=run.root,
        agent_dir="idea",
        stem="idea_proposal",
        version="approved",
    )
    assert len(approved_reports) == 3
    assert all(
        r["metadata"]["target_ref"] == "idea/idea_proposal.approved.md"
        for r in approved_reports
    )

    scorecard_path = write_scorecard(
        run_root=run.root,
        run_id=run.run_id,
        project=run.project,
    )
    assert scorecard_path.name == "evaluation_scorecard.json"


def test_latest_prefers_approved(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="t", project="moe-pimc")
    art = ArtifactStore(run)
    ref1 = art.write(text=_proposal_text())
    ref2 = art.write(text=_proposal_text())
    art.approve(ref1)
    latest = art.latest(agent_dir="idea", stem="idea_proposal")
    assert latest is not None
    assert latest.version == "approved"
    # but list_versions still returns v1, v2, approved
    versions = art.list_versions(agent_dir="idea", stem="idea_proposal")
    assert {v.version for v in versions} == {"v1", "v2", "approved"}
    assert ref2.version == "v2"
