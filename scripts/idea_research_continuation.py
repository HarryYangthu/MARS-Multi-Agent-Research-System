"""Immutable continuation forks for real delegated research evaluations."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents.idea.research_delegate import load_delegated_research, resumed_delegation_count
from app.harness.agent_loop.trace import atomic_json, audit_trace, canonical
from app.harness.llm.provider_base import Message
from scripts.idea_live_resume import messages_match_snapshot, resume_scenario


def file_sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def file_manifest(root: Path) -> dict[str, str]:
    """Regular files only: a copied symlink could permit writes to the source."""
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("continuation source must contain only regular files and directories")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_sha(path)
    return result


def check_terminal_state(state: dict[str, Any], policy: dict[str, Any]) -> None:
    if state.get("status") not in {"model_error", "interrupted"}:
        raise ValueError("continuation only supports model_error/interrupted; terminal budgets are never reset")
    if state.get("pending") == "tool" or state.get("pending_batch"):
        raise ValueError("unknown tool or batch outcome: automatic replay is forbidden")
    if state.get("pending") not in {None, "model"}:
        raise ValueError("unknown pending operation")
    if policy.get("trace") != "full":
        raise ValueError("continuation requires full trace mode")
    used = state.get("counts", {}).get("model_requests")
    maximum = policy.get("max_model_calls")
    if type(used) is not int or type(maximum) is not int or not 0 <= used < maximum:
        raise ValueError("original model request budget is invalid or exhausted")


def remaining_seconds(initial: dict[str, Any], summary: dict[str, Any]) -> float:
    maximum = initial.get("resource_limits", {}).get("max_seconds")
    elapsed = summary.get("cumulative_duration_seconds", summary.get("duration_seconds"))
    if (not isinstance(maximum, (int, float)) or isinstance(maximum, bool)
            or not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
            or not math.isfinite(maximum) or not math.isfinite(elapsed)
            or not 0 <= elapsed < maximum <= 3600):
        raise ValueError("original runtime budget missing, invalid or exhausted; continuation cannot invent a new budget")
    return float(maximum - elapsed)


def external_evidence(root: Path) -> dict[str, str]:
    """Keep original absolute read paths; verify their bytes without rewriting receipts."""
    result = {}
    for checkpoint in root.glob("agent_traces/*/*/checkpoint.json"):
        for row in json.loads(checkpoint.read_text()).get("history", []):
            if row.get("tool") != "search.fetch_sources" or not row.get("ok"):
                continue
            for source in row.get("output", {}).get("sources", []):
                if not source.get("ok"):
                    continue
                document = Path(source["download_path"])
                if not document.is_absolute() or file_sha(document) != source["sha256"]:
                    raise ValueError("original source document path/hash mismatch")
                result[str(document)] = source["sha256"]
                if source.get("read_receipt"):
                    receipt_path = Path(source["read_receipt"])
                    if not receipt_path.is_absolute():
                        raise ValueError("original read receipt must have an absolute path")
                    receipt = json.loads(receipt_path.read_text())
                    if any(receipt.get(key) != source.get(key) for key in
                           ("sha256", "download_path", "visible_pages", "url", "download_url")):
                        raise ValueError("original read receipt differs from its actual observation")
                    result[str(receipt_path)] = file_sha(receipt_path)
    return result


@dataclass(frozen=True)
class Continuation:
    root: Path
    initial: dict[str, Any]
    summary: dict[str, Any]
    checkpoint: Path
    state: dict[str, Any]
    files: dict[str, str]
    external_files: dict[str, str]
    remaining_seconds: float

    @property
    def scenario(self) -> dict[str, Any]:
        return resume_scenario(self.initial)


def load_continuation(root: Path, *, source_commit: str, source_tree: str) -> Continuation:
    root = root.resolve()
    initial = json.loads((root / "input/request.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    if initial.get("source_commit") != source_commit or initial.get("source_tree") != source_tree:
        raise ValueError("continuation requires the original source commit and tree; historical runs cannot use changed code")
    if initial.get("source_dirty") or initial.get("credential_persisted") is not False:
        raise ValueError("continuation requires a clean original source snapshot without persisted credentials")
    if initial.get("run_id") != root.name or summary.get("run_id") != root.name:
        raise ValueError("continuation source identity differs from its directory")
    if summary.get("status") != "failed":
        raise ValueError("continuation requires a terminal failed evaluation summary")
    paths = list(root.glob("agent_traces/idea/*/checkpoint.json"))
    if len(paths) != 1:
        raise ValueError("continuation requires exactly one Idea invocation")
    checkpoint = paths[0]
    state = json.loads(checkpoint.read_text())
    check_terminal_state(state, initial["lead_config"]["loop"])
    counts: dict[str, int] = {}
    usage: dict[str, int | float] = {}
    usage_complete = True
    all_checkpoints = list(root.glob("agent_traces/*/*/checkpoint.json"))
    if {path.parent for path in all_checkpoints} != {path.parent for path in root.glob("agent_traces/*/*/facts.json")}:
        raise ValueError("continuation checkpoint/facts files are incomplete")
    for saved_checkpoint in all_checkpoints:
        audit = audit_trace(saved_checkpoint.parent)
        recorded = json.loads(saved_checkpoint.read_text())
        if not audit["consistent"] or any(recorded.get(k) != audit["facts"].get(k)
                                           for k in ("status", "counts", "usage", "usage_complete", "fingerprint", "pending")):
            raise ValueError("continuation requires consistent original traces and checkpoints")
        for key, value in audit["facts"]["counts"].items():
            counts[key] = counts.get(key, 0) + int(value)
        for key, value in audit["facts"]["usage"].items():
            usage[key] = usage.get(key, 0) + value
        usage_complete = usage_complete and audit["facts"]["usage_complete"]
    if counts != summary.get("counts") or usage != summary.get("usage") or usage_complete != summary.get("usage_complete"):
        raise ValueError("source summary totals differ from actual inherited trace accounting")
    load_delegated_research(root, state["history"])
    resumed_delegation_count(root, state["history"], run_id=root.name, parent_invocation=str(checkpoint.parent))
    external = external_evidence(root)
    return Continuation(root, initial, summary, checkpoint, state, file_manifest(root), external,
                        remaining_seconds(initial, summary))


def check_configuration(source: Continuation, *, lead: dict[str, Any], child: dict[str, Any],
                        messages: list[Message]) -> None:
    if canonical(lead) != canonical(source.initial["lead_config"]) or canonical(child) != canonical(source.initial["child_config"]):
        raise ValueError("continuation lead/child configuration differs from the original")
    if not messages_match_snapshot(messages, source.initial["messages"]):
        raise ValueError("continuation task context differs from the original; no model call made")


def copy_verified_files(source: Path, target: Path, hashes: dict[str, str]) -> None:
    """Copy an actual archive exactly; this function performs no Agent execution."""
    source, target = source.resolve(), target.resolve()
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("continuation must use a separate new run directory")
    if file_manifest(source) != hashes:
        raise ValueError("source archive changed before continuation copy")
    shutil.copytree(source, target, symlinks=False)
    if file_manifest(target) != hashes or file_manifest(source) != hashes:
        raise ValueError("source/copy changed during continuation; no model call made")


def fork_continuation(source: Continuation, target: Path) -> dict[str, Any]:
    copy_verified_files(source.root, target, source.files)
    if any(file_sha(Path(path)) != expected for path, expected in source.external_files.items()):
        raise ValueError("original external evidence changed during continuation")
    load_delegated_research(target, source.state["history"])
    journal = target / "input" / "continuation_history" / source.root.name
    journal.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(target / "input/request.json", journal / "request.json")
    shutil.copyfile(target / "summary.json", journal / "summary.json")
    if (target / "input/continuation.json").exists():
        shutil.copyfile(target / "input/continuation.json", journal / "continuation.json")
    manifest = {"schema": "idea.research_continuation.v1", "source_run_root": str(source.root),
                "source_run_id": source.root.name, "continuation_run_id": target.name,
                "invocation": source.checkpoint.parent.name,
                "source_files_sha256": source.files, "original_read_files_sha256": source.external_files,
                "inherited_counts": source.summary.get("counts", {}),
                "inherited_usage": source.summary.get("usage", {}),
                "inherited_usage_complete": source.summary.get("usage_complete", False),
                "inherited_duration_seconds": source.summary.get("cumulative_duration_seconds", source.summary["duration_seconds"]),
                "remaining_seconds": source.remaining_seconds, "budgets_reset": False,
                "original_receipt_paths_rewritten": False, "model_calls_during_fork": 0,
                "interpretation": "Copied history is inherited execution, not new calls. Absolute source receipts remain read-only original evidence. A prepared fork is not research acceptance."}
    atomic_json(target / "input/continuation.json", manifest)
    resumed_delegation_count(target, source.state["history"], run_id=target.name,
                            parent_invocation=str(target / source.checkpoint.parent.relative_to(source.root)))
    return manifest
