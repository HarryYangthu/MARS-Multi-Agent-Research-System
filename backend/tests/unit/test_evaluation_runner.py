from __future__ import annotations

from pathlib import Path

from app.harness.evaluation.aggregation import has_blocker, worst_decision
from app.harness.evaluation.artifacts import write_report
from app.harness.evaluation.runner import EvaluationRunner
from app.harness.schema.validator import validate_document


VALID_PROPOSAL = """---
schema: proposal.v1
project: moe-pimc
agent: idea
research_question: How can routing be simplified while preserving RES?
hypothesis: Hard top-2 routing keeps RES degradation below the threshold.
novelty: Stream-aware hard routing is compared against the current baseline.
---

# Proposal

Testable proposal.
"""


STRONG_PROPOSAL = """---
schema: proposal.v1
project: moe-pimc
agent: idea
research_question: How can routing be simplified while preserving RES?
hypothesis: Hard top-2 routing keeps RES degradation below 1.5 dB.
novelty: Stream-aware hard routing is explicitly compared with the existing soft router baseline and prior run archive behavior.
constraints:
  - "baseline_compat: required"
related_literature:
  - title: Routing Survey
testable_predictions:
  - prediction: RES degradation remains below 1.5 dB.
    metric: RES
    expected_direction: lte_degradation
    success_threshold: "<=1.5 dB"
experiment_hint:
  variables: [router_type, expert_count]
  metrics: [RES, PIM, APE]
  minimal_ablations:
    - {name: soft, config: {router_type: soft}}
    - {name: hard, config: {router_type: hard-top2}}
downstream_requirements:
  - Compare soft and hard routers under identical 8L settings.
---

# Proposal

The experiment directly tests the routing simplification claim.
"""


INVALID_PROPOSAL = """---
schema: proposal.v1
project: moe-pimc
agent: idea
research_question: How can routing be simplified while preserving RES?
hypothesis: Hard top-2 routing keeps RES degradation below the threshold.
---

# Proposal

Missing novelty.
"""


WEAK_REPORT = """---
schema: report.v1
project: moe-pimc
agent: writing
deliverable_type: research_report
target_audience: advisor
chain_refs:
  proposal: idea/idea_proposal.approved.md
---

# Report

This is a brief summary without metrics or limitations.
"""


def test_schema_validity_evaluator_passes_valid_artifact() -> None:
    reports = EvaluationRunner().evaluate_text(
        project="moe-pimc",
        text=VALID_PROPOSAL,
        target_ref="idea/idea_proposal.v1.md",
        expected_schema="proposal.v1",
    )

    assert len(reports) == 3
    report = next(r for r in reports if r.evaluator == "contract.schema_validity")
    provenance = next(r for r in reports if r.evaluator == "contract.provenance")
    assert report.decision == "pass"
    assert provenance.decision == "pass"
    assert report.overall_score == 1.0
    assert not report.findings

    rendered = report.to_markdown()
    result = validate_document(rendered, expected_schema="evaluation_report.v1")
    assert result.valid, result.errors


def test_artifact_quality_rubric_passes_strong_proposal() -> None:
    reports = EvaluationRunner().evaluate_text(
        project="moe-pimc",
        text=STRONG_PROPOSAL,
        target_ref="idea/idea_proposal.v1.md",
        expected_schema="proposal.v1",
    )

    quality = next(r for r in reports if r.evaluator == "artifact_quality.rubric")
    assert quality.decision == "pass"
    assert quality.overall_score is not None
    assert quality.overall_score >= 0.8
    assert quality.scores["testability"] == 1.0
    assert quality.scores["evidence"] == 1.0
    result = validate_document(
        quality.to_markdown(),
        expected_schema="evaluation_report.v1",
    )
    assert result.valid, result.errors


def test_artifact_quality_rubric_revises_weak_report() -> None:
    reports = EvaluationRunner().evaluate_text(
        project="moe-pimc",
        text=WEAK_REPORT,
        target_ref="writing/research_report.v1.md",
        expected_schema="report.v1",
    )

    quality = next(r for r in reports if r.evaluator == "artifact_quality.rubric")
    assert quality.decision == "revise"
    assert quality.findings
    assert any(f.category == "chain_coverage" for f in quality.findings)
    result = validate_document(
        quality.to_markdown(),
        expected_schema="evaluation_report.v1",
    )
    assert result.valid, result.errors


def test_schema_validity_evaluator_blocks_invalid_artifact(tmp_path: Path) -> None:
    reports = EvaluationRunner().evaluate_text(
        project="moe-pimc",
        text=INVALID_PROPOSAL,
        target_ref="idea/idea_proposal.v1.md",
        expected_schema="proposal.v1",
    )

    assert worst_decision(reports) == "block"
    assert has_blocker(reports)
    report = next(r for r in reports if r.evaluator == "contract.schema_validity")
    assert report.blocking
    assert report.findings
    assert report.findings[0].severity == "blocker"
    assert report.findings[0].evidence_refs == ("idea/idea_proposal.v1.md",)

    path = tmp_path / "idea_proposal.v1.eval.md"
    write_report(path, report)
    result = validate_document(
        path.read_text(encoding="utf-8"),
        expected_schema="evaluation_report.v1",
    )
    assert result.valid, result.errors
