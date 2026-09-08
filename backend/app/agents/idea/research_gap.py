"""Negative research outcomes grounded in observations, never usable findings."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.agents.idea.research import canonical_source, evidence_inventory
from app.agents.idea.research_dossier import dossier_schema
from app.agents.idea.source_identity import SourceIdentityIndex
from app.harness.agent_loop.stop import LoopStop, LoopStopView
from app.harness.schema.frontmatter_parser import parse

GAP_SCHEMA = "research_gap.v1"
STOP_CONTRACT = "idea.research_evidence_stop.v2"


def gap_schema() -> dict[str, Any]:
    text = {"type": "string", "minLength": 1, "maxLength": 800, "pattern": r"\S"}
    return {"type": "object", "additionalProperties": False,
            "required": ["schema", "project", "human_summary", "reason", "remaining_gaps", "next_actions"],
            "properties": {"schema": {"const": GAP_SCHEMA},
                           "project": {"type": "string", "minLength": 1, "maxLength": 200},
                           "human_summary": {**text, "maxLength": 600}, "reason": text,
                           "remaining_gaps": {"type": "array", "minItems": 1, "maxItems": 10,
                                              "uniqueItems": True, "items": text},
                           "next_actions": {"type": "array", "maxItems": 5, "uniqueItems": True,
                                            "items": text}}}


def research_submission_schema() -> dict[str, Any]:
    return {"oneOf": [dossier_schema(), gap_schema()]}


def gap_errors(metadata: dict[str, Any], *, project: str) -> list[str]:
    errors = [f"/{'/'.join(str(p) for p in error.absolute_path)}: {error.message}"
              for error in Draft202012Validator(gap_schema()).iter_errors(metadata)]
    if metadata.get("project") != project:
        errors.append("/project: gap project must match delegated project")
    return errors


def _gap_declaration(text: str, *, project: str) -> dict[str, Any] | None:
    try:
        document = parse(text)
        if (document.metadata.get("schema") == GAP_SCHEMA
                and not gap_errors(document.metadata, project=project)
                and document.body.strip() == document.metadata["human_summary"].strip()):
            return document.metadata
    except (ValueError, TypeError):
        pass
    return None


def material_state(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Count potentially citable pages, not just downloads or asserted reading."""
    inventory = evidence_inventory(observations)
    documents = SourceIdentityIndex(observations)
    reads: list[dict[str, Any]] = []
    identities: set[str] = set()
    for row in inventory["reads"]:
        pages = row.get("visible_pages", [])
        visible = [page for page in pages if isinstance(page, dict) and str(page.get("text", "")).strip()]
        if row.get("source_type") != "pdf" or not visible or not row.get("read_receipt"):
            continue
        identity = canonical_source(str(row.get("url", "")))
        if not documents.matching_hits(str(row.get("url", "")), read_receipt=str(row["read_receipt"])):
            continue
        try:
            receipt = json.loads(Path(row["read_receipt"]).read_text())
            if (not isinstance(receipt, dict) or not receipt.get("ok") or receipt.get("source_type") != "pdf"
                    or any(receipt.get(key) != row.get(key) for key in
                           ("sha256", "download_path", "visible_pages", "url", "download_url"))):
                continue
        except (OSError, ValueError, TypeError):
            continue
        identities.add(identity)
        reads.append({"url": row["url"], "sha256": row["sha256"], "read_receipt": row["read_receipt"],
                      "pages": [page["page"] for page in visible], "publication": identity})
    return {"counts": {**inventory["counts"], "distinct_read_publications": len(identities)},
            "read_sources": reads,
            "note": "Visible, source-matched pages are potential evidence, not verified interpretations or an accepted report."}


def evidence_stop(view: LoopStopView, *, min_sources: int, max_tool_steps: int,
                  tools: tuple[str, ...], project: str) -> LoopStop | None:
    if view.stage == "after_validation":
        gap = _gap_declaration(view.candidate, project=project)
        if gap is not None:
            return LoopStop("evidence_unavailable", gap["reason"],
                            {"origin": "researcher_gap_declaration", "declaration": gap})
        return None
    no_reading_budget = (view.counts["tool_dispatches"] >= max_tool_steps
                         or "search.fetch_sources" not in tools)
    if not no_reading_budget:
        return None
    state = material_state(view.observations)
    observed = state["counts"]["distinct_read_publications"]
    if observed >= min_sources:
        # Existing pages can still support a corrected report without more tools.
        return None
    reason = (f"Need {min_sources} distinct publications with source-matched visible PDF pages; "
              f"observed {observed}, and no reading tool budget is available. Report formatting cannot supply missing evidence.")
    return LoopStop("evidence_unavailable", reason,
                    {"origin": "host_material_floor", "min_sources": min_sources,
                     "observed_read_publications": observed, "remaining_gaps": [reason]})


def actual_attempts(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep failed source rows even when the enclosing batch has ok=False."""
    attempts: list[dict[str, Any]] = []
    source_fields = ("url", "title", "download_url", "ok", "error", "resource_key", "attempt_count",
                     "network_download_attempted", "network_download_performed", "reused", "source_max_mib",
                     "download_bytes_received", "download_wire_bytes_received", "download_expected_bytes",
                     "download_elapsed_seconds", "download_complete", "archive_complete",
                     "error_code", "previous_error_code", "retryable", "retry_blocked_reason", "read_receipt")
    for index, observation in enumerate(observations):
        output = observation.get("output")
        attempt = {"observation_index": index, "tool": observation.get("tool"),
                   "args": observation.get("args", {}), "ok": observation.get("ok"),
                   "error": observation.get("error"), "raw_ref": observation.get("raw_ref")}
        if isinstance(output, dict):
            attempt.update({key: output[key] for key in ("error_code", "retryable", "retry_blocked_reason") if key in output})
            rows = output.get("sources", [])
            if isinstance(rows, list):
                attempt["source_results"] = [{key: row[key] for key in source_fields if key in row}
                                              for row in rows if isinstance(row, dict)]
            if isinstance(output.get("hits"), list):
                attempt["hit_count"] = len(output["hits"])
        attempts.append(attempt)
    return attempts


def failure_record(*, delegation_id: str, trace_ref: str, checkpoint: dict[str, Any],
                   min_sources: int, max_tool_steps: int, max_model_calls: int,
                   gap: str, project: str) -> dict[str, Any]:
    observations = checkpoint["history"]
    material = material_state(observations)
    counts = checkpoint["counts"]
    termination = checkpoint.get("termination", {})
    declaration = (_gap_declaration(str(checkpoint.get("candidate", "")), project=project)
                   if checkpoint.get("status") == "evidence_unavailable" else None)
    issues = checkpoint.get("validation_issues", [])
    remaining = list(declaration["remaining_gaps"]) if declaration else []
    observed = material["counts"]["distinct_read_publications"]
    if observed < min_sources:
        remaining.append(f"Need {min_sources} distinct source-matched visible PDF publications; observed {observed}.")
    if not remaining:
        remaining = [str(issue) for issue in issues] or ["No accepted report resolving the delegated gap was produced: " + gap]
    attempts = actual_attempts(observations)
    unavailable = checkpoint["status"] == "evidence_unavailable"
    failure_type = "evidence_unavailable" if unavailable else (
        "report_validation_failed" if checkpoint["status"] == "validation_exhausted" else "research_runtime_failed")
    return {"schema": "research.failure.v1", "delegation_id": delegation_id,
            "status": checkpoint["status"], "failure_type": failure_type, "trace_ref": trace_ref,
            "delegated_gap": gap, "min_sources": min_sources,
            "feedback": str(checkpoint.get("feedback", ""))[:2400],
            "validation_issues": [str(issue)[:600] for issue in issues[:6]],
            "validation_issue_count": len(issues), "termination": termination,
            "observed_material_counts": material["counts"], "read_sources": material["read_sources"],
            "attempts": attempts, "remaining_gaps": remaining,
            "remaining_budget": {"tool_calls": max(0, max_tool_steps-counts["tool_dispatches"]),
                                 "model_calls": max(0, max_model_calls-counts["model_requests"])},
            "suggested_next_actions": declaration["next_actions"] if declaration else [],
            "recovery_guidance": [
                "Choose the next action from the specific missing evidence and actual source errors, not a repeated broad assignment.",
                "Read retryable/error_code/resource_key when present. URL aliases do not reset resource attempts; unavailable legacy fields are unknown.",
                "Already archived pages remain partial progress: a new child must obtain its own real reading observation before citing them.",
                "If no permitted recovery remains, preserve the unresolved gap. Search hits and failed downloads cannot satisfy the publication minimum."],
            "usable_as_final_evidence": False, "scientific_validated": False,
            "note": "This is a negative outcome, not an accepted report or a finding that relevant literature does not exist."}
