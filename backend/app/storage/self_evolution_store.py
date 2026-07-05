"""Run-local self-evolution memory for Commander feedback loops."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.kb.stores import KBStores
from app.harness.memory.episode import index_episode_event
from app.storage.agent_context_store import (
    SUPPORTED_AGENTS,
    append_approved_agent_memory,
    list_agent_context_files,
    sync_agent_context_file_to_memory,
    update_agent_context_file,
)
from app.storage.run_store import RunHandle

CANDIDATE_LIFECYCLE_STATUSES: frozenset[str] = frozenset(
    {"pending_review", "approved", "active", "rejected", "stale", "superseded"}
)
REVIEWABLE_CANDIDATE_STATUSES: frozenset[str] = frozenset(
    {"pending_review", "approved"}
)
MUTATION_LIFECYCLE_STATUSES: frozenset[str] = frozenset(
    {"pending_review", "applied", "rejected", "failed"}
)
MUTATION_CATEGORIES: frozenset[str] = frozenset({"prompt", "few_shot", "eval"})


@dataclass(frozen=True)
class MemoryWriteResult:
    episode_path: str
    candidates_path: str
    candidate_count: int


AGENT_ORDER: tuple[str, ...] = (
    "commander",
    "idea",
    "experiment",
    "coding",
    "execution",
    "writing",
)


def _memory_dir(run: RunHandle) -> Path:
    d = run.subdir("memory")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(
        json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows
    )
    path.write_text(text, encoding="utf-8")


def append_learning_event(
    *,
    run: RunHandle,
    event: dict[str, Any],
    memory_candidates: list[dict[str, Any]],
) -> MemoryWriteResult:
    """Persist run-local learning plus pending long-term memory candidates.

    Episode memory is always run-local. Candidates are explicitly marked
    ``pending_review`` so they cannot pollute future Agent context until a
    separate approval path promotes them.
    """
    created = datetime.now(tz=timezone.utc).isoformat()
    mdir = _memory_dir(run)
    episode_path = mdir / "episode_memory.jsonl"
    candidates_path = mdir / "memory_candidates.jsonl"

    episode_payload = {
        "schema": "agent_learning_event.v1",
        "run_id": run.run_id,
        "project": run.project,
        "created": created,
        **event,
    }
    _append_jsonl(episode_path, episode_payload)
    index_episode_event(run_root=run.root, event=episode_payload)

    for index, candidate in enumerate(memory_candidates):
        candidate_payload = {
            "schema": "agent_memory_candidate.v1",
            "id": candidate.get("id") or f"{run.run_id}:candidate:{index + 1}",
            "run_id": run.run_id,
            "project": run.project,
            "created": created,
            "status": "pending_review",
            **candidate,
        }
        candidate_payload["status"] = "pending_review"
        _append_jsonl(candidates_path, candidate_payload)

    return MemoryWriteResult(
        episode_path=str(episode_path),
        candidates_path=str(candidates_path),
        candidate_count=len(memory_candidates),
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            out.append(raw)
    return out


def build_self_evolution_levers(
    *,
    run: RunHandle,
    max_items_per_type: int = 8,
) -> dict[str, Any]:
    """Expose prompt/few-shot/finding levers without mutating source files."""
    levers: dict[str, list[dict[str, Any]]] = {
        "prompt": [],
        "few_shot": [],
        "eval": [],
        "kb_finding": [],
    }
    for item in _agent_context_levers(max_items=max_items_per_type):
        lever_type = str(item.get("lever_type", ""))
        if lever_type in levers and len(levers[lever_type]) < max_items_per_type:
            levers[lever_type].append(item)
    for item in _candidate_levers(run):
        if len(levers["kb_finding"]) < max_items_per_type:
            levers["kb_finding"].append(item)
    for item in _scorecard_levers(run):
        if len(levers["kb_finding"]) < max_items_per_type:
            levers["kb_finding"].append(item)

    return {
        "schema": "self_evolution_levers.v1",
        "run_id": run.run_id,
        "project": run.project,
        "mutation_mode": "manual_review_only",
        "allowed_actions": [
            "review_lever",
            "create_mutation_proposal",
            "approve_mutation_proposal",
            "reject_mutation_proposal",
            "approve_memory_candidate",
            "reject_memory_candidate",
            "mark_memory_candidate_stale",
            "supersede_memory_candidate",
            "edit_agent_context_file",
        ],
        "levers": levers,
        "counts": {key: len(value) for key, value in levers.items()},
    }


def list_self_evolution_mutations(*, run: RunHandle) -> list[dict[str, Any]]:
    return read_jsonl(_memory_dir(run) / "self_evolution_mutations.jsonl")


def create_self_evolution_mutation(
    *,
    run: RunHandle,
    lever_id: str,
    agent: str,
    path: str,
    proposed_content: str,
    rationale: str = "",
) -> dict[str, Any]:
    """Create a gated mutation proposal without applying it."""
    normalized_agent = agent.strip()
    normalized_path = path.strip().strip("/")
    if normalized_agent not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported agent context '{agent}'")
    lever_type = _lever_type_for_context_path(normalized_path)
    if lever_type not in MUTATION_CATEGORIES:
        raise ValueError("self-evolution mutation target must be prompts/, examples/, or evals/")
    current_content = _read_context_content(normalized_agent, normalized_path)
    if current_content is None:
        raise ValueError(f"agent context file '{normalized_path}' not found")
    gate = _evaluate_mutation_proposal(
        lever_id=lever_id,
        agent=normalized_agent,
        path=normalized_path,
        current_content=current_content,
        proposed_content=proposed_content,
        rationale=rationale,
    )
    created = datetime.now(tz=timezone.utc).isoformat()
    mutation_id = _mutation_id(
        run_id=run.run_id,
        lever_id=lever_id,
        agent=normalized_agent,
        path=normalized_path,
        proposed_content=proposed_content,
    )
    mutation = {
        "schema": "self_evolution_mutation.v1",
        "id": mutation_id,
        "run_id": run.run_id,
        "project": run.project,
        "lever_id": lever_id,
        "lever_type": lever_type,
        "agent": normalized_agent,
        "path": normalized_path,
        "status": "pending_review",
        "rationale": rationale,
        "current_hash": _content_hash(current_content),
        "proposed_hash": _content_hash(proposed_content),
        "proposed_content": proposed_content,
        "text_preview": _preview(proposed_content),
        "eval_gate": gate,
        "created": created,
    }
    _append_jsonl(_memory_dir(run) / "self_evolution_mutations.jsonl", mutation)
    return mutation


def approve_self_evolution_mutation(
    *,
    run: RunHandle,
    mutation_id: str,
    reviewer_note: str = "",
    stores: KBStores | None = None,
) -> dict[str, Any]:
    """Apply an approved mutation proposal to Agent context after gate pass."""
    mutation = _get_mutation(run=run, mutation_id=mutation_id)
    if str(mutation.get("status", "")) != "pending_review":
        raise ValueError(f"self-evolution mutation '{mutation_id}' is not pending review")
    gate = mutation.get("eval_gate")
    if not isinstance(gate, dict) or gate.get("passed") is not True:
        raise ValueError(f"self-evolution mutation '{mutation_id}' did not pass eval gate")
    agent = str(mutation.get("agent", ""))
    path = str(mutation.get("path", ""))
    proposed_content = str(mutation.get("proposed_content", ""))
    updated = update_agent_context_file(
        agent,
        path=path,
        content=proposed_content,
    )
    sync_agent_context_file_to_memory(
        agent,
        updated,
        project=run.project,
        stores=stores,
    )
    return _set_mutation_status(
        run=run,
        mutation_id=mutation_id,
        status="applied",
        reviewer_note=reviewer_note,
        applied_path=f"agents/{agent}/{path}",
    )


def reject_self_evolution_mutation(
    *,
    run: RunHandle,
    mutation_id: str,
    reviewer_note: str = "",
) -> dict[str, Any]:
    return _set_mutation_status(
        run=run,
        mutation_id=mutation_id,
        status="rejected",
        reviewer_note=reviewer_note,
    )


def approve_memory_candidate(
    *,
    run: RunHandle,
    candidate_id: str,
    stores: KBStores | None = None,
) -> dict[str, Any]:
    """Promote a pending run-local candidate into approved Agent memory."""
    candidates_path = _memory_dir(run) / "memory_candidates.jsonl"
    candidates = read_jsonl(candidates_path)
    candidate = next((item for item in candidates if item.get("id") == candidate_id), None)
    if candidate is None:
        raise ValueError(f"memory candidate '{candidate_id}' not found")
    if str(candidate.get("status", "")) not in REVIEWABLE_CANDIDATE_STATUSES:
        raise ValueError(f"memory candidate '{candidate_id}' is not reviewable")
    agent = str(candidate.get("agent", "")).strip()
    if not agent:
        raise ValueError("memory candidate is missing agent")
    approved = append_approved_agent_memory(
        agent,
        item=candidate,
        project=run.project,
        stores=stores,
    )
    _set_memory_candidate_status(
        run=run,
        candidate_id=candidate_id,
        status="approved",
        reviewer_note="approved for long-term agent memory",
        approved_memory_id=approved.id,
    )
    audit = {
        "schema": "agent_memory_promotion.v1",
        "run_id": run.run_id,
        "project": run.project,
        "candidate_id": candidate_id,
        "agent": agent,
        "status": "approved",
        "created": datetime.now(tz=timezone.utc).isoformat(),
    }
    _append_jsonl(_memory_dir(run) / "memory_promotions.jsonl", audit)
    return {
        "candidate_id": candidate_id,
        "agent": agent,
        "memory_id": approved.id,
        "status": "approved",
    }


def _agent_context_levers(*, max_items: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for agent in AGENT_ORDER:
        if agent not in SUPPORTED_AGENTS:
            continue
        try:
            files = list_agent_context_files(
                agent,
                include_runtime_code=False,
                max_chars_per_file=1200,
            )
        except ValueError:
            continue
        for file in files:
            lever_type = _lever_type_for_context_path(file.path)
            if lever_type is None:
                continue
            out.append(
                {
                    "id": f"agent_context:{agent}:{file.path}",
                    "lever_type": lever_type,
                    "agent": agent,
                    "title": file.path,
                    "source": "agent_context",
                    "source_path": f"agents/{agent}/{file.path}",
                    "status": "active",
                    "text_preview": _preview(file.content),
                    "evidence_refs": [f"agents/{agent}/{file.path}"],
                    "suggested_action": _suggested_action(lever_type),
                }
            )
            if len(out) >= max_items * 3:
                return out
    return out


def _read_context_content(agent: str, path: str) -> str | None:
    try:
        files = list_agent_context_files(
            agent,
            include_runtime_code=False,
            max_chars_per_file=1_000_000,
        )
    except ValueError:
        return None
    for file in files:
        if file.path == path and file.editable:
            return file.content
    return None


def _evaluate_mutation_proposal(
    *,
    lever_id: str,
    agent: str,
    path: str,
    current_content: str,
    proposed_content: str,
    rationale: str,
) -> dict[str, Any]:
    checks = {
        "lever_present": bool(lever_id.strip()),
        "agent_supported": agent in SUPPORTED_AGENTS,
        "target_allowed": _lever_type_for_context_path(path) in MUTATION_CATEGORIES,
        "non_empty": bool(proposed_content.strip()),
        "changes_content": proposed_content != current_content,
        "rationale_present": bool(rationale.strip()),
    }
    passed = all(checks.values())
    return {
        "schema": "self_evolution_mutation_gate.v1",
        "passed": passed,
        "decision": "pass" if passed else "block",
        "blocking": not passed,
        "checks": checks,
        "reason": "" if passed else "mutation proposal failed deterministic safety checks",
    }


def _get_mutation(*, run: RunHandle, mutation_id: str) -> dict[str, Any]:
    mutation = next(
        (
            item
            for item in list_self_evolution_mutations(run=run)
            if item.get("id") == mutation_id
        ),
        None,
    )
    if mutation is None:
        raise ValueError(f"self-evolution mutation '{mutation_id}' not found")
    return mutation


def _set_mutation_status(
    *,
    run: RunHandle,
    mutation_id: str,
    status: str,
    reviewer_note: str = "",
    applied_path: str = "",
) -> dict[str, Any]:
    if status not in MUTATION_LIFECYCLE_STATUSES:
        raise ValueError(f"unsupported self-evolution mutation status '{status}'")
    mutations_path = _memory_dir(run) / "self_evolution_mutations.jsonl"
    mutations = read_jsonl(mutations_path)
    reviewed_at = datetime.now(tz=timezone.utc).isoformat()
    found: dict[str, Any] | None = None
    for item in mutations:
        if item.get("id") != mutation_id:
            continue
        item["status"] = status
        item["reviewed_at"] = reviewed_at
        if reviewer_note:
            item["reviewer_note"] = reviewer_note
        if applied_path:
            item["applied_path"] = applied_path
        found = item
        break
    if found is None:
        raise ValueError(f"self-evolution mutation '{mutation_id}' not found")
    _write_jsonl(mutations_path, mutations)
    audit = {
        "schema": "self_evolution_mutation_review.v1",
        "run_id": run.run_id,
        "project": run.project,
        "mutation_id": mutation_id,
        "agent": str(found.get("agent", "")),
        "path": str(found.get("path", "")),
        "status": status,
        "reviewer_note": reviewer_note,
        "applied_path": applied_path,
        "created": reviewed_at,
    }
    _append_jsonl(_memory_dir(run) / "self_evolution_mutation_reviews.jsonl", audit)
    return {
        "mutation_id": mutation_id,
        "agent": str(found.get("agent", "")),
        "path": str(found.get("path", "")),
        "status": status,
        "applied_path": applied_path,
    }


def _candidate_levers(run: RunHandle) -> list[dict[str, Any]]:
    items = read_jsonl(_memory_dir(run) / "memory_candidates.jsonl")
    out: list[dict[str, Any]] = []
    for item in items:
        candidate_id = str(item.get("id", "") or "")
        out.append(
            {
                "id": f"memory_candidate:{candidate_id}",
                "lever_type": "kb_finding",
                "agent": str(item.get("agent", "") or ""),
                "title": candidate_id or "memory candidate",
                "source": "memory_candidate",
                "source_path": f"runs/{run.run_id}/memory/memory_candidates.jsonl",
                "status": str(item.get("status", "pending_review") or "pending_review"),
                "text_preview": _preview(str(item.get("text", "") or "")),
                "evidence_refs": _string_list(item.get("evidence_refs")),
                "suggested_action": "review and approve only if reusable across runs",
            }
        )
    return out


def _scorecard_levers(run: RunHandle) -> list[dict[str, Any]]:
    path = run.subdir("events") / "evaluation_scorecard.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, dict):
        return []
    findings = raw.get("top_findings")
    if not isinstance(findings, list):
        findings = []
    out: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("id", "") or f"finding_{index + 1}")
        out.append(
            {
                "id": f"scorecard:{finding_id}",
                "lever_type": "kb_finding",
                "agent": str(finding.get("agent", "") or finding.get("target_agent", "")),
                "title": finding_id,
                "source": "evaluation_scorecard",
                "source_path": f"runs/{run.run_id}/events/evaluation_scorecard.json",
                "status": "observed",
                "text_preview": _preview(str(finding.get("message", "") or "")),
                "evidence_refs": _string_list(finding.get("evidence_refs")),
                "suggested_action": "turn into a memory candidate only after eval-gated review",
            }
        )
    return out


def _lever_type_for_context_path(path: str) -> str | None:
    if path.startswith("prompts/"):
        return "prompt"
    if path.startswith("examples/"):
        return "few_shot"
    if path.startswith("evals/"):
        return "eval"
    return None


def _suggested_action(lever_type: str) -> str:
    if lever_type == "prompt":
        return "review prompt update; do not auto-mutate"
    if lever_type == "few_shot":
        return "review as few-shot candidate"
    if lever_type == "eval":
        return "review eval rubric or scorecard policy"
    return "review lever"


def _preview(text: str, *, limit: int = 360) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mutation_id(
    *,
    run_id: str,
    lever_id: str,
    agent: str,
    path: str,
    proposed_content: str,
) -> str:
    raw = "|".join([run_id, lever_id, agent, path, proposed_content])
    return "mut_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def reject_memory_candidate(
    *,
    run: RunHandle,
    candidate_id: str,
    reviewer_note: str = "",
) -> dict[str, Any]:
    """Reject a candidate so it never enters cross-run Agent context."""
    return _set_memory_candidate_status(
        run=run,
        candidate_id=candidate_id,
        status="rejected",
        reviewer_note=reviewer_note,
    )


def mark_memory_candidate_stale(
    *,
    run: RunHandle,
    candidate_id: str,
    reviewer_note: str = "",
) -> dict[str, Any]:
    """Mark a candidate stale when later evidence makes it unsafe to reuse."""
    return _set_memory_candidate_status(
        run=run,
        candidate_id=candidate_id,
        status="stale",
        reviewer_note=reviewer_note,
    )


def supersede_memory_candidate(
    *,
    run: RunHandle,
    candidate_id: str,
    superseded_by: str = "",
    reviewer_note: str = "",
) -> dict[str, Any]:
    """Mark a candidate superseded by a newer or more specific memory."""
    return _set_memory_candidate_status(
        run=run,
        candidate_id=candidate_id,
        status="superseded",
        reviewer_note=reviewer_note,
        superseded_by=superseded_by,
    )


def _set_memory_candidate_status(
    *,
    run: RunHandle,
    candidate_id: str,
    status: str,
    reviewer_note: str = "",
    approved_memory_id: str = "",
    superseded_by: str = "",
) -> dict[str, Any]:
    if status not in CANDIDATE_LIFECYCLE_STATUSES:
        raise ValueError(f"unsupported memory candidate status '{status}'")
    candidates_path = _memory_dir(run) / "memory_candidates.jsonl"
    candidates = read_jsonl(candidates_path)
    created = datetime.now(tz=timezone.utc).isoformat()
    found: dict[str, Any] | None = None
    for item in candidates:
        if item.get("id") != candidate_id:
            continue
        item["status"] = status
        item["reviewed_at"] = created
        if reviewer_note:
            item["reviewer_note"] = reviewer_note
        if approved_memory_id:
            item["approved_memory_id"] = approved_memory_id
        if superseded_by:
            item["superseded_by"] = superseded_by
        found = item
        break
    if found is None:
        raise ValueError(f"memory candidate '{candidate_id}' not found")

    _write_jsonl(candidates_path, candidates)
    audit = {
        "schema": "agent_memory_candidate_review.v1",
        "run_id": run.run_id,
        "project": run.project,
        "candidate_id": candidate_id,
        "agent": str(found.get("agent", "")),
        "status": status,
        "reviewer_note": reviewer_note,
        "approved_memory_id": approved_memory_id,
        "superseded_by": superseded_by,
        "created": created,
    }
    _append_jsonl(_memory_dir(run) / "memory_candidate_reviews.jsonl", audit)
    return {
        "candidate_id": candidate_id,
        "agent": str(found.get("agent", "")),
        "memory_id": approved_memory_id,
        "status": status,
    }
