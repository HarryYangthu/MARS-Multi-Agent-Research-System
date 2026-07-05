from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.harness.context.engine import CompileContextInput, compile_context
from app.harness.kb.ingester import ingest_memory
from app.harness.kb.models import EvalStatus
from app.harness.kb.selector import select_memory
from app.harness.kb.stores import reset_for_tests
from app.harness.memory.conflict import assess_conflict
from app.harness.memory.episode import search_episode_index
from app.harness.memory.evals import evaluate_pollution, evaluate_retrieval_precision
from app.harness.memory.importance import calculate_importance
from app.main import create_app
from app.storage.run_store import RunStore
from app.storage.self_evolution_store import append_learning_event


def test_context_v2_injects_approved_memory_and_writes_usage(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path / "knowledge")
    ingest_memory(
        zone="methodology",
        text="Verified router methodology lowers RES under beam switching.",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        source_path="tests/methodology.md",
        eval_status=EvalStatus(passed=True, decision="pass"),
        confidence=0.9,
        salience=0.9,
        approved=True,
        stores=stores,
    )
    run = RunStore(tmp_path / "runs").create(
        task="router memory",
        project="pimc",
        user_request="Use router methodology to lower RES.",
    )

    result = compile_context(
        CompileContextInput(
            agent="experiment",
            node_key="experiment",
            project="pimc",
            output_schema="experiment_plan.v1",
            system="system",
            project_context="project",
            task="Use router methodology to lower RES.",
            upstream={},
            metadata={},
            run_id=run.run_id,
            run_root=run.root,
        )
    )

    memory_segments = [segment for segment in result.manifest.segments if segment.kind == "memory"]
    assert memory_segments
    usage_path = run.root / "context" / "agents" / "experiment" / "memory" / "memory_usage.jsonl"
    assert usage_path.exists()
    assert "tests/methodology.md" in usage_path.read_text(encoding="utf-8")


def test_episode_memory_is_indexed_and_searchable(tmp_path: Path) -> None:
    run = RunStore(tmp_path / "runs").create(
        task="episode",
        project="pimc",
        user_request="record episode",
    )

    append_learning_event(
        run=run,
        event={
            "target_agent": "experiment",
            "reason": "RES regression after shallow memory taps",
            "expected_fix": "increase memory taps",
            "passed": False,
        },
        memory_candidates=[],
    )

    hits = search_episode_index(run_root=run.root, query="shallow memory taps")
    assert hits
    assert hits[0]["score"] > 0


def test_importance_scoring_and_semantic_graph_bonus(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path / "knowledge")
    important = calculate_importance(
        agent="execution",
        metadata={"schema": "run_log.v1", "metrics": {"RES": -26}},
        text="记住: RES failure was fixed by deeper memory taps.",
    )
    assert important.score >= 0.8

    record = ingest_memory(
        zone="run_archive",
        text="Run archive: deeper memory taps fixed RES failure.",
        metadata={"project": "pimc", "kind": "run_log", "schema": "run_log.v1"},
        memory_type="episodic",
        source_path="runs/a/execution/run_log.approved.md",
        eval_status=EvalStatus(passed=True, decision="pass"),
        approved=True,
        stores=stores,
    )[0]

    graph_path = stores.base / "_semantic_graph.json"
    assert graph_path.exists()
    hits = select_memory(
        query="Which run fixed RES failure with memory taps?",
        zones=["run_archive"],
        project="pimc",
        stores=stores,
    )
    assert hits[0].record.id == record.id


def test_quarantine_review_promotes_real_memory_and_blocks_mock(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path / "knowledge")
    real = ingest_memory(
        zone="quarantine",
        text="Real reviewed methodology candidate.",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        source_path="tests/real.md",
        approved=False,
        stores=stores,
    )[0]
    mock = ingest_memory(
        zone="quarantine",
        text="mock placeholder",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        source_path="tests/mock.md",
        is_mock=True,
        approved=False,
        stores=stores,
    )[0]

    client = TestClient(create_app())
    promoted = client.post(
        f"/api/knowledge/quarantine/{real.id}/review",
        json={"action": "approve", "target_zone": "methodology"},
    )
    assert promoted.status_code == 200, promoted.text
    assert stores.zone("methodology").all(exclude_mock=False)

    blocked = client.post(
        f"/api/knowledge/quarantine/{mock.id}/review",
        json={"action": "approve", "target_zone": "methodology"},
    )
    assert blocked.status_code == 409


def test_conflict_and_memory_evals(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path / "knowledge")
    record = ingest_memory(
        zone="methodology",
        text="Hard routing improves RES.",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        eval_status=EvalStatus(passed=True, decision="pass"),
        approved=True,
        stores=stores,
    )[0]

    conflict = assess_conflict(
        old_text="Hard routing improves RES.",
        new_text="Hard routing does not improve RES.",
    )
    assert conflict.decision in {"conflict", "complementary", "duplicate_or_update"}

    precision = evaluate_retrieval_precision(
        query="hard routing RES",
        expected_record_ids={record.id},
        zones=["methodology"],
        stores=stores,
    )
    assert precision.passed

    pollution = evaluate_pollution(
        query="hard routing RES",
        zones=["methodology"],
        stores=stores,
    )
    assert pollution.passed
