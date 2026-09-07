"""Native Idea run audit shared by headless and bridge callers.

This is an execution/schema/material report, never an experimental-success verdict.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.agents.idea.delivery import delivery_errors
from app.agents.idea.research import evidence_inventory, material_errors
from app.harness.agent_loop.trace import atomic_json, audit_trace, digest
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


def archive_baseline_input(
    *, run_root: Path, project: str, content: str, candidate_sha256: str,
) -> dict[str, Any] | None:
    """Archive caller-supplied text, never a model's claim that it read code."""
    if not content.strip():
        return None
    encoded = content.encode("utf-8")
    content_sha = hashlib.sha256(encoded).hexdigest()
    relative = f"input/idea_context/baseline_code.{content_sha}.json"
    path = run_root / relative
    if not path.resolve().is_relative_to(run_root.resolve()):
        raise ValueError("baseline input archive must remain inside its run")
    document = {"schema": "idea.input_context.v1", "source": "caller_upstream",
                "key": "baseline_code", "project": project, "content": content}
    if path.exists():
        if _json(path) != document:
            raise ValueError("existing baseline input archive does not match its content hash")
    else:
        atomic_json(path, document)
    return {"schema": "idea.input_evidence.v1", "kind": "baseline_code", "source": "caller_upstream",
            "path": relative, "sha256": content_sha, "bytes": len(encoded),
            "candidate_sha256": candidate_sha256}


def verify_baseline_input(
    *, run_root: Path, project: str, candidate_sha256: str, receipt: object,
) -> tuple[str | None, list[str]]:
    """Verify the original input bytes and their exact candidate binding."""
    prefix = "/input_evidence: "
    if not isinstance(receipt, dict) or receipt.get("schema") != "idea.input_evidence.v1":
        return None, [prefix + "unsupported input receipt"]
    if receipt.get("kind") != "baseline_code" or receipt.get("source") != "caller_upstream":
        return None, [prefix + "baseline evidence must originate from caller upstream input"]
    if receipt.get("candidate_sha256") != candidate_sha256:
        return None, [prefix + "input receipt is bound to a different candidate"]
    sha = receipt.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        return None, [prefix + "invalid input SHA-256"]
    relative = f"input/idea_context/baseline_code.{sha}.json"
    if receipt.get("path") != relative:
        return None, [prefix + "input archive path does not match its content hash"]
    path = run_root / relative
    if not path.resolve().is_relative_to(run_root.resolve()):
        return None, [prefix + "input archive resolves outside this run"]
    document = _json(path)
    if (document.get("schema") != "idea.input_context.v1" or document.get("source") != "caller_upstream"
            or document.get("key") != "baseline_code" or document.get("project") != project):
        return None, [prefix + "missing or mismatched caller input archive"]
    content = document.get("content")
    if not isinstance(content, str) or not content.strip():
        return None, [prefix + "archived baseline context is empty"]
    encoded = content.encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != sha:
        return None, [prefix + "archived baseline content hash mismatch"]
    if type(receipt.get("bytes")) is not int or receipt["bytes"] != len(encoded):
        return None, [prefix + "archived baseline byte count mismatch"]
    return content, []


def model_input_contains_baseline(messages: object, content: str) -> bool:
    """Match actual caller-context framing, not a citation or assistant assertion."""
    return isinstance(messages, list) and any(
        isinstance(message, dict) and message.get("role") == "user"
        and message.get("content") == "[untrusted upstream:baseline_code]\n" + content
        for message in messages
    )


def _baseline_was_model_input(trace_root: Path, content: str) -> bool:
    try:
        with (trace_root / "events.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("kind") != "model_request" or row.get("phase") == "reflect":
                    continue
                messages = row.get("visible")
                if (model_input_contains_baseline(messages, content)
                        and row.get("visible_sha256") == digest(messages)):
                    return True
    except (OSError, ValueError):
        return False
    return False


def validation_record_delivery_errors(metadata: dict[str, Any], record: dict[str, Any], *, body: str = "") -> list[str]:
    """Recheck the version actually enforced; do not upgrade historical receipts."""
    version = record.get("delivery_contract_version")
    if version is None:
        return []
    if version != "idea.handoff.v1":
        return ["/delivery_contract_version: unsupported recorded delivery contract"]
    return delivery_errors(metadata, str(record.get("scope", "method_proposal")),
                           body=body if record.get("body_policy") == "summary_only" else None)


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
                "delivery_contract_valid": None,
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
    delivery_contract_valid: bool | None = None
    input_evidence_refs: list[str] = []
    if not records:
        errors.append("No host validation receipt tied to this exact candidate hash.")
    elif validation.valid:
        # Every recorded requirement set for this exact candidate must hold.
        material_failures = []
        for record in records:
            req = record.get("requirements", {})
            delivery_failures = validation_record_delivery_errors(parse(text).metadata, record, body=parse(text).body)
            if record.get("delivery_contract_version") is not None:
                delivery_contract_valid = delivery_contract_valid is not False and not delivery_failures
            material_failures.extend(delivery_failures)
            material_failures.extend(material_errors(
                parse(text).metadata, observations,
                min_sources=int(req.get("min_sources", 1)), min_pdfs=int(req.get("min_pdfs", 1)),
                require_budget=bool(req.get("require_parameter_budget", False)),
                max_ratio=float(req.get("max_parameter_ratio", 1.2)),
            ))
            supplied_baseline_verified = False
            input_receipts = record.get("input_evidence", [])
            if not isinstance(input_receipts, list):
                material_failures.append("/input_evidence: receipt list required")
                input_receipts = []
            for receipt in input_receipts:
                content, input_errors = verify_baseline_input(
                    run_root=run.root, project=run.project, candidate_sha256=digest(text), receipt=receipt,
                )
                material_failures.extend(input_errors)
                if content is not None:
                    if _baseline_was_model_input(path.parent, content):
                        supplied_baseline_verified = True
                        input_evidence_refs.append(str(receipt["path"]))
                    else:
                        material_failures.append("/input_evidence: archived baseline was not found in this invocation's audited model input")
            if (record.get("scope") == "project_proposal" and not supplied_baseline_verified
                    and not any(o.get("ok") and o.get("tool") == "code.repo_reader" for o in observations)):
                material_failures.append("Project scope lacks actual baseline code evidence.")
        material_ready = not material_failures
        errors.extend(dict.fromkeys(material_failures))
    for name in ("evidence_index.v1.json", "tool_results.v1.json"):
        if not (run.subdir("idea") / "research" / name).is_file():
            errors.append(f"Missing archived research file: {name}")
    inventory = evidence_inventory(observations)
    return {"passed": not errors, "errors": errors, "schema_valid": validation.valid,
            "delivery_contract_valid": delivery_contract_valid,
            "input_evidence_refs": list(dict.fromkeys(input_evidence_refs)),
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
             f"- Delivery contract: {report.get('delivery_contract_valid')} (None means no versioned delivery receipt)",
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
