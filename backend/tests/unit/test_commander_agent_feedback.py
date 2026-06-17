from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.bridge.agent_registry import get_registry, reset_registry_for_tests
from app.bridge.agent_runner import run_agent_node
from app.bridge.commander_agent import (
    CommanderAgent,
    CommanderAttribution,
    RunObservation,
    load_feedback_context_for_agent,
)
from app.bridge.commander_eval import run_commander_attribution_eval
from app.bridge.commander_observability import build_commander_observability
from app.bridge.diagnostics import (
    DiagnosisAnalysis,
    DiagnosticsConfig,
    MetricFailure,
    SuspectedCause,
)
from app.harness.context.budget_policy import ContextBudgetPolicy
from app.harness.llm.mock_provider import build_fake_metadata
from app.harness.kb.stores import KBStores
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_document
from app.storage.agent_context_store import load_agent_memory_items
from app.storage.run_store import RunHandle, RunStore
from app.storage.self_evolution_store import (
    approve_memory_candidate,
    approve_self_evolution_mutation,
    build_self_evolution_levers,
    create_self_evolution_mutation,
    list_self_evolution_mutations,
    mark_memory_candidate_stale,
    read_jsonl,
    reject_memory_candidate,
    reject_self_evolution_mutation,
    supersede_memory_candidate,
)


def _write_metrics_and_artifacts(
    run: RunHandle,
    *,
    loss: float = 0.5,
    high_code_risk: bool = False,
    empty_ablations: bool = False,
) -> None:
    (run.subdir("execution") / "metrics.json").write_text(
        '[{"metrics": {"loss": %.4f, "RES": -42.0}}]' % loss,
        encoding="utf-8",
    )
    experiment_meta = {
        "schema": "experiment_plan.v1",
        "project": run.project,
        "agent": "experiment",
        "variables": {"independent": ["k"], "dependent": ["loss"]},
        "metrics": {"primary": "loss"},
        "ablations": [] if empty_ablations else [{"name": "a", "config": {"k": 1}}],
        "estimated_runs": 1,
    }
    (run.subdir("experiment") / "experiment_plan.approved.md").write_text(
        fm_dumps(experiment_meta, "plan"),
        encoding="utf-8",
    )
    code_meta = {
        "schema": "code_spec.v1",
        "project": run.project,
        "agent": "coding",
        "target_lang": "python",
        "baseline_compat": {"preserved": True},
        "files_changed": [
            {
                "path": "libs/router_v2.py",
                "type": "modified",
                "risk": "high" if high_code_risk else "low",
            }
        ],
    }
    (run.subdir("coding") / "code_spec.approved.md").write_text(
        fm_dumps(code_meta, "code"),
        encoding="utf-8",
    )


def test_commander_targets_experiment_for_config_sanity(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="feedback", project="moe-pimc")
    _write_metrics_and_artifacts(run, empty_ablations=True)

    decision = CommanderAgent().diagnose(run=run, attempt=1)

    assert decision.should_continue
    assert decision.recommended_target == "experiment"
    assert decision.feedback_packet_ref == "diagnosis/feedback_packet.attempt_2.md"
    packet = run.root / decision.feedback_packet_ref
    result = validate_document(packet.read_text(encoding="utf-8"), expected_schema="feedback_packet.v1")
    assert result.valid, result.errors


def test_commander_targets_coding_for_code_risk_and_writes_memory(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="feedback", project="moe-pimc")
    _write_metrics_and_artifacts(run, high_code_risk=True)

    decision = CommanderAgent().diagnose(run=run, attempt=1)

    assert decision.recommended_target == "coding"
    memory_dir = run.subdir("memory")
    episodes = read_jsonl(memory_dir / "episode_memory.jsonl")
    candidates = read_jsonl(memory_dir / "memory_candidates.jsonl")
    assert episodes and episodes[-1]["target_agent"] == "coding"
    assert candidates and candidates[-1]["status"] == "pending_review"
    assert candidates[-1]["agent"] == "coding"


def test_low_confidence_target_flip_pauses_for_human(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="flip", project="moe-pimc")
    analysis = DiagnosisAnalysis(
        passed=False,
        failed_metrics=(
            MetricFailure(
                metric="loss",
                observed=0.5,
                target=0.04,
                direction="lte",
                gap=0.46,
                aggregation="max",
            ),
        ),
        suspected_causes=(
            SuspectedCause(kind="metrics_gap", summary="threshold missed"),
        ),
        evidence_refs=("execution/metrics.json",),
    )
    observation = RunObservation(
        run=run,
        attempt=1,
        config=DiagnosticsConfig(
            project="moe-pimc",
            allowed_targets=(),
            default_target="coding",
            metric_rules=(),
            analyzers={},
        ),
        analysis=analysis,
        metrics_summary={},
        curve_summary={},
        log_summary={},
        approved_artifact_refs={},
        attempt_history=[
            {
                "attempt": 1,
                "target_agent": "experiment",
                "confidence": 0.4,
                "passed": False,
            }
        ],
        latest_diagnosis=None,
    )

    attribution = CommanderAgent().diagnose_failure(observation)

    assert attribution.requires_human
    assert not attribution.should_continue
    assert attribution.target_agent == "none"


def test_feedback_context_is_target_only_and_bounded(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="packet", project="moe-pimc")
    _write_metrics_and_artifacts(run, high_code_risk=True)
    CommanderAgent().diagnose(run=run, attempt=1)

    coding_ctx = load_feedback_context_for_agent(
        run=run,
        agent="coding",
        attempt=2,
        policy=ContextBudgetPolicy(max_tokens=80, recent_packet_count=1),
    )
    experiment_ctx = load_feedback_context_for_agent(
        run=run,
        agent="experiment",
        attempt=2,
        max_tokens=80,
    )

    assert coding_ctx is not None
    assert len(str(coding_ctx["text"])) <= 320
    assert coding_ctx["max_chars"] == 320
    assert coding_ctx["budget_policy"]["recent_packet_count"] == 1
    assert "full_diagnosis_replaced_by_refs" in coding_ctx["prune_reasons"]
    assert experiment_ctx is None


class _CaptureCodingAgent(BaseAgent):
    name = "coding"
    output_schema = "code_spec.v1"

    def __init__(self) -> None:
        super().__init__()
        self.captured_upstream: dict[str, str] = {}

    async def run_loop(self, request: RunRequest, context: ContextPack) -> Artifact:
        self.captured_upstream = dict(request.upstream_artifacts)
        metadata = build_fake_metadata("code_spec.v1", seed="capture")
        body = "# captured\n"
        return Artifact(
            text=fm_dumps(metadata, body),
            schema_id="code_spec.v1",
            metadata=metadata,
            body=body,
        )

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        return await self.run_loop(request, context)


@pytest.mark.asyncio
async def test_agent_runner_injects_commander_feedback_only_for_target(
    tmp_path: Path,
) -> None:
    reset_registry_for_tests()
    try:
        run = RunStore(tmp_path).create(task="runner", project="moe-pimc")
        _write_metrics_and_artifacts(run, high_code_risk=True)
        CommanderAgent().diagnose(run=run, attempt=1)
        agent = _CaptureCodingAgent()
        get_registry().register("coding", agent)

        await run_agent_node(run, "coding_attempt_2")

        assert "commander_feedback" in agent.captured_upstream
        assert "diagnosis.v1.md" not in agent.captured_upstream
    finally:
        reset_registry_for_tests()


def test_pending_memory_does_not_load_until_approved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.storage.agent_context_store as context_store

    monkeypatch.setattr(context_store, "repo_root", lambda: tmp_path)
    run = RunStore(tmp_path / "runs").create(task="memory", project="moe-pimc")
    _write_metrics_and_artifacts(run, high_code_risk=True)
    CommanderAgent().diagnose(run=run, attempt=1)
    candidates = read_jsonl(run.subdir("memory") / "memory_candidates.jsonl")
    candidate_id = str(candidates[-1]["id"])

    assert load_agent_memory_items("coding") == ()

    stores = KBStores(tmp_path / "knowledge")
    approved = approve_memory_candidate(
        run=run,
        candidate_id=candidate_id,
        stores=stores,
    )

    loaded = load_agent_memory_items("coding")
    reviewed = read_jsonl(run.subdir("memory") / "memory_candidates.jsonl")
    review_audit = read_jsonl(run.subdir("memory") / "memory_candidate_reviews.jsonl")
    assert approved["status"] == "approved"
    assert reviewed[-1]["status"] == "approved"
    assert reviewed[-1]["approved_memory_id"] == approved["memory_id"]
    assert review_audit[-1]["status"] == "approved"
    assert len(loaded) == 1
    assert loaded[0].status == "approved"
    kb_records = stores.zone("methodology").all(exclude_mock=False)
    assert len(kb_records) == 1
    assert kb_records[0].metadata["memory_type"] == "procedural"
    assert kb_records[0].metadata["source_path"] == f"agents/coding/memory_items/{approved['memory_id']}"


def test_self_evolution_levers_are_manual_review_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.storage.agent_context_store as context_store

    monkeypatch.setattr(context_store, "repo_root", lambda: tmp_path)
    context_store.create_agent_context_file(
        "coding",
        category="prompts",
        filename="repair.md",
        content="Prefer small patches during repair.",
    )
    context_store.create_agent_context_file(
        "coding",
        category="examples",
        filename="focused_patch.md",
        content="A focused patch example.",
    )
    context_store.create_agent_context_file(
        "coding",
        category="evals",
        filename="risk_rubric.md",
        content="Block risky patch rewrites.",
    )
    run = RunStore(tmp_path / "runs").create(task="levers", project="moe-pimc")
    (run.subdir("memory") / "memory_candidates.jsonl").write_text(
        json.dumps(
            {
                "schema": "agent_memory_candidate.v1",
                "id": "candidate_1",
                "agent": "coding",
                "status": "pending_review",
                "text": "Keep repair patches focused.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run.subdir("events") / "evaluation_scorecard.json").write_text(
        json.dumps(
            {
                "schema": "evaluation_scorecard.v1",
                "top_findings": [
                    {
                        "id": "risk_high",
                        "message": "Patch risk stayed high.",
                        "evidence_refs": ["coding/code_spec.approved.md"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    view = build_self_evolution_levers(run=run)

    assert view["schema"] == "self_evolution_levers.v1"
    assert view["mutation_mode"] == "manual_review_only"
    assert "auto_mutate_prompt" not in view["allowed_actions"]
    assert view["counts"]["prompt"] >= 1
    assert view["counts"]["few_shot"] >= 1
    assert view["counts"]["eval"] >= 1
    assert view["counts"]["kb_finding"] >= 2


def test_self_evolution_mutation_requires_gate_and_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.storage.agent_context_store as context_store

    monkeypatch.setattr(context_store, "repo_root", lambda: tmp_path)
    original = context_store.create_agent_context_file(
        "coding",
        category="prompts",
        filename="repair.md",
        content="Prefer small patches during repair.",
    )
    run = RunStore(tmp_path / "runs").create(task="mutation", project="moe-pimc")
    stores = KBStores(tmp_path / "knowledge")

    blocked = create_self_evolution_mutation(
        run=run,
        lever_id="agent_context:coding:prompts/repair.md",
        agent="coding",
        path=original.path,
        proposed_content=original.content,
        rationale="",
    )
    assert blocked["eval_gate"]["passed"] is False
    with pytest.raises(ValueError, match="did not pass eval gate"):
        approve_self_evolution_mutation(
            run=run,
            mutation_id=str(blocked["id"]),
            stores=stores,
        )

    proposal = create_self_evolution_mutation(
        run=run,
        lever_id="agent_context:coding:prompts/repair.md",
        agent="coding",
        path=original.path,
        proposed_content="Prefer focused patches and preserve tests.",
        rationale="Eval findings show broad repair patches are risky.",
    )

    assert proposal["status"] == "pending_review"
    assert proposal["eval_gate"]["passed"] is True
    assert context_store.list_agent_context_files("coding")[0].content == original.content

    applied = approve_self_evolution_mutation(
        run=run,
        mutation_id=str(proposal["id"]),
        reviewer_note="approved by HITL",
        stores=stores,
    )

    loaded = {
        item.path: item
        for item in context_store.list_agent_context_files("coding", include_runtime_code=False)
    }
    assert applied["status"] == "applied"
    assert loaded[original.path].content == "Prefer focused patches and preserve tests."
    assert list_self_evolution_mutations(run=run)[-1]["status"] == "applied"
    kb_records = stores.zone("methodology").all(exclude_mock=False)
    assert kb_records
    assert kb_records[-1].metadata["source_path"] == "agents/coding/prompts/repair.md"


def test_self_evolution_mutation_reject_does_not_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.storage.agent_context_store as context_store

    monkeypatch.setattr(context_store, "repo_root", lambda: tmp_path)
    original = context_store.create_agent_context_file(
        "coding",
        category="evals",
        filename="rubric.md",
        content="Block risky patches.",
    )
    run = RunStore(tmp_path / "runs").create(task="reject-mutation", project="moe-pimc")
    proposal = create_self_evolution_mutation(
        run=run,
        lever_id="agent_context:coding:evals/rubric.md",
        agent="coding",
        path=original.path,
        proposed_content="Allow all patches.",
        rationale="Test rejection path.",
    )

    rejected = reject_self_evolution_mutation(
        run=run,
        mutation_id=str(proposal["id"]),
        reviewer_note="unsafe",
    )

    loaded = {
        item.path: item
        for item in context_store.list_agent_context_files("coding", include_runtime_code=False)
    }
    assert rejected["status"] == "rejected"
    assert loaded[original.path].content == original.content


def test_memory_candidate_lifecycle_decisions_are_audited(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="memory-lifecycle", project="moe-pimc")
    candidates_path = run.subdir("memory") / "memory_candidates.jsonl"
    rows = [
        {"id": "reject_me", "agent": "coding", "status": "pending_review", "text": "x"},
        {"id": "stale_me", "agent": "coding", "status": "approved", "text": "y"},
        {"id": "supersede_me", "agent": "experiment", "status": "approved", "text": "z"},
    ]
    candidates_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    rejected = reject_memory_candidate(
        run=run,
        candidate_id="reject_me",
        reviewer_note="not reusable",
    )
    stale = mark_memory_candidate_stale(run=run, candidate_id="stale_me")
    superseded = supersede_memory_candidate(
        run=run,
        candidate_id="supersede_me",
        superseded_by="newer_memory",
    )

    statuses = {item["id"]: item["status"] for item in read_jsonl(candidates_path)}
    reviews = read_jsonl(run.subdir("memory") / "memory_candidate_reviews.jsonl")
    assert rejected["status"] == "rejected"
    assert stale["status"] == "stale"
    assert superseded["status"] == "superseded"
    assert statuses == {
        "reject_me": "rejected",
        "stale_me": "stale",
        "supersede_me": "superseded",
    }
    assert [item["status"] for item in reviews] == [
        "rejected",
        "stale",
        "superseded",
    ]


def test_commander_marks_approved_memory_stale_after_repeated_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.storage.agent_context_store as context_store

    monkeypatch.setattr(context_store, "repo_root", lambda: tmp_path)
    cfg_dir = tmp_path / "configs" / "agent_contexts"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "coding.yaml").write_text(
        "\n".join(
            [
                "agent: coding",
                "memory_items:",
                "  - id: mem1",
                "    label: risky lesson",
                "    text: Try this coding strategy.",
                "    enabled: true",
                "    status: approved",
                "    source: test",
                "    evidence_refs: []",
            ]
        ),
        encoding="utf-8",
    )
    run = RunStore(tmp_path / "runs").create(task="stale", project="moe-pimc")
    context_manifest = {
        "summary": {
            "metadata": {
                "memory_sources": {
                    "long_term_memory": "approved_only",
                    "long_term_memory_ids": ["mem1"],
                }
            }
        }
    }
    (run.subdir("context") / "coding_attempt_2_context_pack.v1.json").write_text(
        json.dumps(context_manifest),
        encoding="utf-8",
    )
    failure = MetricFailure(
        metric="loss",
        observed=0.5,
        target=0.04,
        direction="lte",
        gap=0.46,
        aggregation="mean",
    )
    analysis = DiagnosisAnalysis(
        passed=False,
        failed_metrics=(failure,),
        suspected_causes=(
            SuspectedCause(kind="code_change_risk", summary="risky patch"),
        ),
        evidence_refs=("execution/metrics.json",),
    )
    attribution = CommanderAttribution(
        passed=False,
        should_continue=True,
        target_agent="coding",
        confidence=0.85,
        reason="Coding risk stayed high.",
        expected_fix="Use a smaller coding patch.",
        failed_metrics=[failure.to_metadata()],
        suspected_causes=[analysis.suspected_causes[0].to_metadata()],
        evidence_refs=list(analysis.evidence_refs),
        rejected_alternatives=[],
        budget_status="within_budget",
        next_attempt=3,
    )
    commander = CommanderAgent()
    first_observation = RunObservation(
        run=run,
        attempt=2,
        config=DiagnosticsConfig(project="moe-pimc"),
        analysis=analysis,
        metrics_summary={},
        curve_summary={},
        log_summary={},
        approved_artifact_refs={},
        attempt_history=[],
        latest_diagnosis=None,
    )
    second_observation = RunObservation(
        run=run,
        attempt=3,
        config=DiagnosticsConfig(project="moe-pimc"),
        analysis=analysis,
        metrics_summary={},
        curve_summary={},
        log_summary={},
        approved_artifact_refs={},
        attempt_history=[],
        latest_diagnosis=None,
    )

    commander.write_learning_memory(
        run=run,
        observation=first_observation,
        attribution=attribution,
        feedback_packet_ref="diagnosis/feedback_packet.attempt_3.md",
    )
    assert [item.id for item in load_agent_memory_items("coding")] == ["mem1"]

    commander.write_learning_memory(
        run=run,
        observation=second_observation,
        attribution=attribution,
        feedback_packet_ref="diagnosis/feedback_packet.attempt_4.md",
    )

    assert load_agent_memory_items("coding") == ()
    episodes = read_jsonl(run.subdir("memory") / "episode_memory.jsonl")
    assert episodes[-1]["memory_outcomes"][0]["stale_ids"] == ["mem1"]


def test_commander_observability_collects_attempt_context_and_memory(
    tmp_path: Path,
) -> None:
    run = RunStore(tmp_path).create(task="observe", project="moe-pimc")
    _write_metrics_and_artifacts(run, high_code_risk=True)
    CommanderAgent().diagnose(run=run, attempt=1)
    context_manifest = {
        "summary": {
            "task": {"upstream_handoff_keys": ["commander_feedback"]},
            "metadata": {
                "feedback_context": {
                    "path": "diagnosis/feedback_packet.attempt_2.md",
                    "original_chars": 2000,
                    "compressed_chars": 1200,
                    "injected": True,
                },
                "compression": {"strategy": "bounded_commander_feedback"},
                "memory_sources": {"long_term_memory": "approved_only"},
                "pollution_guards": {"target_only": True},
            },
        },
        "tokens_estimated": 900,
    }
    (run.subdir("context") / "coding_attempt_2_context_pack.v1.json").write_text(
        json.dumps(context_manifest),
        encoding="utf-8",
    )

    view = build_commander_observability(run)

    assert view["schema"] == "commander_observability.v1"
    assert view["attempt_count"] == 1
    attempt = view["attempts"][0]
    assert attempt["recommended_target"] == "coding"
    assert attempt["observability"]["has_feedback_packet"]
    assert attempt["observability"]["feedback_was_injected"]
    assert attempt["observability"]["target_only_guard"]
    assert view["memory_candidates"]


def test_commander_attribution_eval_replay_cases_pass() -> None:
    result = run_commander_attribution_eval(project="moe-pimc")

    assert result["schema"] == "commander_attribution_eval.v1"
    assert result["case_count"] >= 6
    assert result["failed"] == 0
    assert result["target_accuracy"] == 1.0
