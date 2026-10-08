"""Carry verified same-task reading evidence into an explicitly requested revision.

This is source reuse, never tool replay or acceptance of a previous conclusion.
Mutable repository reads are deliberately excluded and must be read again.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import audit_trace, digest
from app.harness.schema.validator import validate_document


@dataclass(frozen=True)
class RevisionSeed:
    candidate: str
    observations: tuple[dict[str, Any], ...]
    receipt: dict[str, Any]


def _read(path: Path, root: Path) -> Any:
    if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 32_000_000:
        raise ValueError("revision evidence is outside the task or exceeds its size limit")
    return json.loads(path.read_text())


def load_revision_seed(root: Path, *, project: str, agent: str, candidate_path: Path,
                       schema: str) -> RevisionSeed:
    if not candidate_path.resolve().is_relative_to((root / agent).resolve()):
        raise ValueError("revision candidate must belong to this task and agent")
    candidate = candidate_path.read_text()
    parsed = validate_document(candidate, expected_schema=schema)
    if not parsed.valid or parsed.metadata.get("project") != project:
        raise ValueError("revision candidate has an invalid schema or project binding")
    traces = root / "agent_traces" / agent
    source = None
    state = None
    for path in sorted(traces.glob("*/checkpoint.json")):
        previous = _read(path, root)
        if previous.get("candidate") == candidate and previous.get("status") == "passed":
            source, state = path.parent, previous
            break
    if source is None or state is None:
        raise ValueError("revision candidate has no completed, matching model trace")
    if (state.get("pending") or state.get("pending_batch") or not state.get("reflection_accepted")
            or state.get("correlation", {}).get("trace_id") != root.name
            or not audit_trace(source).get("consistent")):
        raise ValueError("revision candidate trace is unresolved or inconsistent")
    return _seed_from_state(root, agent=agent, source=source, state=state, candidate=candidate,
                            candidate_ref=candidate_path.relative_to(root).as_posix())


def load_failed_revision_seed(root: Path, *, project: str, agent: str,
                              schema: str) -> RevisionSeed | None:
    """Reuse a settled rejected draft and verified readings, never its acceptance."""
    traces = root / "agent_traces" / agent
    paths = sorted(traces.glob("*/checkpoint.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            state = _read(path, root)
            if not isinstance(state, dict):
                continue
            rejected_quota = None
            if state.get("status") == "model_error":
                from app.harness.agent_loop.provider_rejection_resume import checkpoint_quota_rejection_receipt
                rejected_quota = checkpoint_quota_rejection_receipt(path.parent, state, run_root=root)
            if ((state.get("status") not in {"protocol_exhausted", "validation_exhausted",
                    "reflection_rejected", "budget_exhausted", "evidence_unavailable"}
                    and rejected_quota is None)
                    or state.get("pending") and rejected_quota is None or state.get("pending_batch")):
                continue
            if (state.get("correlation", {}).get("trace_id") != root.name
                    or not state.get("counts", {}).get("model_responses")
                    or not audit_trace(path.parent).get("consistent")):
                continue
            candidate = state.get("candidate", "")
            if not isinstance(candidate, str):
                continue
            parsed = validate_document(candidate, expected_schema=schema)
            if not parsed.valid or parsed.metadata.get("project") != project:
                continue
            return _seed_from_state(root, agent=agent, source=path.parent, state=state,
                candidate=candidate, candidate_ref=path.relative_to(root).as_posix() + "#candidate")
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return None


def _seed_from_state(root: Path, *, agent: str, source: Path, state: dict[str, Any],
                     candidate: str, candidate_ref: str) -> RevisionSeed:
    traces = root / "agent_traces" / agent
    evidence = []
    seen: set[str] = set()
    audited: dict[Path, list[dict[str, Any]]] = {}
    for observation in state.get("history", []):
        # Literature is an immutable archived source. Files in a code repository
        # may change between reviews, so never restore their old observations.
        if not observation.get("ok") or not str(observation.get("tool", "")).startswith("search."):
            continue
        raw = Path(str(observation.get("raw_ref", "")))
        if (not raw.resolve().is_relative_to(traces.resolve()) or raw.parent.name != "tools"):
            raise ValueError("revision reading has no task-owned source receipt")
        if str(raw) in seen:
            continue
        original = _read(raw, root)
        origin = raw.parent.parent
        if origin not in audited:
            if not audit_trace(origin).get("consistent"):
                raise ValueError("revision reading trace is inconsistent")
            audited[origin] = [json.loads(line) for line in (origin / "events.jsonl").read_text().splitlines()]
        actual = {**original, "raw_ref": str(raw)}
        event = next((row for row in audited[origin] if row.get("kind") == "observation"
                      and row.get("visible") == actual and row.get("visible_sha256") == digest(actual)), None)
        if event is None or event.get("correlation", {}).get("trace_id") != root.name:
            raise ValueError("revision reading receipt no longer matches its actual observation")
        for key in ("tool", "args", "ok", "output", "error", "status", "blocked_by_gate"):
            if observation.get(key) != original.get(key):
                raise ValueError("revision reading was changed after its original tool result")
        # Do not manufacture a native assistant/tool exchange in the new model
        # session. The packer presents this as a clearly sourced prior observation.
        inherited = {key: value for key, value in actual.items() if not key.startswith("native_")}
        inherited["evidence_origin"] = {"invocation_id": origin.name, "event_seq": event["event_seq"],
                                        "visible_sha256": event["visible_sha256"],
                                        "source_ref": raw.relative_to(root).as_posix()}
        evidence.append(inherited)
        seen.add(str(raw))
    return RevisionSeed(candidate, tuple(evidence), {
        "schema": "agent.revision_seed.v1", "candidate_sha256": digest(candidate),
        "candidate_ref": candidate_ref,
        "invocation_id": source.name, "checkpoint_sha256": digest(state),
        "source_status": state["status"],
        "observations": len(evidence), "evidence_sha256": digest(evidence),
        "acceptance_inherited": False, "tool_calls_replayed": False,
    })
