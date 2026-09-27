"""Authored state-ledger inputs evaluate persistence, never research success."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.harness.evaluation.run_evaluators import MultiAgentCollaborationEvaluator
from app.harness.evaluation.suites import EvaluationSuite, ExpectedOutcome
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_journal import StateJournal
from app.harness.runtime.state_machine import NodeState
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunHandle, RunStore


SUITE = EvaluationSuite(id="state_evidence_contract", expected=ExpectedOutcome(expected_stages=("idea", "experiment")))


def _journal_run(tmp_path: Path) -> tuple[RunHandle, RunStateStore, RunGraph]:
    run = RunStore(tmp_path).create(task="state-evaluation-contract", project="pimc", entrypoint="pipeline")
    graph = RunGraph()
    graph.add_node("idea")
    graph.add_node("experiment")
    graph.add_edge("idea", "experiment")
    store = RunStateStore(run)
    store.write(graph=graph, request={}, status="created", expected_revision=0)
    return run, store, graph


def test_only_committed_events_count_even_when_projection_lags(tmp_path: Path) -> None:
    run, store, graph = _journal_run(tmp_path)
    evaluator = MultiAgentCollaborationEvaluator()
    assert evaluator.evaluate_run(run=run, suite=SUITE).scores["routing_stage_coverage"] == 0
    graph.transition("idea", NodeState.RUNNING)
    store.write(graph=graph, request={}, status="running", expected_revision=1)
    # Deliberately stale caller-authored projection must not establish evidence.
    store.path.write_text(json.dumps({"states": {"experiment": "pending"}}))
    (run.root / "events/agent_events.jsonl").write_text(json.dumps({"agent": "experiment"}) + "\n")
    for published in (False, True):
        if published:
            event = store.pending_events()[0]
            store.mark_published(event["event_id"])
        report = evaluator.evaluate_run(run=run, suite=SUITE)
        assert report.scores["routing_stage_coverage"] == 0.5
        missing = [finding for finding in report.findings if finding.id == "expected_stage_missing"]
        assert len(missing) == 1 and "`experiment`" in missing[0].message
        assert missing[0].evidence_refs == ("run_state.sqlite3#state_events",)
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    assert journal and len(journal.all_events()) == 1 and journal.pending_events() == []


@pytest.mark.parametrize("damage", ["missing_db", "bad_db", "missing_marker", "bad_marker", "both_missing", "bad_event"])
def test_broken_authority_reports_unknown_never_json_fallback(tmp_path: Path, damage: str) -> None:
    run, store, graph = _journal_run(tmp_path)
    graph.transition("idea", NodeState.RUNNING)
    store.write(graph=graph, request={}, status="running", expected_revision=1)
    (run.root / "events/agent_events.jsonl").write_text(
        json.dumps({"agent": "idea"}) + "\n" + json.dumps({"agent": "experiment"}) + "\n")
    if damage in {"missing_db", "both_missing"}:
        store.database_path.unlink()
    if damage in {"missing_marker", "both_missing"}:
        store.authority_path.unlink()
    if damage == "bad_db":
        store.database_path.write_bytes(b"unavailable sqlite")
    if damage == "bad_marker":
        store.authority_path.write_text("invalid authority")
    if damage == "bad_event":
        with sqlite3.connect(store.database_path) as connection:
            connection.execute("UPDATE state_events SET payload='{}'")
    report = MultiAgentCollaborationEvaluator().evaluate_run(run=run, suite=SUITE)
    assert report.decision == "warn" and report.overall_score is None and report.scores == {}
    assert [finding.id for finding in report.findings] == ["state_evidence_unavailable"]


def test_uncommitted_rows_are_not_evidence(tmp_path: Path) -> None:
    run, store, _ = _journal_run(tmp_path)
    with sqlite3.connect(store.database_path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        payload = json.loads(writer.execute("SELECT payload FROM run_state").fetchone()[0])
        payload["revision"] = 2
        payload["graph"]["nodes"][0]["state"] = "running"
        writer.execute("UPDATE run_state SET revision=2,payload=?", (json.dumps(payload),))
        event = {"event": "agent_state", "event_id": "uncommitted", "revision": 2,
                 "run_id": run.run_id, "agent": "idea", "from_state": "pending", "to_state": "running"}
        writer.execute("INSERT INTO state_events(event_id,revision,payload) VALUES ('uncommitted',2,?)", (json.dumps(event),))
        report = MultiAgentCollaborationEvaluator().evaluate_run(run=run, suite=SUITE)
        assert report.scores["routing_stage_coverage"] == 0
        writer.rollback()


@pytest.mark.parametrize("state_map", [False, True])
def test_legacy_keeps_existing_json_or_jsonl_reading(tmp_path: Path, state_map: bool) -> None:
    run = RunStore(tmp_path).create(task="legacy-evaluation-contract", project="pimc")
    if state_map:
        (run.root / "run_state.json").write_text(json.dumps({"states": {"idea": "running"}}))
    else:
        (run.root / "events/agent_events.jsonl").write_text(json.dumps({"agent": "idea"}) + "\n")
    report = MultiAgentCollaborationEvaluator().evaluate_run(run=run, suite=SUITE)
    assert report.scores["routing_stage_coverage"] == 0.5
    assert not any(finding.id == "state_evidence_unavailable" for finding in report.findings)
