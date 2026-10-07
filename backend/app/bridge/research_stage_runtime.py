"""Bind the existing Agent runner to sealed SQL identities and file permissions.

Dispatch receipts describe a single effect attempt, never a second RunGraph.
Preparation/restoration does not initialize budgets or permit product start.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, cast

from filelock import FileLock
import yaml

from app.bridge.research_run_service import load_run_research_contract, validate_saved_run_journal_schema
from app.bridge.research_scope_recovery import restore_project_scope
from app.bridge.research_stage_service import _decode, restore_research_stage
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.model_registry import get_agent_config
from app.harness.runtime.project_scope import ProjectScope, bind_project_scope, safe_scope_path, verify_candidate_scope
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.research_execution_scope import ResearchExecutionScope, Stage, bind_research_execution
from app.harness.runtime.state_journal import StateJournal
from app.harness.runtime.state_machine import NodeState
from app.harness.runtime.task_contract import ResultEnvelope, TaskEnvelope
from app.harness.schema.validator import validate_document
from app.settings import get_settings, repo_root
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunHandle


@dataclass(frozen=True)
class BoundResearchStage:
    run: RunHandle
    task: TaskEnvelope
    ledger: ResearchBudgetLedger
    project_scope: ProjectScope
    upstream: dict[str, str]
    skills: tuple[str, ...] = ()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def existing_research_ledger(run: RunHandle) -> ResearchBudgetLedger:
    """Open the exact saved budget policy without resetting any existing usage."""
    frozen = load_run_research_contract(run)
    if frozen is None:
        raise ValueError("Stage runtime requires a frozen research contract")
    for name in ("run_state.authority.json", "run_state.sqlite3"):
        safe_scope_path(run.root, name, must_exist=True)
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    if journal is None:
        raise ValueError("Stage runtime requires the existing SQL authority")
    with journal.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        validate_saved_run_journal_schema(connection)
        row = connection.execute("SELECT policy FROM research_budget WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Stage budget has not been initialized by its owner")
        policy = json.loads(row[0])
        if not isinstance(policy, dict):
            raise ValueError("Saved budget policy is invalid")
        ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget,
                                      price_reference_sha256=policy.get("price_reference_sha256"))
        ledger.in_transaction(connection).snapshot()
        return ledger


def _running(stage: BoundResearchStage, connection: sqlite3.Connection) -> None:
    stage.ledger.in_transaction(connection).snapshot()
    snapshot = RunStateStore(stage.run)._snapshot(stage.ledger.journal._read(connection))
    if snapshot.status != "running" or snapshot.graph.state(stage.task.node_id) != NodeState.RUNNING:
        raise ValueError("Stage dispatch requires its actual running SQL node and run")


def load_bound_research_stage(run: RunHandle, node_key: str, *, agent: Any) -> BoundResearchStage:
    ledger = existing_research_ledger(run)
    task = restore_research_stage(run, node_key=node_key, ledger=ledger)
    if task.agent == "execution":
        raise ValueError("Execution must use the contract job adapter, never the legacy Agent runner")
    with ledger.journal.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        binding = _decode(connection.execute(
            "SELECT version,binding,binding_sha256,invocation_id FROM research_stage_invocations WHERE node_key=?",
            (node_key,)).fetchone(), node_key)
    if binding.task != task:
        raise ValueError("Stage identity changed during restoration")
    scope = restore_project_scope(run, candidate_id=binding.inputs.candidate_id, ledger=ledger)
    config = getattr(agent, "config", None)
    policy = getattr(agent, "loop_policy", None)
    configured = yaml.safe_load((repo_root() / "configs/agents.yaml").read_bytes()).get(task.agent)
    if (getattr(agent, "name", None) != task.agent or getattr(agent, "output_schema", None) != task.output_schema
            or config != get_agent_config(task.agent) or not isinstance(configured, dict) or dict(config.raw) != configured
            or not isinstance(policy, AgentLoopPolicy)
            or policy.fingerprint_data() != AgentLoopPolicy.from_mapping(configured.get("loop", {})).fingerprint_data()):
        raise ValueError("Registered Agent configuration differs from the sealed stage")
    if task.agent == "coding" and (get_settings().mars_coding_backend != "native_llm"
            or getattr(getattr(agent, "post_training_handle", None), "enabled", True)):
        raise ValueError("Contract Coding requires its configured native model without runtime endpoint overrides")
    upstream: dict[str, str] = {}
    for evidence in binding.inputs.upstream:
        data = safe_scope_path(run.root, evidence.source_ref, must_exist=True).read_bytes()
        if _sha(data) != evidence.source_sha256:
            raise ValueError("Approved stage source changed before dispatch")
        upstream[evidence.source_ref] = data.decode("utf-8")
    for host_evidence in binding.inputs.host_context:
        if host_evidence.source == "frozen_contract":
            frozen = load_run_research_contract(run)
            if frozen is None:
                raise ValueError("Frozen context disappeared")
            value = frozen.model_dump(mode="json")
            if digest(value) != host_evidence.sha256:
                raise ValueError("Frozen context changed before dispatch")
            upstream["host:frozen_contract"] = canonical(value)
        else:
            name = "AGENTS.md" if host_evidence.source == "project_rules" else "knowledge.md"
            data = safe_scope_path(scope.metadata_root, name, must_exist=True).read_bytes()
            if _sha(data) != host_evidence.sha256:
                raise ValueError("Project context changed before dispatch")
            upstream["host:" + host_evidence.source] = data.decode("utf-8")
    skills: list[str] = []
    if task.agent == "writing":
        from app.bridge.report_skill_binding import frozen_report_skills
        skills = frozen_report_skills(run, node_key)
    stage = BoundResearchStage(run, task, ledger, scope, upstream, tuple(skills))
    with ledger.journal.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        _running(stage, connection)
    return stage


@contextmanager
def research_stage_dispatch(stage: BoundResearchStage, *, resume_invocation: str | None = None) -> Iterator[None]:
    """Claim once before invoking the actual Agent. Unknown dispatches never replay.

    Explicit checkpoint continuation is not yet admitted here: tool/model unknown
    evidence must be reconciled first. A fresh attempt needs a fresh graph node.
    """
    if resume_invocation is not None:
        raise ValueError("Contract stage continuation requires owner checkpoint reconciliation")
    path = safe_scope_path(stage.run.root, f"execution/stage_owners/{stage.task.invocation_id}.lock")
    path.parent.mkdir(exist_ok=True)
    # A fresh lock object avoids same-thread reentrancy between async tasks.
    with FileLock(path, timeout=0, thread_local=False):
        with stage.ledger.journal.transaction() as connection:
            _running(stage, connection)
            connection.execute("""CREATE TABLE IF NOT EXISTS research_stage_dispatches (
                invocation_id TEXT PRIMARY KEY, node_key TEXT NOT NULL UNIQUE,
                input_sha256 TEXT NOT NULL, result TEXT, result_sha256 TEXT)""")
            if connection.execute("SELECT 1 FROM research_stage_dispatches WHERE invocation_id=? OR node_key=?",
                                  (stage.task.invocation_id, stage.task.node_id)).fetchone() is not None:
                raise ValueError("Stage was already dispatched; do not repeat an unknown or completed invocation")
            connection.execute("INSERT INTO research_stage_dispatches VALUES (?,?,?,NULL,NULL)",
                               (stage.task.invocation_id, stage.task.node_id, stage.task.input_sha256))
        with bind_project_scope(stage.project_scope), bind_research_execution(
                ResearchExecutionScope(stage.ledger, cast(Stage, stage.task.agent), stage.task.invocation_id)):
            yield


def record_research_stage_result(stage: BoundResearchStage, result: ResultEnvelope) -> None:
    """Write an immutable producer-to-artifact receipt in the existing authority."""
    if result.task_id != stage.task.task_id or result.invocation_id != stage.task.invocation_id:
        raise ValueError("Stage result belongs to another invocation")
    if result.status == "awaiting_review":
        if not result.schema_valid or result.artifact_ref is None or result.artifact_sha256 is None:
            raise ValueError("Successful output requires its actual validated artifact")
        path = safe_scope_path(stage.run.root, result.artifact_ref, must_exist=True)
        content = path.read_bytes()
        validation = validate_document(content.decode("utf-8"), expected_schema=stage.task.output_schema)
        import re
        from app.storage.artifact_store import SCHEMA_TO_AGENT
        stem = SCHEMA_TO_AGENT[stage.task.output_schema][1]
        if (path.parent != stage.run.root / stage.task.agent
                or re.fullmatch(re.escape(stem) + r"\.v[1-9][0-9]*\.md", path.name) is None
                or _sha(content) != result.artifact_sha256 or result.failure is not None
                or not validation.valid or validation.metadata.get("project") != stage.run.project):
            raise ValueError("Stage output does not match its actual artifact evidence")
        verify_candidate_scope(stage.project_scope)
    elif (result.failure is None or result.failure.invocation_id != stage.task.invocation_id
            or result.failure.task_id != stage.task.task_id or result.schema_valid
            or result.artifact_ref is not None or result.artifact_sha256 is not None):
        raise ValueError("Failed stage needs matching failure evidence")
    encoded = canonical(result.model_dump(mode="json"))
    with stage.ledger.journal.transaction() as connection:
        # A late result may be retained after cancellation, but cannot transition
        # the run or authorize another model/tool call.
        stage.ledger.in_transaction(connection).snapshot()
        row = connection.execute("SELECT node_key,input_sha256,result,result_sha256 FROM research_stage_dispatches WHERE invocation_id=?",
                                 (stage.task.invocation_id,)).fetchone()
        if row is None or row[:2] != (stage.task.node_id, stage.task.input_sha256):
            raise ValueError("Result has no matching committed dispatch intent")
        if row[2] is not None:
            if row[2:] != (encoded, _sha(encoded.encode())):
                raise ValueError("Stage result is immutable")
            return
        connection.execute("UPDATE research_stage_dispatches SET result=?,result_sha256=? WHERE invocation_id=?",
                           (encoded, _sha(encoded.encode()), stage.task.invocation_id))


def validate_bound_handoffs(stage: BoundResearchStage) -> None:
    """Enforce prerequisites on the approved bytes actually bound to this stage."""
    from app.harness.schema.frontmatter_parser import parse
    from app.harness.runtime.task_contract import HandoffPrerequisite, missing_prerequisites
    for name, content in stage.upstream.items():
        if name.startswith("host:"):
            continue
        metadata = parse(content).metadata
        handoff = metadata.get("handoff", {})
        if not isinstance(handoff, dict):
            raise ValueError("Approved handoff declaration is invalid")
        prerequisites = [HandoffPrerequisite.model_validate(item) for item in handoff.get("required_context", [])]
        missing = missing_prerequisites(prerequisites, stage=stage.task.agent, supplied_context=stage.upstream)
        if missing:
            raise ValueError("Execution prerequisites missing from sealed context: " + ", ".join(missing))
