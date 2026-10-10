"""Real immutable artifacts, SQLite writers and process recovery; no doubles."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from typing import Any

import pytest
import yaml

from app.bridge.research_scope_recovery import seal_project_scope
from app.bridge.research_stage_service import ApprovedStageInput, bind_research_stage, restore_research_stage
from app.harness.runtime.project_scope import ProjectScope
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import NodeState
from app.storage.artifact_store import ArtifactStore
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunHandle
from tests.unit.test_research_scope_recovery import fixture as scope_fixture, files
from tests.unit.test_research_tool_accounting import call, patch


def fixture(root: Path, *, dependencies: bool = False, node: str = "coding", attempt: int = 1,
            state: NodeState = NodeState.PENDING) -> tuple[RunHandle, ProjectScope, ResearchBudgetLedger]:
    run, scope, ledger = scope_fixture(root)
    graph = RunGraph()
    if dependencies:
        graph.add_node("experiment", metadata={"stage": "experiment", "attempt": 1})
        graph.restore_state("experiment", NodeState.APPROVED)
    graph.add_node(node, metadata={"stage": "coding", "attempt": attempt})
    graph.restore_state(node, state)
    if dependencies:
        graph.add_edge("experiment", node)
    previous = ledger.journal.read()
    RunStateStore(run).write(graph=graph, request=previous["request"], status="created", expected_revision=previous["revision"])
    seal_project_scope(run, scope, ledger)
    return run, scope, ledger


def bind(run: RunHandle, ledger: ResearchBudgetLedger, **changes: Any) -> Any:
    args = {"node_key": "coding", "candidate_id": "one", "ledger": ledger,
            "goal": "Actual file tool checks", "output_schema": "code_spec.v1",
            "host_context": ("frozen_contract", "project_rules"), **changes}
    return bind_research_stage(run, **args)


def approved(run: RunHandle, *, project: str | None = None) -> ApprovedStageInput:
    store = ArtifactStore(run)
    reference = store.write_metadata(metadata={"schema": "experiment_plan.v1", "project": project or run.project,
        "agent": "experiment", "variables": {"independent": ["scale"], "dependent": ["mse"]},
        "metrics": {"primary": "mse"}, "ablations": [{"name": "authored_plan", "config": {"seed": 0, "budget_steps": 1}}],
        "estimated_runs": 1}, body="Human-authored and approved plan, not a model or experiment result.",
        expected_schema="experiment_plan.v1")
    store.approve(reference)
    return ApprovedStageInput(predecessor_node="experiment", schema_id="experiment_plan.v1",
        stem=reference.stem, source_version=reference.version, approval_sequence=1)


def test_binding_repeated_and_restore_readonly_with_same_task_envelope(tmp_path: Path) -> None:
    run, scope, ledger = fixture(tmp_path)
    state_before = ledger.journal.read()
    task = bind(run, ledger)
    assert task.node_id == "coding" and task.agent == "coding" and task.attempt == 1
    assert task.task_id == f"{run.run_id}:coding" and task.run_id == run.run_id
    before = files(run.root)
    assert bind(run, ledger) == task
    assert restore_research_stage(run, node_key="coding", ledger=ledger) == task
    assert files(run.root) == before and ledger.journal.read() == state_before
    assert not (run.root / "input/task_contracts").exists()
    assert not (run.root / "agent_traces").exists()
    assert ledger.snapshot().used["model_requests"] == 0
    (tmp_path / "source").rename(tmp_path / "source-gone")
    assert restore_research_stage(run, node_key="coding", ledger=ledger) == task
    assert scope.resolve_file("candidate.py").read_text() == "VALUE = 1\n"


def test_writing_skill_admission_is_in_the_sealed_inputs_and_restore_is_readonly(tmp_path: Path) -> None:
    from app.bridge.report_skill_binding import snapshot_ref
    run, scope, ledger = scope_fixture(tmp_path)
    graph = RunGraph()
    graph.add_node("writing", metadata={"stage": "writing", "attempt": 1})
    previous = ledger.journal.read()
    RunStateStore(run).write(graph=graph, request=previous["request"], status="created", expected_revision=previous["revision"])
    seal_project_scope(run, scope, ledger)
    task = bind_research_stage(run, node_key="writing", candidate_id="one", ledger=ledger,
        goal="Actual file tool checks", output_schema="report.v1")
    skill_path = run.root / snapshot_ref("writing")
    assert json.loads(skill_path.read_text())["ids"] == []
    before = files(run.root)
    assert restore_research_stage(run, node_key="writing", ledger=ledger) == task
    assert files(run.root) == before
    skill_path.write_text(skill_path.read_text() + " ")
    with pytest.raises(ValueError, match="changed"):
        restore_research_stage(run, node_key="writing", ledger=ledger)


def test_missing_lookup_never_installs_table_or_files(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path)
    before = files(run.root)
    with pytest.raises(ValueError):
        restore_research_stage(run, node_key="coding", ledger=ledger)
    assert files(run.root) == before
    with sqlite3.connect(ledger.journal.path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='research_stage_invocations'").fetchone() is None


@pytest.mark.parametrize("damage", ["outbox_missing", "outbox_column", "outbox_view", "state_missing", "identity_missing"])
def test_incomplete_core_journal_cannot_restore_or_bind_stage(tmp_path: Path, damage: str) -> None:
    from app.bridge.research_scope_recovery import restore_project_scope
    run, _scope, ledger = fixture(tmp_path)
    bind(run, ledger)
    with sqlite3.connect(ledger.journal.path) as connection:
        if damage == "outbox_missing":
            connection.execute("DROP TABLE state_events")
        elif damage == "outbox_column":
            connection.execute("ALTER TABLE state_events RENAME COLUMN published TO damaged")
        elif damage == "outbox_view":
            connection.execute("ALTER TABLE state_events RENAME TO removed_events")
            connection.execute("CREATE VIEW state_events AS SELECT * FROM removed_events")
        else:
            connection.execute("DROP TABLE " + ("run_state" if damage == "state_missing" else "identity"))
    before = files(run.root)
    with pytest.raises(ValueError):
        restore_research_stage(run, node_key="coding", ledger=ledger)
    with pytest.raises(ValueError):
        restore_project_scope(run, candidate_id="one", ledger=ledger)
    with pytest.raises(ValueError):
        bind(run, ledger)
    assert files(run.root) == before


def test_approved_dependency_uses_immutable_source_receipt_without_pointer_repair(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path, dependencies=True)
    reference = approved(run)
    task = bind(run, ledger, upstream=(reference,))
    pointer = run.root / "experiment/experiment_plan.approved.md"
    pointer.unlink()
    before = files(run.root)
    restored = restore_research_stage(run, node_key="coding", ledger=ledger)
    assert restored == task and not pointer.exists() and files(run.root) == before
    assert task.predecessor_task_ids == [f"{run.run_id}:experiment"]
    assert "experiment/experiment_plan.v1.md" in task.required_context_refs


@pytest.mark.parametrize("change", ["goal", "schema", "candidate", "host_context", "foreign_ledger", "foreign_run"])
def test_changed_input_and_cross_run_bindings_refused(tmp_path: Path, change: str) -> None:
    run, _scope, ledger = fixture(tmp_path / "a")
    other, _other_scope, other_ledger = fixture(tmp_path / "b")
    bind(run, ledger)
    changes: dict[str, Any] = {}
    if change == "goal":
        changes["goal"] = "A different task"
    elif change == "schema":
        changes["output_schema"] = "report.v1"
    elif change == "candidate":
        changes["candidate_id"] = "two"
    elif change == "host_context":
        changes["host_context"] = ("frozen_contract",)
    elif change == "foreign_ledger":
        ledger = other_ledger
    else:
        run = other
    before = files(tmp_path)
    with pytest.raises(ValueError):
        bind(run, ledger, **changes)
    assert files(tmp_path) == before


@pytest.mark.parametrize("change", ["missing", "arbitrary_text", "foreign_project", "unapproved", "wrong_receipt_version", "non_dependency"])
def test_upstream_must_be_real_approved_dependency_evidence(tmp_path: Path, change: str) -> None:
    run, _scope, ledger = fixture(tmp_path, dependencies=True)
    upstream: Any = ()
    if change == "arbitrary_text":
        upstream = ("Pretend the upstream succeeded",)
    elif change != "missing":
        ref = approved(run, project="another" if change == "foreign_project" else None)
        upstream = (ref,)
        if change == "unapproved":
            (run.root / "experiment/.approvals/experiment_plan/00000000000000000001.json").unlink()
        elif change == "wrong_receipt_version":
            upstream = (ref.model_copy(update={"source_version": "v2"}),)
        elif change == "non_dependency":
            upstream = (ref.model_copy(update={"predecessor_node": "idea"}),)
    before = files(run.root)
    with pytest.raises((ValueError, FileNotFoundError)):
        bind(run, ledger, upstream=upstream)
    assert files(run.root) == before


@pytest.mark.parametrize("target", ["source", "receipt"])
@pytest.mark.parametrize("kind", ["tamper", "hardlink", "symlink"])
def test_saved_approved_input_tamper_and_alias_refused(tmp_path: Path, target: str, kind: str) -> None:
    run, _scope, ledger = fixture(tmp_path, dependencies=True)
    ref = approved(run)
    bind(run, ledger, upstream=(ref,))
    path = run.root / ("experiment/experiment_plan.v1.md" if target == "source" else
                       "experiment/.approvals/experiment_plan/00000000000000000001.json")
    outside = tmp_path / "outside-authored-evidence"
    outside.write_bytes(path.read_bytes())
    if kind == "tamper":
        path.write_text("changed")
    else:
        path.unlink()
        if kind == "hardlink":
            os.link(outside, path)
        else:
            path.symlink_to(outside)
    before = files(run.root)
    with pytest.raises(ValueError):
        restore_research_stage(run, node_key="coding", ledger=ledger)
    assert files(run.root) == before


def test_new_approval_cannot_rebind_original_invocation(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path, dependencies=True)
    ref = approved(run)
    original = bind(run, ledger, upstream=(ref,))
    newer = ArtifactStore(run).write(text=(run.root / "experiment/experiment_plan.v1.md").read_text() + "\nA second authored plan.\n",
                                    expected_schema="experiment_plan.v1")
    ArtifactStore(run).approve(newer)
    changed = ref.model_copy(update={"source_version": newer.version, "approval_sequence": 2})
    with pytest.raises(ValueError):
        bind(run, ledger, upstream=(changed,))
    assert restore_research_stage(run, node_key="coding", ledger=ledger) == original


@pytest.mark.parametrize("state", [NodeState.RUNNING, NodeState.WAITING_REVIEW, NodeState.DONE, NodeState.FAILED, NodeState.SKIPPED])
def test_no_new_invocation_for_unbound_non_pending_node(tmp_path: Path, state: NodeState) -> None:
    run, _scope, ledger = fixture(tmp_path, state=state)
    before = files(run.root)
    with pytest.raises(ValueError):
        bind(run, ledger)
    assert files(run.root) == before


def test_original_task_survives_real_graph_transition_without_executing(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path)
    task = bind(run, ledger)
    previous = ledger.journal.read()
    graph = RunGraph.from_dict(previous["graph"])
    graph.transition("coding", NodeState.RUNNING)
    RunStateStore(run).write(graph=graph, request=previous["request"], status="running", expected_revision=previous["revision"])
    before = files(run.root)
    assert bind(run, ledger) == task
    assert restore_research_stage(run, node_key="coding", ledger=ledger) == task
    assert files(run.root) == before and ledger.journal.read()["status"] == "running"


def test_dynamic_attempt_uses_existing_node_identity(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path, node="coding_attempt_2", attempt=2)
    task = bind(run, ledger, node_key="coding_attempt_2")
    assert task.node_id == "coding_attempt_2" and task.attempt == 2 and task.agent == "coding"


@pytest.mark.parametrize("damage", ["attempt", "stage", "kind", "predecessors"])
def test_changed_sql_graph_identity_is_not_silently_reused(tmp_path: Path, damage: str) -> None:
    run, _scope, ledger = fixture(tmp_path)
    bind(run, ledger)
    with ledger.journal.transaction() as connection:
        payload = ledger.journal._read(connection)
        node = payload["graph"]["nodes"][0]
        if damage == "attempt":
            node["metadata"]["attempt"] = 2
        elif damage == "stage":
            node["metadata"]["stage"] = "writing"
        elif damage == "kind":
            node["kind"] = "gate"
        else:
            payload["graph"]["nodes"].append({"key": "idea", "kind": "agent", "state": "skipped", "metadata": {"stage": "idea", "attempt": 1}})
            payload["graph"]["edges"].append({"src": "idea", "dst": "coding"})
        ledger.journal.commit_in_transaction(connection, payload, expected_revision=payload["revision"])
    before = files(run.root)
    with pytest.raises(ValueError):
        restore_research_stage(run, node_key="coding", ledger=ledger)
    assert files(run.root) == before


@pytest.mark.parametrize("damage", ["hash", "column_id", "task_id", "missing", "drop"])
def test_broken_sql_binding_never_recreates_invocation_on_read(tmp_path: Path, damage: str) -> None:
    run, _scope, ledger = fixture(tmp_path)
    bind(run, ledger)
    with sqlite3.connect(ledger.journal.path) as connection:
        if damage == "hash":
            connection.execute("UPDATE research_stage_invocations SET binding_sha256=?", ("0" * 64,))
        elif damage == "column_id":
            connection.execute("UPDATE research_stage_invocations SET invocation_id='another'")
        elif damage == "task_id":
            raw = json.loads(connection.execute("SELECT binding FROM research_stage_invocations").fetchone()[0])
            raw["task"]["task_id"] = "forged"
            encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            connection.execute("UPDATE research_stage_invocations SET binding=?,binding_sha256=?", (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        elif damage == "missing":
            connection.execute("DELETE FROM research_stage_invocations")
        else:
            connection.execute("DROP TABLE research_stage_invocations")
    before = files(run.root)
    with pytest.raises(ValueError):
        restore_research_stage(run, node_key="coding", ledger=ledger)
    assert files(run.root) == before


def test_actual_sql_insert_failure_rolls_back_without_task_trace(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path)
    with sqlite3.connect(ledger.journal.path) as connection:
        connection.execute("CREATE TABLE research_stage_invocations (node_key TEXT PRIMARY KEY, invocation_id TEXT NOT NULL UNIQUE, version INTEGER NOT NULL CHECK(version=1), binding TEXT NOT NULL,binding_sha256 TEXT NOT NULL)")
        connection.execute("CREATE TRIGGER reject_stage BEFORE INSERT ON research_stage_invocations BEGIN SELECT RAISE(ABORT, 'authored transaction refusal'); END")
    before = files(run.root)
    with pytest.raises(ValueError):
        bind(run, ledger)
    assert files(run.root) == before
    with sqlite3.connect(ledger.journal.path) as connection:
        assert connection.execute("SELECT count(*) FROM research_stage_invocations").fetchone()[0] == 0


def test_actual_failed_tool_usage_is_not_reset_by_stage_restore(tmp_path: Path) -> None:
    run, scope, ledger = fixture(tmp_path)
    task = bind(run, ledger)
    from app.harness.runtime.research_execution_scope import ResearchExecutionScope
    from app.harness.tools.registry import ToolContext
    execution = ResearchExecutionScope(ledger, stage="coding", invocation_id=task.invocation_id)
    ctx = ToolContext(run_id=run.run_id, project=run.project, agent="coding", extra={"run_root": str(run.root)})
    result = call(scope, execution, ctx, "code.apply_patch", {"diff": patch(before="VALUE = 999")})
    assert not result.ok
    usage = ledger.snapshot()
    before = files(run.root)
    assert restore_research_stage(run, node_key="coding", ledger=ledger) == task
    assert ledger.snapshot().used == usage.used and ledger.snapshot().unknown_reservations == usage.unknown_reservations
    assert usage.used["tool_executions"] == 1 and usage.unknown_reservations
    assert files(run.root) == before


_PROCESS = """
from pathlib import Path
import json, os, sys, time
from app.storage.run_store import RunStore
from app.bridge.research_run_service import load_run_research_contract
from app.bridge.research_stage_service import bind_research_stage, restore_research_stage
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.state_journal import StateJournal
root = Path(sys.argv[1])
run = RunStore(root.parent).get(root.name)
assert run is not None
frozen = load_run_research_contract(run)
assert frozen is not None
journal = StateJournal.from_authority(root, run_id=run.run_id)
assert journal is not None
ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget)
if len(sys.argv) > 3:
    while not Path(sys.argv[3]).exists(): time.sleep(.01)
if sys.argv[2] == 'bind':
    task = bind_research_stage(run, node_key='coding', candidate_id='one', ledger=ledger, goal=frozen.task.goal,
        output_schema='code_spec.v1', host_context=('frozen_contract', 'project_rules'))
else:
    task = restore_research_stage(run, node_key='coding', ledger=ledger)
sys.stdout.write(task.model_dump_json() + '\\n'); sys.stdout.flush()
os._exit(0)
"""


def process(run: RunHandle, action: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", _PROCESS, str(run.root), action], capture_output=True, text=True, timeout=20, env=env)


def test_real_process_exit_restore_and_concurrent_writers_share_one_id(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path)
    barrier = tmp_path / "go"
    workers = [subprocess.Popen([sys.executable, "-c", _PROCESS, str(run.root), "bind", str(barrier)],
               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    try:
        barrier.write_text("Host releases the real SQLite writers.\n")
        outputs = [worker.communicate(timeout=25) for worker in workers]
        assert all(worker.returncode == 0 for worker in workers), outputs
        ids = {json.loads(out)["invocation_id"] for out, _err in outputs}
        assert len(ids) == 1
        with sqlite3.connect(ledger.journal.path) as connection:
            assert connection.execute("SELECT count(*) FROM research_stage_invocations").fetchone()[0] == 1
        before = files(run.root)
        restored = process(run, "restore")
        assert restored.returncode == 0, restored.stderr
        assert json.loads(restored.stdout)["invocation_id"] in ids and files(run.root) == before
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)


@pytest.mark.parametrize("change", ["loop", "tools", "context", "schema"])
def test_configuration_drift_after_process_restart_refused_without_secret_copy(tmp_path: Path, change: str) -> None:
    run, _scope, _ledger = fixture(tmp_path)
    from app.settings import repo_root
    seed = tmp_path / "runtime-resources"
    shutil.copytree(repo_root() / "configs", seed / "configs")
    shutil.copytree(repo_root() / "backend/app/harness/schema/schemas", seed / "backend/app/harness/schema/schemas")
    (seed / "templates/artifacts").mkdir(parents=True)
    environment = {**os.environ, "MARS_RUNTIME_ROOT": str(seed)}
    first = process(run, "bind", env=environment)
    assert first.returncode == 0, first.stderr
    assert process(run, "restore", env=environment).returncode == 0
    if change in {"loop", "tools"}:
        agents = seed / "configs/agents.yaml"
        configuration = yaml.safe_load(agents.read_text())
        if change == "loop":
            configuration["coding"]["loop"]["max_tool_steps"] += 1
        else:
            configuration["coding"]["tools"].remove("code.lint")
        agents.write_text(yaml.safe_dump(configuration))
    else:
        target = seed / ("configs/agent_contexts/coding.yaml" if change == "context" else
                         "backend/app/harness/schema/schemas/code_spec.v1.json")
        target.write_text(target.read_text() + "\n")
    before = files(run.root)
    restored = process(run, "restore", env=environment)
    assert restored.returncode != 0 and "configuration changed" in restored.stderr
    assert files(run.root) == before


@pytest.mark.parametrize("damage", ["stage", "attempt", "kind", "not_approved"])
def test_upstream_node_identity_and_approval_state_are_authoritative(tmp_path: Path, damage: str) -> None:
    run, _scope, ledger = fixture(tmp_path, dependencies=True)
    reference = approved(run)
    with ledger.journal.transaction() as connection:
        payload = ledger.journal._read(connection)
        node = payload["graph"]["nodes"][0]
        if damage == "stage":
            node["metadata"]["stage"] = "idea"
        elif damage == "attempt":
            node["metadata"]["attempt"] = 9
        elif damage == "kind":
            node["kind"] = "gate"
        else:
            node["state"] = "waiting_review"
        ledger.journal.commit_in_transaction(connection, payload, expected_revision=payload["revision"])
    before = files(run.root)
    with pytest.raises(ValueError):
        bind(run, ledger, upstream=(reference,))
    assert files(run.root) == before


@pytest.mark.parametrize("kind", ["project_knowledge", "raw_unproven_context"])
def test_host_context_cannot_claim_missing_evidence_or_arbitrary_text(tmp_path: Path, kind: str) -> None:
    run, _scope, ledger = fixture(tmp_path)
    before = files(run.root)
    with pytest.raises(ValueError):
        bind(run, ledger, host_context=(kind,))
    assert files(run.root) == before


def test_unsealed_candidate_cannot_bind_stage(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path)
    with sqlite3.connect(ledger.journal.path) as connection:
        connection.execute("DELETE FROM research_project_scopes")
    before = files(run.root)
    with pytest.raises(ValueError):
        bind(run, ledger)
    assert files(run.root) == before


def test_second_valid_sealed_candidate_cannot_replace_original_binding(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path)
    original = bind(run, ledger)
    from app.bridge.research_project_scope import prepare_project_scope
    from app.harness.discovery.snapshots import SnapshotPolicy
    other = prepare_project_scope(run, candidate_id="two", snapshot_policy=SnapshotPolicy(allowed_paths=("*",)))
    seal_project_scope(run, other, ledger)
    before = files(run.root)
    with pytest.raises(ValueError, match="rebinding"):
        bind(run, ledger, candidate_id="two")
    assert restore_research_stage(run, node_key="coding", ledger=ledger) == original
    assert files(run.root) == before


def test_unknown_node_never_creates_a_task_or_changes_graph(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path)
    before = files(run.root)
    with pytest.raises(ValueError):
        bind(run, ledger, node_key="coding_attempt_2")
    assert files(run.root) == before
