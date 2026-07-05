from __future__ import annotations

import json
from pathlib import Path

from app.harness.evaluation.run_report import evaluate_run_replay
from app.harness.evaluation.suites import EvaluationSuite, ExpectedOutcome
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunStore


VALID_PROPOSAL = """---
schema: proposal.v1
project: pimc
agent: idea
research_question: How can routing be simplified while preserving RES?
hypothesis: Hard top-2 routing keeps RES degradation below 1.5 dB.
novelty: Stream-aware hard routing is compared against the current baseline.
---

# Proposal

Testable proposal.
"""


def test_evaluate_run_replay_writes_report_scorecard_and_candidates(tmp_path: Path) -> None:
    run = RunStore(runs_root=tmp_path).create(
        task="eval smoke",
        project="pimc",
        user_request="Evaluate this run.",
    )
    (run.root / "idea" / "idea_proposal.approved.md").write_text(
        VALID_PROPOSAL,
        encoding="utf-8",
    )
    (run.root / "context" / "trace_manifest.v2.json").write_text(
        json.dumps({"schema": "trace_manifest.v2"}),
        encoding="utf-8",
    )
    (run.root / "events" / "agent_events.jsonl").write_text(
        json.dumps({"agent": "idea", "to_state": "done"}) + "\n",
        encoding="utf-8",
    )
    (run.root / "events" / "tool_events.jsonl").write_text(
        json.dumps({"event": "tool.started", "call_id": "call_1"}) + "\n"
        + json.dumps({"event": "tool.completed", "call_id": "call_1"}) + "\n",
        encoding="utf-8",
    )
    (run.root / "events" / "tool_calls.jsonl").write_text(
        json.dumps({"tool": "knowledge.query", "status": "success"}) + "\n",
        encoding="utf-8",
    )
    (run.root / "events" / "evaluation_events.jsonl").write_text(
        json.dumps({"event": "evaluation.seed"}) + "\n",
        encoding="utf-8",
    )
    suite = EvaluationSuite(
        id="unit_replay",
        expected=ExpectedOutcome(
            required_dirs=("input", "context", "idea", "events"),
            required_artifacts=("idea/idea_proposal.approved.md",),
            required_event_files=("events/evaluation_events.jsonl",),
            require_context_manifest=True,
            require_tool_audit=True,
        ),
    )

    result = evaluate_run_replay(run=run, suite=suite)

    assert result.markdown_report_path.exists()
    assert result.scorecard_path.exists()
    assert result.self_evolution_candidates_path.exists()
    assert result.human_review_queue_path.exists()
    assert all(path.exists() for path in result.report_paths)
    assert {report.scope for report in result.reports} == {"run"}
    assert not any(report.blocking for report in result.reports)
    reports_by_evaluator = {report.evaluator: report for report in result.reports}
    assert reports_by_evaluator["llm_rubric.advisory"].grader_type == "llm"
    assert reports_by_evaluator["llm_rubric.advisory"].advisory is True
    assert reports_by_evaluator["llm_rubric.advisory"].requires_human_review is True
    assert reports_by_evaluator["human_review.queue"].grader_type == "human"
    assert reports_by_evaluator["human_review.queue"].advisory is True
    for path in result.report_paths:
        validation = validate_document(
            path.read_text(encoding="utf-8"),
            expected_schema="evaluation_report.v1",
        )
        assert validation.valid, validation.errors
    queue_rows = [
        json.loads(line)
        for line in result.human_review_queue_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row["source_evaluator"] == "llm_rubric.advisory" for row in queue_rows)
    scorecard = json.loads(result.scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["grader_counts"]["llm"] == 1
    assert scorecard["grader_counts"]["human"] == 1
    assert scorecard["grader_counts"]["advisory"] == 2


def test_evaluate_run_replay_flags_missing_required_artifact(tmp_path: Path) -> None:
    run = RunStore(runs_root=tmp_path).create(
        task="missing artifact",
        project="pimc",
        user_request="Evaluate this run.",
    )
    suite = EvaluationSuite(
        id="unit_replay",
        expected=ExpectedOutcome(
            required_dirs=("input", "context", "idea", "events"),
            required_artifacts=("idea/idea_proposal.approved.md",),
            require_context_manifest=False,
            require_tool_audit=False,
        ),
    )

    result = evaluate_run_replay(run=run, suite=suite)

    integrity = next(
        report
        for report in result.reports
        if report.evaluator == "run_integrity.required_outcome"
    )
    assert integrity.decision == "revise"
    assert any(f.id == "required_artifact_missing" for f in integrity.findings)
    scorecard = json.loads(result.scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["overall_decision"] == "revise"
    assert scorecard["grader_counts"]["requires_human_review"] >= 1
