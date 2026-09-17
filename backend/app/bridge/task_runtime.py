"""Durable task binding, handoff admission and explicit loop recovery checks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import uuid

from app.bridge.node_key import parse_node_key
from app.harness.agent_loop.trace import atomic_json, audit_trace, digest
from app.harness.runtime.task_contract import (
    HandoffBlockedError, HandoffEnvelope, HandoffPrerequisite, TaskEnvelope, missing_prerequisites,
)
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunHandle


def task_contract_path(run: RunHandle, node_key: str) -> Path:
    parse_node_key(node_key)
    if Path(node_key).name != node_key or node_key in {".", ".."}:
        raise ValueError("invalid node key")
    return run.root / "input" / "task_contracts" / (node_key + ".json")


def bind_task(run: RunHandle, node_key: str, *, goal: str, upstream: dict[str, str],
              output_schema: str, resume_invocation: str | None = None,
              predecessor_task_ids: list[str] | None = None) -> TaskEnvelope:
    path = task_contract_path(run, node_key)
    inputs = digest({"goal": goal, "upstream": upstream, "project": run.project})
    if resume_invocation is not None:
        task = TaskEnvelope.model_validate_json(path.read_text())
        if (task.invocation_id != resume_invocation or task.run_id != run.run_id
                or task.node_id != node_key or task.input_sha256 != inputs
                or task.output_schema != output_schema):
            raise ValueError("resume task or inputs changed; original invocation cannot be replayed")
        return task
    identity = parse_node_key(node_key)
    task = TaskEnvelope(run_id=run.run_id, task_id=f"{run.run_id}:{node_key}", parent_task_id=run.run_id,
        node_id=node_key, invocation_id=uuid.uuid4().hex, agent=identity.stage, project=run.project,
        goal=goal, attempt=identity.attempt, output_schema=output_schema, input_sha256=inputs,
        predecessor_task_ids=predecessor_task_ids or [], required_context_refs=sorted(upstream))
    atomic_json(path, task.model_dump())
    atomic_json(path.parent / "invocations" / (task.invocation_id + ".json"), task.model_dump())
    return task


def admit_handoffs(run: RunHandle, node_key: str, *, supplied_context: dict[str, str]) -> list[HandoffEnvelope]:
    task_contract_path(run, node_key)
    stage = parse_node_key(node_key).stage
    if stage not in {"experiment", "coding", "execution", "writing"}:
        return []
    handoffs: list[HandoffEnvelope] = []
    for source in sorted(run.subdir("idea").glob("*.approved.md")):
        if not source.resolve().is_relative_to(run.root.resolve()):
            raise ValueError("handoff artifact escapes its run")
        raw = source.read_bytes()
        validation = validate_document(raw.decode("utf-8"), expected_schema="proposal.v1")
        if not validation.valid or validation.metadata.get("project") != run.project:
            raise ValueError("approved handoff is invalid or belongs to another project")
        metadata = validation.metadata
        declared = metadata.get("handoff", {})
        if not isinstance(declared, dict):
            raise ValueError("handoff must be an object")
        prerequisites = [HandoffPrerequisite.model_validate(item) for item in declared.get("required_context", [])]
        handoffs.append(HandoffEnvelope(source_ref=source.relative_to(run.root).as_posix(),
            source_sha256=hashlib.sha256(raw).hexdigest(), destination_task_id=f"{run.run_id}:{node_key}",
            prerequisites=prerequisites, supplied_context_refs=sorted(supplied_context),
            missing_context=missing_prerequisites(prerequisites, stage=stage, supplied_context=supplied_context)))
    path = run.root / "input" / "handoffs" / (node_key + ".json")
    atomic_json(path, {"schema_id": "task.handoff_set.v1", "handoffs": [item.model_dump() for item in handoffs]})
    if any(item.missing_context for item in handoffs):
        raise HandoffBlockedError(handoffs)
    return handoffs


def resumable_task(run: RunHandle, node_key: str) -> TaskEnvelope:
    """Read-only preflight. The loop repeats fingerprint and receipt checks on entry."""
    task = TaskEnvelope.model_validate_json(task_contract_path(run, node_key).read_text())
    if task.run_id != run.run_id or task.node_id != node_key:
        raise ValueError("task contract belongs to another run or node")
    root = run.root / "agent_traces" / task.agent / task.invocation_id
    if not root.resolve().is_relative_to(run.root.resolve()):
        raise ValueError("invocation escapes its run")
    state: dict[str, Any] = json.loads((root / "checkpoint.json").read_text())
    if state.get("pending") == "tool" or state.get("pending_batch"):
        raise ValueError("tool_outcome_unknown: reconcile original tool receipts; automatic replay forbidden")
    if state.get("status") not in {"running", "interrupted", "model_error"}:
        raise ValueError("checkpoint is not an interrupted loop; do not rerun a completed or rejected invocation")
    audit = audit_trace(root)
    if not audit.get("consistent") or not audit.get("facts", {}).get("resume_available"):
        raise ValueError("checkpoint/journal inconsistent or incomplete; reconcile before resume")
    if audit["facts"].get("fingerprint") != state.get("fingerprint"):
        raise ValueError("checkpoint/facts fingerprint mismatch")
    return task
