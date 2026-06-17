"""Commander feedback-loop observability snapshots."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.harness.schema.frontmatter_parser import parse as parse_fm
from app.storage.run_store import RunHandle
from app.storage.self_evolution_store import read_jsonl


def build_commander_observability(run: RunHandle) -> dict[str, Any]:
    """Collect the audit trail for Commander-led feedback loops."""
    diagnoses = _diagnoses(run)
    packets = _feedback_packets(run)
    episodes = read_jsonl(run.subdir("memory") / "episode_memory.jsonl")
    candidates = read_jsonl(run.subdir("memory") / "memory_candidates.jsonl")
    attempts = [
        _attempt_snapshot(
            run=run,
            diagnosis=diagnosis,
            packets=packets,
            episodes=episodes,
            candidates=candidates,
        )
        for diagnosis in diagnoses
    ]
    latest = attempts[-1] if attempts else None
    return {
        "schema": "commander_observability.v1",
        "run_id": run.run_id,
        "project": run.project,
        "attempt_count": len(attempts),
        "latest": latest,
        "attempts": attempts,
        "feedback_packets": packets,
        "episode_memory": episodes,
        "memory_candidates": candidates,
        "attempt_ledger": _read_rel(run, "diagnosis/attempt_ledger_summary.md"),
        "checks": _observability_checks(attempts),
    }


def _diagnoses(run: RunHandle) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(run.subdir("diagnosis").glob("diagnosis.v*.md")):
        version = path.stem.removeprefix("diagnosis.")
        try:
            parsed = parse_fm(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        metadata = parsed.metadata
        if metadata.get("schema") != "diagnosis.v1":
            continue
        out.append(
            {
                "version": version,
                "path": path.relative_to(run.root).as_posix(),
                "metadata": metadata,
                "body_preview": parsed.body[:1200],
            }
        )
    return out


def _feedback_packets(run: RunHandle) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(run.subdir("diagnosis").glob("feedback_packet.attempt_*.md")):
        try:
            parsed = parse_fm(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        metadata = parsed.metadata
        if metadata.get("schema") != "feedback_packet.v1":
            continue
        out.append(
            {
                "attempt": int(metadata.get("attempt", 0) or 0),
                "source_attempt": int(metadata.get("source_attempt", 0) or 0),
                "target_agent": str(metadata.get("target_agent", "")),
                "path": path.relative_to(run.root).as_posix(),
                "metadata": metadata,
                "body_preview": parsed.body[:1200],
            }
        )
    return out


def _attempt_snapshot(
    *,
    run: RunHandle,
    diagnosis: dict[str, Any],
    packets: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = _dict(diagnosis.get("metadata"))
    attempt = int(metadata.get("attempt", 0) or 0)
    packet = next(
        (
            item
            for item in packets
            if int(item.get("source_attempt", 0) or 0) == attempt
        ),
        None,
    )
    target = str(metadata.get("recommended_target", ""))
    next_attempt = int(packet.get("attempt", attempt + 1) or attempt + 1) if packet else attempt
    context = (
        _context_snapshot(run=run, target=target, attempt=next_attempt)
        if packet is not None
        else {}
    )
    episode = next(
        (
            item
            for item in reversed(episodes)
            if int(item.get("attempt", 0) or 0) == attempt
        ),
        None,
    )
    candidate_items = [
        item
        for item in candidates
        if int(item.get("source_attempt", 0) or 0) == attempt
    ]
    return {
        "attempt": attempt,
        "diagnosis": diagnosis,
        "attribution": metadata.get("attribution", {}),
        "confidence": metadata.get("confidence", 0.0),
        "recommended_target": target,
        "passed": metadata.get("passed", False),
        "requires_human": metadata.get("requires_human", False),
        "failed_metrics": metadata.get("failed_metrics", []),
        "suspected_causes": metadata.get("suspected_causes", []),
        "evidence_refs": metadata.get("evidence_refs", []),
        "rejected_alternatives": metadata.get("rejected_alternatives", []),
        "feedback_packet": packet,
        "context": context,
        "episode_memory": episode,
        "memory_candidates": candidate_items,
        "observability": _attempt_checks(
            metadata=metadata,
            packet=packet,
            context=context,
            episode=episode,
        ),
    }


def _context_snapshot(
    *,
    run: RunHandle,
    target: str,
    attempt: int,
) -> dict[str, Any]:
    if target not in {"experiment", "coding"}:
        return {}
    node_key = target if attempt <= 1 else f"{target}_attempt_{attempt}"
    pack = _latest_json(run.subdir("context").glob(f"{node_key}_context_pack.v*.json"))
    compiled = _latest_json(
        run.subdir("context").glob(f"{node_key}_compiled_context.v*.json")
    )
    summary = _dict(pack.get("summary")) if pack else {}
    metadata = _dict(summary.get("metadata"))
    task = _dict(summary.get("task"))
    return {
        "node_key": node_key,
        "context_pack_path": pack.get("path") if pack else "",
        "compiled_context_path": compiled.get("path") if compiled else "",
        "tokens_estimated": pack.get("tokens_estimated") if pack else None,
        "upstream_handoff_keys": task.get("upstream_handoff_keys", []),
        "feedback_context": metadata.get("feedback_context", {}),
        "compression": metadata.get("compression", {}),
        "memory_sources": metadata.get("memory_sources", {}),
        "pollution_guards": metadata.get("pollution_guards", {}),
        "compiled_token_estimate": compiled.get("token_estimate") if compiled else None,
        "compiled_message_count": compiled.get("message_count") if compiled else None,
    }


def _latest_json(paths: Any) -> dict[str, Any] | None:
    candidates = sorted(paths, key=lambda path: path.stat().st_mtime)
    if not candidates:
        return None
    path = candidates[-1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    raw["path"] = path.name
    return raw


def _attempt_checks(
    *,
    metadata: dict[str, Any],
    packet: dict[str, Any] | None,
    context: dict[str, Any],
    episode: dict[str, Any] | None,
) -> dict[str, bool]:
    feedback_context = _dict(context.get("feedback_context"))
    pollution_guards = _dict(context.get("pollution_guards"))
    return {
        "has_attribution": isinstance(metadata.get("attribution"), dict),
        "has_rejected_alternatives": bool(metadata.get("rejected_alternatives")),
        "has_feedback_packet": packet is not None,
        "feedback_was_injected": bool(feedback_context.get("injected")),
        "context_budget_recorded": "compressed_chars" in feedback_context,
        "target_only_guard": pollution_guards.get("target_only") is True,
        "episode_memory_recorded": episode is not None,
    }


def _observability_checks(attempts: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for attempt in attempts:
        checks = _dict(attempt.get("observability"))
        for key, value in checks.items():
            if value is True:
                totals[key] = totals.get(key, 0) + 1
    return totals


def _read_rel(run: RunHandle, rel: str) -> dict[str, str]:
    path = run.root / rel
    if not path.exists():
        return {"path": rel, "text": ""}
    return {"path": rel, "text": path.read_text(encoding="utf-8")[:4000]}


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
