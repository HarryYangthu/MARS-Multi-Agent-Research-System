"""Real Agents at admission, SQLite, files and processes; no execution doubles."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

from filelock import Timeout
import pytest

from app.agents.coding.agent import CodingAgent
from app.bridge.agent_registry import AgentRegistry
from app.bridge.agent_runner import run_agent_node
from app.bridge.research_stage_runtime import (
    BoundResearchStage, existing_research_ledger, load_bound_research_stage,
    record_research_stage_result, research_stage_dispatch,
)
from app.harness.runtime.project_scope import current_project_scope
from app.harness.runtime.research_execution_scope import bound_research_execution
from app.harness.runtime.state_machine import NodeState
from app.harness.runtime.task_contract import FailureEnvelope, ResultEnvelope
from app.harness.tools.registry import ToolContext, get_registry
from app.storage.artifact_store import ArtifactStore
from app.storage.run_state_store import RunStateStore
from tests.unit.test_research_stage_service import fixture, bind, approved
from tests.unit.test_research_scope_recovery import files


def transition(stage: BoundResearchStage, *, status: str = "running", state: NodeState = NodeState.RUNNING) -> None:
    payload = stage.ledger.journal.read()
    snapshot = RunStateStore(stage.run)._snapshot(payload)
    snapshot.graph.restore_state(stage.task.node_id, state)
    RunStateStore(stage.run).write(graph=snapshot.graph, request=snapshot.request, status=status,
                                  expected_revision=payload["revision"])


def ready(root: Path) -> BoundResearchStage:
    run, scope, ledger = fixture(root)
    task = bind(run, ledger)
    transition(BoundResearchStage(run, task, ledger, scope, {}))
    return load_bound_research_stage(run, "coding", agent=CodingAgent())


def authored_result(stage: BoundResearchStage, *, project: str | None = None) -> ResultEnvelope:
    """Human-authored schema input only, never evidence of model execution."""
    ref = ArtifactStore(stage.run).write_metadata(metadata={"schema": "code_spec.v1",
        "project": project or stage.run.project, "agent": "coding", "target_lang": "python",
        "baseline_compat": {"preserved": True}, "files_changed": []},
        body="Human-authored schema record; no model or experiment has run.", expected_schema="code_spec.v1")
    return ResultEnvelope(task_id=stage.task.task_id, invocation_id=stage.task.invocation_id,
        status="awaiting_review", artifact_ref=ref.path.relative_to(stage.run.root).as_posix(),
        artifact_sha256=hashlib.sha256(ref.path.read_bytes()).hexdigest(), schema_valid=True)


def dispatch_row(stage: BoundResearchStage) -> Any:
    with stage.ledger.journal.connection() as connection:
        return connection.execute("SELECT * FROM research_stage_dispatches").fetchone()


def test_readonly_restore_exact_context_and_original_budget_after_source_removed(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    (tmp_path / "source").rename(tmp_path / "source-gone")
    before = files(stage.run.root)
    again = load_bound_research_stage(stage.run, "coding", agent=CodingAgent())
    assert again.task == stage.task and again.project_scope == stage.project_scope
    assert again.upstream == stage.upstream and set(again.upstream) == {"host:frozen_contract", "host:project_rules"}
    assert existing_research_ledger(stage.run).snapshot() == stage.ledger.snapshot()
    assert files(stage.run.root) == before


@pytest.mark.parametrize("status,state", [("created", NodeState.RUNNING), ("paused", NodeState.RUNNING),
    ("cancelled", NodeState.FAILED), ("running", NodeState.PENDING), ("running", NodeState.DONE)])
def test_non_running_authority_blocks_restore_and_late_dispatch(tmp_path: Path, status: str, state: NodeState) -> None:
    stage = ready(tmp_path)
    transition(stage, status=status, state=state)
    before = stage.ledger.journal.path.read_bytes()
    with pytest.raises(ValueError, match="running SQL"):
        load_bound_research_stage(stage.run, "coding", agent=CodingAgent())
    with pytest.raises(ValueError, match="running SQL"):
        with research_stage_dispatch(stage):
            pytest.fail("invalid state dispatched")
    assert stage.ledger.journal.path.read_bytes() == before


@pytest.mark.parametrize("change", ["model", "tools", "policy", "post_training"])
def test_actual_agent_effective_configuration_drift_is_refused(tmp_path: Path, change: str) -> None:
    stage = ready(tmp_path)
    original = CodingAgent()
    if change == "model":
        agent = CodingAgent(agent_config=replace(original.config, model_name="unbound-model"))
    elif change == "tools":
        agent = CodingAgent(agent_config=replace(original.config, tools=()))
    elif change == "policy":
        agent = original
        agent._loop_policy = replace(original.loop_policy, max_tool_steps=1)
    else:
        agent = original
        agent.load_post_training({"enabled": True, "mode": "fine_tuned_id", "fine_tuned_model_id": "unbound-model"})
    before = files(stage.run.root)
    with pytest.raises(ValueError):
        load_bound_research_stage(stage.run, "coding", agent=agent)
    assert files(stage.run.root) == before


def test_single_effect_claim_scope_and_actual_tool_accounting(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    with research_stage_dispatch(stage):
        scope = bound_research_execution()
        assert scope is not None and scope.invocation_id == stage.task.invocation_id
        assert current_project_scope(stage.run.project, stage.run.run_id) == stage.project_scope
        with pytest.raises(Timeout):
            with research_stage_dispatch(stage):
                pytest.fail("same-thread owner was reentrant")
        ctx = ToolContext(run_id=stage.run.run_id, project=stage.run.project, agent="coding",
                          extra={"run_root": str(stage.run.root)})
        result = asyncio.run(get_registry().dispatch("code.write_file", {"path": "candidate.py", "content": "VALUE = 4\n"}, ctx))
        assert result.ok
    assert bound_research_execution() is None
    assert stage.project_scope.resolve_file("candidate.py").read_text() == "VALUE = 4\n"
    assert (tmp_path / "source/candidate.py").read_text() == "VALUE = 1\n"
    assert stage.ledger.snapshot().used["tool_executions"] == 1
    with pytest.raises(ValueError, match="already dispatched"):
        with research_stage_dispatch(stage):
            pytest.fail("unknown effect replayed")
    assert dispatch_row(stage)[3:] == (None, None)
    assert not (stage.run.root / "input/task_contracts").exists()


def test_result_receipt_is_immutable_and_does_not_revive_cancelled_graph(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    with research_stage_dispatch(stage):
        transition(stage, status="cancelled", state=NodeState.FAILED)
        result = authored_result(stage)
        before = stage.ledger.journal.read()
        record_research_stage_result(stage, result)
    record_research_stage_result(stage, result)
    assert stage.ledger.journal.read() == before
    assert ResultEnvelope.model_validate_json(dispatch_row(stage)[3]) == result
    failure = FailureEnvelope(task_id=stage.task.task_id, invocation_id=stage.task.invocation_id,
                              code="authored_test_failure", message="A different authored outcome")
    with pytest.raises(ValueError, match="immutable"):
        record_research_stage_result(stage, ResultEnvelope(task_id=stage.task.task_id,
            invocation_id=stage.task.invocation_id, status="failed", failure=failure))


@pytest.mark.parametrize("damage", ["foreign_project", "wrong_hash", "wrong_invocation", "approved_pointer", "foreign_failure"])
def test_result_requires_exact_producer_and_versioned_artifact(tmp_path: Path, damage: str) -> None:
    stage = ready(tmp_path)
    with research_stage_dispatch(stage):
        result = authored_result(stage, project="other" if damage == "foreign_project" else None)
        if damage == "wrong_hash":
            result = result.model_copy(update={"artifact_sha256": "0" * 64})
        elif damage == "wrong_invocation":
            result = result.model_copy(update={"invocation_id": "another"})
        elif damage == "approved_pointer":
            path = stage.run.root / str(result.artifact_ref)
            pointer = path.with_name("code_spec.approved.md")
            pointer.write_bytes(path.read_bytes())
            result = result.model_copy(update={"artifact_ref": pointer.relative_to(stage.run.root).as_posix()})
        elif damage == "foreign_failure":
            result = ResultEnvelope(task_id=stage.task.task_id, invocation_id=stage.task.invocation_id,
                status="failed", failure=FailureEnvelope(task_id="foreign", invocation_id=stage.task.invocation_id,
                    code="authored", message="authored input"))
        with pytest.raises(ValueError):
            record_research_stage_result(stage, result)
    assert dispatch_row(stage)[3:] == (None, None)


def test_sql_rejects_result_without_replaying_or_deleting_artifact(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    with research_stage_dispatch(stage):
        with sqlite3.connect(stage.ledger.journal.path) as connection:
            connection.execute("CREATE TRIGGER reject_stage_result BEFORE UPDATE ON research_stage_dispatches BEGIN SELECT RAISE(ABORT, 'actual SQL rejection'); END")
        result = authored_result(stage)
        with pytest.raises(ValueError):
            record_research_stage_result(stage, result)
    assert dispatch_row(stage)[3:] == (None, None)
    assert (stage.run.root / str(result.artifact_ref)).is_file()
    with pytest.raises(ValueError, match="already dispatched"):
        with research_stage_dispatch(stage):
            pytest.fail("receipt failure replayed")


def test_crashed_real_process_leaves_durable_intent_and_releases_lease(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    code = "\n".join([
        "import os,sys", "from pathlib import Path",
        "from app.storage.run_store import RunStore", "from app.agents.coding.agent import CodingAgent",
        "from app.bridge.research_stage_runtime import load_bound_research_stage,research_stage_dispatch",
        "run=RunStore(Path(sys.argv[1])).get(sys.argv[2])",
        "stage=load_bound_research_stage(run,'coding',agent=CodingAgent())",
        "with research_stage_dispatch(stage): os._exit(19)",
    ])
    child = subprocess.run([sys.executable, "-c", code, str(stage.run.root.parent), stage.run.run_id], capture_output=True, timeout=15)
    assert child.returncode == 19, child.stderr.decode()
    assert dispatch_row(stage)[3:] == (None, None)
    with pytest.raises(ValueError, match="already dispatched"):
        with research_stage_dispatch(stage):
            pytest.fail("crashed stage replayed")
    assert stage.ledger.snapshot().used["model_requests"] == 0


def test_runner_refuses_unbound_contract_even_with_seed_artifact(tmp_path: Path) -> None:
    run, _scope, ledger = fixture(tmp_path)
    registry = AgentRegistry()
    registry.register("coding", CodingAgent())
    (run.root / "coding/code_spec.v1.md").write_text("Seed is not new Agent execution evidence")
    with pytest.raises(ValueError):
        asyncio.run(run_agent_node(run, "coding", registry=registry))
    assert ledger.snapshot().used["model_requests"] == 0
    assert not (run.root / "input/task_results").exists()


def test_missing_file_and_meta_markers_do_not_downgrade_sql_contract_to_legacy(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    before = stage.ledger.journal.path.read_bytes()
    (stage.run.root / "input/research_task.v1.json").unlink()
    stage.run.meta.pop("research_task_sha256", None)
    meta = stage.run.root / "run_meta.json"
    value = json.loads(meta.read_text())
    value.pop("research_task_sha256", None)
    meta.write_text(json.dumps(value))
    registry = AgentRegistry()
    registry.register("coding", CodingAgent())
    with pytest.raises(ValueError):
        asyncio.run(run_agent_node(stage.run, "coding", registry=registry))
    assert stage.ledger.journal.path.read_bytes() == before


@pytest.mark.parametrize("option", ["revision", "resume", "predecessors"])
def test_runner_rejects_unbound_continuation_before_dispatch(tmp_path: Path, option: str) -> None:
    stage = ready(tmp_path)
    registry = AgentRegistry()
    registry.register("coding", CodingAgent())
    arguments: dict[str, Any] = {"revision_reason": "changed goal"} if option == "revision" else (
        {"resume_invocation": stage.task.invocation_id} if option == "resume" else {"predecessor_task_ids": ["foreign"]})
    before = files(stage.run.root)
    with pytest.raises(ValueError):
        asyncio.run(run_agent_node(stage.run, "coding", registry=registry, **arguments))
    assert files(stage.run.root) == before


def test_actual_missing_provider_stores_failure_for_the_same_sql_invocation(tmp_path: Path) -> None:
    import os
    stage = ready(tmp_path)
    code = "\n".join([
        "import asyncio,sys", "from pathlib import Path",
        "from app.storage.run_store import RunStore", "from app.agents.coding.agent import CodingAgent",
        "from app.bridge.agent_registry import AgentRegistry", "from app.bridge.agent_runner import run_agent_node",
        "run=RunStore(Path(sys.argv[1])).get(sys.argv[2])",
        "registry=AgentRegistry()", "registry.register('coding',CodingAgent())",
        "try: asyncio.run(run_agent_node(run,'coding',registry=registry))",
        "except RuntimeError as error:",
        "    if 'not configured' not in str(error): raise",
        "else: raise AssertionError('missing credential claimed success')",
    ])
    env = {**os.environ, "ZHIPU_API_KEY": "", "MARS_CODING_BACKEND": "native_llm"}
    child = subprocess.run([sys.executable, "-c", code, str(stage.run.root.parent), stage.run.run_id],
                           env=env, capture_output=True, timeout=15)
    assert child.returncode == 0, child.stderr.decode()
    result = ResultEnvelope.model_validate_json(dispatch_row(stage)[3])
    assert result.invocation_id == stage.task.invocation_id and result.status == "failed"
    assert result.failure is not None and result.failure.task_id == stage.task.task_id
    assert not (stage.run.root / "coding/code_spec.v1.md").exists()
    assert not (stage.run.root / "input/task_results").exists()
    assert stage.ledger.snapshot().used["model_requests"] == 0


def test_upstream_context_uses_bound_approved_source_without_latest_pointer(tmp_path: Path) -> None:
    run, scope, ledger = fixture(tmp_path, dependencies=True)
    reference = approved(run)
    task = bind(run, ledger, upstream=(reference,))
    (run.root / "experiment/experiment_plan.approved.md").unlink()
    transition(BoundResearchStage(run, task, ledger, scope, {}))
    before = files(run.root)
    stage = load_bound_research_stage(run, "coding", agent=CodingAgent())
    assert stage.upstream["experiment/experiment_plan.v1.md"] == (run.root / "experiment/experiment_plan.v1.md").read_text()
    assert files(run.root) == before
