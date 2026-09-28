"""Immutable TaskEnvelope bindings in the existing run authority.

This service records inputs, not a second stage lifecycle. It never starts an
Agent, grants a tool, repairs artifacts, creates traces or transitions a node.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field
import yaml

from app.bridge.node_key import parse_node_key
from app.bridge.research_run_service import load_run_research_contract
from app.bridge.research_scope_recovery import restore_project_scope
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import canonical, digest
from app.harness.runtime.project_scope import ProjectScope, safe_scope_path
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.state_machine import NodeState
from app.harness.runtime.task_contract import Contract, TaskEnvelope
from app.harness.schema.validator import get_schema, validate_document
from app.settings import get_settings, repo_root
from app.storage.artifact_store import SCHEMA_TO_AGENT
from app.storage.run_state_store import RunStateSnapshot, RunStateStore
from app.storage.run_store import RunHandle

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
HostContext = Literal["frozen_contract", "project_rules", "project_knowledge"]


class StageBindingError(ValueError):
    """Missing or changed evidence cannot produce a fresh invocation identity."""


class ApprovedStageInput(Contract):
    """An approved run artifact used for a graph dependency, not producer proof."""

    predecessor_node: str = Field(min_length=1)
    schema_id: str = Field(min_length=1)
    stem: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    source_version: str = Field(pattern=r"^v[1-9][0-9]*$")
    approval_sequence: int = Field(ge=1)


class _Fingerprint(Contract):
    source: str
    sha256: Sha256


class _ApprovedEvidence(Contract):
    reference: ApprovedStageInput
    source_ref: str
    source_sha256: Sha256
    approval_ref: str
    approval_sha256: Sha256
    schema_sha256: Sha256


class _Approval(Contract):
    schema_id: Literal["artifact_approval.v1"] = Field(alias="schema")
    run_id: str
    agent_dir: str
    stem: str
    sequence: int = Field(ge=1)
    source_version: str = Field(pattern=r"^v[1-9][0-9]*$")
    source_sha256: Sha256


class _StageInputs(Contract):
    schema_id: Literal["research_stage_inputs.v1"] = "research_stage_inputs.v1"
    run_id: str
    project: str
    journal_id: str
    task_sha256: Sha256
    node_key: str
    stage: str
    attempt: int = Field(ge=1)
    predecessors: tuple[str, ...]
    exclusive_resources: tuple[str, ...]
    candidate_id: str
    scope_binding_sha256: Sha256
    goal: str
    output_schema: str
    output_schema_sha256: Sha256
    configuration: tuple[_Fingerprint, ...]
    # This is a drift fingerprint, not certification or a capability grant.
    configuration_scope_sha256: Sha256
    upstream: tuple[_ApprovedEvidence, ...]
    host_context: tuple[_Fingerprint, ...]


class _StageBinding(Contract):
    schema_id: Literal["research_stage_invocation.v1"] = "research_stage_invocation.v1"
    inputs: _StageInputs
    task: TaskEnvelope


def _bytes_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(run: RunHandle, relative: str) -> bytes:
    return safe_scope_path(run.root, relative, must_exist=True).read_bytes()


def _configuration(stage: str, output_schema: str) -> tuple[_Fingerprint, ...]:
    """Hash existing public configuration sources; never resolve provider secrets."""
    if SCHEMA_TO_AGENT.get(output_schema, (None, None))[0] != stage:
        raise StageBindingError("Output schema is not an ArtifactStore contract for this stage")
    root = repo_root().resolve(strict=True)
    agents_bytes = safe_scope_path(root, "configs/agents.yaml", must_exist=True).read_bytes()
    agents = yaml.safe_load(agents_bytes)
    definition = agents.get(stage) if isinstance(agents, dict) else None
    if (not isinstance(definition, dict) or definition.get("enabled", True) is not True
            or definition.get("output_schema") != output_schema):
        raise StageBindingError("Output schema differs from the configured stage contract")
    loop_policy = AgentLoopPolicy.from_mapping(definition.get("loop", {}))
    files = {"configs/agents.yaml", "configs/models.yaml", "configs/tools.yaml", "configs/gates.yaml",
             "configs/context.yaml", f"configs/agent_contexts/{stage}.yaml",
             f"backend/app/harness/schema/schemas/{output_schema}.json"}
    # agents.yaml contains the baseline loop policy. Idea has explicit runtime
    # profile overrides in these existing files, so they must be pinned too.
    if stage == "idea":
        files.update({"configs/idea_focused.yaml", "configs/idea_runtime_profiles.yaml"})
    result = [_Fingerprint(source=name, sha256=_bytes_sha(safe_scope_path(root, name, must_exist=True).read_bytes()))
              for name in sorted(files)]
    result.append(_Fingerprint(source="policy:AgentLoopPolicy", sha256=digest(loop_policy.fingerprint_data())))
    if stage == "idea":
        result.append(_Fingerprint(source="setting:mars_idea_runtime_profile",
                                   sha256=digest(get_settings().mars_idea_runtime_profile)))
    return tuple(result)


def _approved_input(run: RunHandle, reference: ApprovedStageInput,
                    snapshot: RunStateSnapshot) -> _ApprovedEvidence:
    source_node = snapshot.graph.nodes.get(reference.predecessor_node)
    mapping = SCHEMA_TO_AGENT.get(reference.schema_id)
    if (source_node is None or source_node.kind != "agent" or mapping is None or ".." in reference.stem
            or parse_node_key(source_node.key).stage != mapping[0]
            or source_node.metadata.get("stage") != mapping[0]
            or type(source_node.metadata.get("attempt")) is not int
            or source_node.metadata["attempt"] != parse_node_key(source_node.key).attempt):
        raise StageBindingError("Approved input does not match its graph dependency stage")
    stage = mapping[0]
    source_ref = f"{stage}/{reference.stem}.{reference.source_version}.md"
    approval_ref = f"{stage}/.approvals/{reference.stem}/{reference.approval_sequence:020d}.json"
    # Require the actual immutable approval history prefix, not an .approved.md
    # filename. Do not call ArtifactStore.latest/recover_approvals (both write).
    for sequence in range(1, reference.approval_sequence + 1):
        relative = f"{stage}/.approvals/{reference.stem}/{sequence:020d}.json"
        raw = _read(run, relative)
        receipt = _Approval.model_validate_json(raw)
        if ((receipt.run_id, receipt.agent_dir, receipt.stem, receipt.sequence)
                != (run.run_id, stage, reference.stem, sequence)):
            raise StageBindingError("Approved input receipt identity differs")
        approved_source = _read(run, f"{stage}/{reference.stem}.{receipt.source_version}.md")
        if _bytes_sha(approved_source) != receipt.source_sha256:
            raise StageBindingError("Approved input history no longer matches its immutable source")
    if receipt.source_version != reference.source_version:
        raise StageBindingError("Approved input points to a different source version")
    content = _read(run, source_ref)
    validation = validate_document(content.decode("utf-8"), expected_schema=reference.schema_id)
    if (not validation.valid or validation.metadata.get("project") != run.project
            or ("run_id" in get_schema(reference.schema_id).get("properties", {})
                or "run_id" in validation.metadata) and validation.metadata.get("run_id") != run.run_id):
        raise StageBindingError("Approved input schema/project/run identity differs")
    return _ApprovedEvidence(reference=reference, source_ref=source_ref, source_sha256=_bytes_sha(content),
        approval_ref=approval_ref, approval_sha256=_bytes_sha(raw), schema_sha256=digest(get_schema(reference.schema_id)))


def _host_context(run: RunHandle, scope: ProjectScope, kinds: tuple[HostContext, ...],
                  frozen: dict[str, Any]) -> tuple[_Fingerprint, ...]:
    if len(set(kinds)) != len(kinds) or any(kind not in {"frozen_contract", "project_rules", "project_knowledge"} for kind in kinds):
        raise StageBindingError("Host context must use unique frozen evidence references")
    items: list[_Fingerprint] = []
    for kind in sorted(kinds):
        if kind == "frozen_contract":
            fingerprint = digest(frozen)
        else:
            name = "AGENTS.md" if kind == "project_rules" else "knowledge.md"
            if name not in dict(scope.metadata_hashes):
                raise StageBindingError("Requested host context was not frozen in the project capability")
            fingerprint = _bytes_sha(_read(run, (scope.metadata_root / name).relative_to(run.root).as_posix()))
        items.append(_Fingerprint(source=kind, sha256=fingerprint))
    return tuple(items)


def _inputs(run: RunHandle, *, node_key: str, candidate_id: str, ledger: ResearchBudgetLedger,
            goal: str, output_schema: str, upstream: tuple[ApprovedStageInput, ...],
            host_context: tuple[HostContext, ...], connection: sqlite3.Connection) -> tuple[_StageInputs, RunStateSnapshot]:
    scope = restore_project_scope(run, candidate_id=candidate_id, ledger=ledger)
    ledger.in_transaction(connection).snapshot()
    # _snapshot only validates/decode the committed payload. Unlike load(), it
    # does not acquire a filesystem lock or recover approval projections.
    snapshot = RunStateStore(run)._snapshot(ledger.journal._read(connection))
    frozen = load_run_research_contract(run, snapshot.request.get("extra"))
    if frozen is None or frozen.task_sha256 != ledger.task_sha256 or goal != frozen.task.goal:
        raise StageBindingError("Stage goal must be the original frozen research goal")
    node = snapshot.graph.nodes.get(node_key)
    identity = parse_node_key(node_key)
    if (node is None or node.kind != "agent" or node.metadata.get("stage") != identity.stage
            or type(node.metadata.get("attempt")) is not int or node.metadata["attempt"] != identity.attempt):
        raise StageBindingError("Stage/attempt must be declared by the existing authoritative graph node")
    predecessors = tuple(sorted(snapshot.graph.predecessors(node_key)))
    resources = node.metadata.get("exclusive_resources", [])
    if not isinstance(resources, list) or any(not isinstance(item, str) or not item for item in resources):
        raise StageBindingError("Invalid graph resource declaration")
    if any(not isinstance(item, ApprovedStageInput) for item in upstream):
        raise StageBindingError("Upstream inputs must be approved artifact references, not text")
    if any(item.predecessor_node not in predecessors for item in upstream):
        raise StageBindingError("Approved input is not an immediate graph dependency")
    sources = tuple(sorted((_approved_input(run, item, snapshot) for item in upstream),
                           key=lambda item: (item.reference.predecessor_node, item.source_ref)))
    if len({(item.reference.predecessor_node, item.source_ref) for item in sources}) != len(sources):
        raise StageBindingError("Duplicate approved stage input")
    # RunGraph remains the lifecycle authority. Only actual approved inputs may
    # satisfy non-skipped dependencies; a declaration/host note cannot stand in.
    for predecessor in predecessors:
        predecessor_node = snapshot.graph.nodes[predecessor]
        parent_identity = parse_node_key(predecessor)
        if (predecessor_node.kind != "agent" or predecessor_node.metadata.get("stage") != parent_identity.stage
                or type(predecessor_node.metadata.get("attempt")) is not int
                or predecessor_node.metadata["attempt"] != parent_identity.attempt):
            raise StageBindingError("Graph dependency has an inconsistent stage/attempt identity")
        state = snapshot.graph.state(predecessor)
        if state != NodeState.SKIPPED and (state not in {NodeState.APPROVED, NodeState.DONE}
                or not any(item.reference.predecessor_node == predecessor for item in sources)):
            raise StageBindingError("Graph dependency has no approved artifact input")
    row = connection.execute("SELECT version,binding,binding_sha256 FROM research_project_scopes WHERE candidate_id=?", (candidate_id,)).fetchone()
    if row is None or row[0] != 1 or not isinstance(row[1], str) or _bytes_sha(row[1].encode()) != row[2]:
        raise StageBindingError("Stage requires the original sealed project capability")
    configuration = _configuration(identity.stage, output_schema)
    # Existing host profile receipts are evidence only; no profile resolver is
    # invoked and no snapshot is manufactured as a side effect of this service.
    profile_path = run.root / "input/idea_runtime_profile.v1.json"
    if identity.stage == "idea" and (profile_path.exists() or profile_path.is_symlink()):
        configuration += (_Fingerprint(source="input/idea_runtime_profile.v1.json",
            sha256=_bytes_sha(_read(run, "input/idea_runtime_profile.v1.json"))),)
    context = _host_context(run, scope, host_context, frozen.model_dump(mode="json"))
    return _StageInputs(run_id=run.run_id, project=run.project, journal_id=ledger.journal.journal_id,
        task_sha256=frozen.task_sha256, node_key=node_key, stage=identity.stage, attempt=identity.attempt,
        predecessors=predecessors, exclusive_resources=tuple(sorted(set(resources))), candidate_id=candidate_id,
        scope_binding_sha256=row[2], goal=goal, output_schema=output_schema,
        output_schema_sha256=digest(get_schema(output_schema)), configuration=configuration,
        configuration_scope_sha256=digest({"scope_binding_sha256": row[2], "stage": identity.stage,
            "configuration": [item.model_dump() for item in configuration]}), upstream=sources, host_context=context), snapshot


def _task(inputs: _StageInputs, invocation_id: str) -> TaskEnvelope:
    return TaskEnvelope(run_id=inputs.run_id, task_id=f"{inputs.run_id}:{inputs.node_key}",
        parent_task_id=inputs.run_id, node_id=inputs.node_key, invocation_id=invocation_id,
        predecessor_task_ids=[f"{inputs.run_id}:{key}" for key in inputs.predecessors], agent=inputs.stage,
        project=inputs.project, goal=inputs.goal, attempt=inputs.attempt, output_schema=inputs.output_schema,
        input_sha256=digest(inputs.model_dump(mode="json")),
        required_context_refs=sorted([item.source_ref for item in inputs.upstream]
                                     + ["host:" + item.source for item in inputs.host_context]))


def _decode(row: tuple[Any, ...] | None, node_key: str) -> _StageBinding:
    if (row is None or row[0] != 1 or not isinstance(row[1], str) or _bytes_sha(row[1].encode()) != row[2]):
        raise StageBindingError("Stage has no verified immutable SQL invocation")
    result = _StageBinding.model_validate_json(row[1])
    if (result.inputs.node_key != node_key or canonical(result.model_dump(mode="json")) != row[1]
            or result.task != _task(result.inputs, result.task.invocation_id)
            or row[3] != result.task.invocation_id):
        raise StageBindingError("Stage invocation differs from its immutable inputs")
    return result


def bind_research_stage(run: RunHandle, *, node_key: str, candidate_id: str, ledger: ResearchBudgetLedger,
                        goal: str, output_schema: str, upstream: tuple[ApprovedStageInput, ...] = (),
                        host_context: tuple[HostContext, ...] = ()) -> TaskEnvelope:
    """Explicit host-only write-once binding; a repeated node returns its old ID.

    No execution admission is implied. A new binding requires a pending node;
    the existing scheduler alone owns all subsequent node states.
    """
    # Validate the run/SQL paths before opening a writable transaction.
    restore_project_scope(run, candidate_id=candidate_id, ledger=ledger)
    try:
        with ledger.journal.transaction() as connection:
            inputs, snapshot = _inputs(run, node_key=node_key, candidate_id=candidate_id, ledger=ledger,
                goal=goal, output_schema=output_schema, upstream=upstream, host_context=host_context, connection=connection)
            connection.execute("""CREATE TABLE IF NOT EXISTS research_stage_invocations (
                node_key TEXT PRIMARY KEY, invocation_id TEXT NOT NULL UNIQUE,
                version INTEGER NOT NULL CHECK(version=1), binding TEXT NOT NULL, binding_sha256 TEXT NOT NULL)""")
            row = connection.execute("SELECT version,binding,binding_sha256,invocation_id FROM research_stage_invocations WHERE node_key=?", (node_key,)).fetchone()
            if row is not None:
                saved = _decode(row, node_key)
                if saved.inputs != inputs:
                    raise StageBindingError("Stage inputs or configuration changed; rebinding is forbidden")
                return saved.task
            if snapshot.graph.state(node_key) != NodeState.PENDING:
                raise StageBindingError("An unbound non-pending node requires explicit migration; no invocation may be invented")
            binding = _StageBinding(inputs=inputs, task=_task(inputs, uuid4().hex))
            encoded = canonical(binding.model_dump(mode="json"))
            connection.execute("INSERT INTO research_stage_invocations VALUES (?,?,1,?,?)",
                (node_key, binding.task.invocation_id, encoded, _bytes_sha(encoded.encode())))
            return binding.task
    except sqlite3.Error as exc:
        raise StageBindingError("Stage invocation authority is unavailable") from exc


def restore_research_stage(run: RunHandle, *, node_key: str, ledger: ResearchBudgetLedger) -> TaskEnvelope:
    """Read-only verification; missing bindings never create files/tables/IDs."""
    # The scope verifier performs path, authority and budget checks before any
    # SQL access. The candidate ID must itself come from the saved binding.
    from app.bridge.research_scope_recovery import _identity
    _identity(run, ledger)
    try:
        with ledger.journal.connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            ledger.in_transaction(connection).snapshot()
            row = connection.execute("SELECT version,binding,binding_sha256,invocation_id FROM research_stage_invocations WHERE node_key=?", (node_key,)).fetchone()
            saved = _decode(row, node_key)
            kinds: tuple[HostContext, ...] = tuple(_host_kind(item.source) for item in saved.inputs.host_context)
            current, _snapshot = _inputs(run, node_key=node_key, candidate_id=saved.inputs.candidate_id, ledger=ledger,
                goal=saved.inputs.goal, output_schema=saved.inputs.output_schema,
                upstream=tuple(item.reference for item in saved.inputs.upstream), host_context=kinds, connection=connection)
            if saved.inputs != current:
                raise StageBindingError("Saved stage inputs or configuration changed; recovery is refused")
            return saved.task
    except sqlite3.Error as exc:
        raise StageBindingError("Stage invocation authority is unavailable") from exc


def _host_kind(value: str) -> HostContext:
    if value == "frozen_contract":
        return "frozen_contract"
    if value == "project_rules":
        return "project_rules"
    if value == "project_knowledge":
        return "project_knowledge"
    raise StageBindingError("Unknown saved host context")
