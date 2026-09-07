"""File-level resume guards and immutable source journals for real evaluations."""
from __future__ import annotations

import fcntl
import copy
import hashlib
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.harness.agent_loop.trace import atomic_json, audit_trace, canonical
from app.harness.llm.provider_base import Message
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.review import ExternalReview, review_revision
from dataclasses import asdict


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def messages_match_snapshot(messages: list[Message], saved: object) -> bool:
    """Compare serialized values, including tuple-to-JSON-array round trips."""
    return canonical([asdict(message) for message in messages]) == canonical(saved)


def resume_scenario(initial: dict[str, Any]) -> dict[str, Any]:
    """Restore formatting order from the original prompt, without changing values.

    Canonical checkpoint JSON sorts mapping keys, but the original prompt contains
    a JSON string whose key order is part of its fingerprint. Both the reconstructed
    messages and the native fingerprint still have to match exactly before resume.
    """
    scenario: dict[str, Any] = copy.deepcopy(initial["scenario"])
    prefix = str(scenario["question"]) + "\n\nHost evaluation requirements (not experimental facts):\n"
    matches = [m["content"][len(prefix):] for m in initial["messages"]
               if m["role"] == "user" and m["content"].startswith(prefix)]
    if len(matches) != 1:
        raise ValueError("original task requirements are not unambiguous")
    ordered, _ = json.JSONDecoder().raw_decode(matches[0])
    if ordered != scenario["requirements"]:
        raise ValueError("original prompt requirements differ from the saved scenario")
    scenario["requirements"] = ordered
    return scenario


@contextmanager
def exclusive_run(root: Path) -> Iterator[None]:
    if not root.is_dir():
        raise ValueError("resume run directory does not exist")
    with (root / "evaluation.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another evaluation process holds this run") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def check_resume_state(state: dict[str, Any], *, has_review: bool, recover_abandoned: bool) -> None:
    """Inspect saved state without relabeling it or clearing any budget counter."""
    allowed = {"model_error", "interrupted"} | ({"passed"} if has_review else set())
    if recover_abandoned and state["status"] == "running" and state["pending"] == "model":
        allowed.add("running")
    if state["status"] not in allowed:
        raise ValueError("only interrupted or model-error runs can resume; an abandoned running model request requires explicit recovery; budgets are never reset")
    if state["pending"] == "tool" or state.get("pending_batch"):
        raise ValueError("unknown tool or batch outcome: reconcile the existing receipts before resuming")


def load_resume(root: Path, *, review: ExternalReview | None = None,
                recover_abandoned: bool = False) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    """Read resumable state; the executing CLI must hold exclusive_run throughout."""
    initial = json.loads((root / "input/request.json").read_text())
    previous = json.loads((root / "summary.json").read_text())
    checkpoints = list((root / "agent_traces/idea").glob("*/checkpoint.json"))
    if len(checkpoints) != 1:
        raise ValueError("resume requires exactly one Idea invocation checkpoint")
    checkpoint = checkpoints[0]
    state = json.loads(checkpoint.read_text())
    check_resume_state(state, has_review=review is not None, recover_abandoned=recover_abandoned)
    if initial["loop_policy"]["trace"] != "full" or not audit_trace(checkpoint.parent)["consistent"]:
        raise ValueError("resume requires a complete, consistent full trace")
    if initial["run_id"] != root.name or previous["run_id"] != root.name:
        raise ValueError("run identity does not match its directory")
    if state["counts"]["model_requests"] >= initial["loop_policy"]["max_model_calls"]:
        raise ValueError("model request budget already exhausted")
    if review:
        review_revision(state, review, AgentLoopPolicy.from_mapping(initial["loop_policy"]))
    return initial, previous, checkpoint, state


def record_resumption(root: Path, checkpoint: Path, source: dict[str, Any], *, review: ExternalReview | None = None) -> Path:
    """Freeze the actual pre-resume files before any checkpoint/summary update."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:6]
    journal = root / "input/resumptions" / stamp
    journal.mkdir(parents=True, exist_ok=False)
    files = {"checkpoint.json": checkpoint, "facts.json": checkpoint.parent / "facts.json",
             "summary.json": root / "summary.json"}
    prior = json.loads((root / "summary.json").read_text())
    if prior.get("proposal_path"):
        proposal = Path(prior["proposal_path"]).resolve()
        if not proposal.is_relative_to(root.resolve()):
            raise ValueError("prior proposal path escapes the run")
        if proposal.is_file():
            files["proposal.md"] = proposal
    hashes = {}
    for name, path in files.items():
        shutil.copyfile(path, journal / name)
        hashes[name] = sha256_file(journal / name)
    if review:
        atomic_json(journal / "external_review.json", asdict(review))
        hashes["external_review.json"] = sha256_file(journal / "external_review.json")
    events = checkpoint.parent / "events.jsonl"
    manifest = {**source, "invocation": checkpoint.parent.name,
                "input_sha256": sha256_file(root / "input/request.json"),
                "prior_event_bytes": events.stat().st_size, "prior_events_sha256": sha256_file(events),
                "prior_files_sha256": hashes, "budgets_reset": False}
    atomic_json(journal / "manifest.json", manifest)
    return journal


def audit_resumptions(root: Path, trace_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    events = (trace_root / "events.jsonl").read_bytes()
    for path in sorted((root / "input/resumptions").glob("*/manifest.json")):
        row = json.loads(path.read_text())
        rows.append(row)
        if row["invocation"] != trace_root.name or row.get("budgets_reset") is not False:
            errors.append("resumption changed invocation or reset budgets")
        if row.get("source_dirty"):
            errors.append("resumption started with a dirty source tree")
        if sha256_file(root / "input/request.json") != row["input_sha256"]:
            errors.append("original evaluation input changed after resumption")
        size = row["prior_event_bytes"]
        if size > len(events) or hashlib.sha256(events[:size]).hexdigest() != row["prior_events_sha256"]:
            errors.append("pre-resumption events were changed")
        for name, expected in row["prior_files_sha256"].items():
            if name not in {"checkpoint.json", "facts.json", "summary.json", "external_review.json", "proposal.md"}:
                errors.append("invalid journal file name")
            elif not (path.parent / name).is_file() or sha256_file(path.parent / name) != expected:
                errors.append("pre-resumption snapshot differs from journal hash")
    return rows, errors
