"""Recover an unapproved Idea draft without replaying a completed invocation."""
from __future__ import annotations

import json
from pathlib import Path

from app.harness.agent_loop.context import reading_coverage_index, source_receipt_index
from app.harness.agent_loop.trace import audit_trace
from app.harness.schema.validator import validate_document


def failed_idea_revision_context(root: Path, project: str) -> dict[str, str]:
    paths = sorted((root / "agent_traces/idea").glob("*/checkpoint.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 32_000_000:
            continue
        try:
            state = json.loads(path.read_text())
            rejected_quota = None
            if state.get("status") == "model_error":
                from app.harness.agent_loop.provider_rejection_resume import checkpoint_quota_rejection_receipt
                rejected_quota = checkpoint_quota_rejection_receipt(path.parent, state, run_root=root)
            if ((state.get("status") not in {"validation_exhausted", "reflection_rejected", "budget_exhausted", "evidence_unavailable"}
                    and rejected_quota is None)
                    or state.get("pending") and rejected_quota is None or state.get("pending_batch")):
                continue
            audit = audit_trace(path.parent)
            if not audit.get("consistent") or not state.get("counts", {}).get("model_responses"):
                continue
            text = state.get("candidate", "")
            result = validate_document(text, expected_schema="proposal.v1")
            if not result.valid or result.metadata.get("project") != project:
                continue
            ref = path.relative_to(root).as_posix()
            return {"revision_candidate": "Previously model-generated draft; NOT accepted or approved. Source: " + ref
                    + "\nRevise against the current requirements. Preserve useful work but verify all claims.\n" + text,
                "revision_reading_index": "Historical reading index, not observations of this new invocation. "
                    "Reuse the archived source_id with search.fetch_sources to read required method windows; "
                    "do not invent fresh receipts or assume historical readings meet the new gate.\n"
                    + json.dumps({"source_ref": ref, "sources": source_receipt_index(state.get("history", [])),
                                  "coverage": reading_coverage_index(state.get("history", []))}, ensure_ascii=False)}
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return {}
