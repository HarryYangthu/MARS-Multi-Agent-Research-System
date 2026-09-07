"""Native Idea run audit shared by headless and bridge callers.

This is an execution/schema/material report, never an experimental-success verdict.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agents.idea.research import evidence_inventory, material_errors
from app.harness.agent_loop.trace import audit_trace, digest
from app.harness.schema.frontmatter_parser import parse
from app.harness.schema.validator import validate_document
from app.storage.artifact_store import ArtifactRef
from app.storage.run_store import RunHandle


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _proposal(run: RunHandle) -> Path | None:
    paths = list(run.subdir("idea").glob("idea_proposal.v*.md"))
    def version(path: Path) -> int:
        try:
            return int(path.stem.rsplit(".v", 1)[-1])
        except ValueError:
            return -1
    return max(paths, key=version) if paths else None


def inspect_native_idea_run(run: RunHandle, proposal: Path | None) -> dict[str, Any]:
    errors: list[str] = []
    text = proposal.read_text(encoding="utf-8") if proposal and proposal.is_file() else ""
    if not text:
        errors.append("No proposal artifact found.")
    validation = validate_document(text, expected_schema="proposal.v1")
    if not validation.valid:
        errors.append("proposal.v1 schema validation failed.")
    matches = []
    for path in (run.root / "agent_traces" / "idea").glob("*/checkpoint.json"):
        state = _json(path)
        if text and state.get("candidate") == text:
            matches.append((path, state))
    if len(matches) != 1:
        errors.append("Exactly one matching native invocation checkpoint is required.")
        return {"passed": False, "errors": errors, "schema_valid": validation.valid,
                "material_ready": False, "scientific_validated": False, "project_ready": False}
    path, state = matches[0]
    if state.get("status") != "passed" or state.get("pending") is not None:
        errors.append("Native loop has not finished successfully.")
    try:
        trace = audit_trace(path.parent)
        if not trace["consistent"]:
            errors.append("Trace counts are inconsistent.")
    except (OSError, ValueError, KeyError):
        errors.append("Trace could not be audited.")
    counts = state.get("counts", {})
    if counts.get("sdk_attempts", 0) < 1 or counts.get("model_responses", 0) < 1:
        errors.append("No actual model attempt/response is recorded.")
    if counts.get("reflections", 0) and not state.get("reflection_accepted"):
        errors.append("Reflection has unresolved issues.")
    observations = state.get("history", [])
    records = [_json(p) for p in (run.subdir("idea") / "validation").glob("*.json")]
    records = [r for r in records if r.get("candidate_sha256") == digest(text)]
    material_ready = False
    if not records:
        errors.append("No host validation receipt tied to this exact candidate hash.")
    elif validation.valid:
        # Every recorded requirement set for this exact candidate must hold.
        material_failures = []
        for record in records:
            req = record.get("requirements", {})
            material_failures.extend(material_errors(
                parse(text).metadata, observations,
                min_sources=int(req.get("min_sources", 1)), min_pdfs=int(req.get("min_pdfs", 1)),
                require_budget=bool(req.get("require_parameter_budget", False)),
                max_ratio=float(req.get("max_parameter_ratio", 1.2)),
            ))
            if record.get("scope") == "project_proposal" and not any(
                o.get("ok") and o.get("tool") == "code.repo_reader" for o in observations
            ):
                material_failures.append("Project scope lacks actual baseline code evidence.")
        material_ready = not material_failures
        errors.extend(dict.fromkeys(material_failures))
    for name in ("evidence_index.v1.json", "tool_results.v1.json"):
        if not (run.subdir("idea") / "research" / name).is_file():
            errors.append(f"Missing archived research file: {name}")
    inventory = evidence_inventory(observations)
    return {"passed": not errors, "errors": errors, "schema_valid": validation.valid,
            "material_ready": material_ready, "scientific_validated": False, "project_ready": False,
            "trace_root": str(path.parent), "counts": counts, "usage_complete": state.get("usage_complete"),
            "reflection_accepted": state.get("reflection_accepted"), "tools": observations,
            "evidence": inventory["counts"]}


def build_idea_acceptance_report(
    *, run: RunHandle, artifact_ref: ArtifactRef | None = None, node_key: str = "idea",
) -> str:
    report = inspect_native_idea_run(run, artifact_ref.path if artifact_ref else _proposal(run))
    status = "PASS" if report["passed"] else "FAIL"
    lines = ["# Idea Agent execution and material audit", "", f"- Run: `{run.run_id}`",
             f"- Node: `{node_key}`", f"- Overall: **{status}**", "",
             "This verdict checks recorded execution, schema and research material only.",
             "Model Reflection is self-review. Independent method review and real baseline experiments remain separate.",
             "A GUI, fixed search order, synthetic source summaries and a predefined proposal recipe are not required.", "",
             f"- Schema: {report['schema_valid']}; material: {report['material_ready']}",
             f"- Scientific validation: {report['scientific_validated']}; project ready: {report['project_ready']}",
             f"- Counts: {report.get('counts', {})}", f"- Evidence: {report.get('evidence', {})}", ""]
    lines.extend("- " + error for error in report["errors"])
    lines.extend(["", "| Tool | Success | Agent's recorded reason |", "| --- | --- | --- |"])
    for item in report.get("tools", []):
        reason = str(item.get("reason", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item.get('tool')} | {item.get('ok')} | {reason} |")
    return "\n".join(lines) + "\n"


def write_idea_acceptance_report(
    *, run: RunHandle, artifact_ref: ArtifactRef | None = None, node_key: str = "idea",
) -> Path:
    target = run.subdir("idea") / "idea_agent_acceptance_report.md"
    target.write_text(build_idea_acceptance_report(run=run, artifact_ref=artifact_ref, node_key=node_key),
                      encoding="utf-8")
    return target
