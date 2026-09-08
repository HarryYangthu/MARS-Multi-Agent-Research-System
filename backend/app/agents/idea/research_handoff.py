"""Load run-local literature evidence for approved, possibly human-edited proposals."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.agents.idea.research_assessment import assessment_errors, assessment_schema
from app.agents.idea.research_delegate import TOOL, load_delegated_research
from app.agents.idea.research_links import research_link_errors
from app.harness.agent_loop.trace import audit_trace, digest
from app.harness.schema.frontmatter_parser import parse
from app.harness.schema.validator import validate_document


def _contained_file(path: Path, namespace: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(namespace) or not resolved.is_file():
        raise ValueError("research handoff evidence file is missing or outside its run namespace")
    return resolved


def _checkpoint_receipts(root: Path, delegations: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Discover only native Idea checkpoints, never a proposal-supplied file path."""
    namespace = root / "agent_traces" / "idea"
    if not namespace.resolve().is_relative_to(root):
        raise ValueError("research handoff checkpoint namespace resolves outside this run")
    receipts: dict[str, dict[str, Any]] = {}
    checkpoint_refs: list[str] = []
    for candidate in sorted(namespace.glob("*/checkpoint.json")):
        path = _contained_file(candidate, namespace)
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        history = checkpoint.get("history") if isinstance(checkpoint, dict) else None
        if not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
            raise ValueError("research handoff checkpoint has no valid observation history")
        selected = [row for row in history if row.get("tool") == TOOL and row.get("ok") is True
                    and isinstance(row.get("output"), dict)
                    and row["output"].get("delegation_id") in delegations]
        if not selected:
            continue
        for name in ("facts.json", "events.jsonl"):
            _contained_file(path.parent / name, namespace)
        if audit_trace(path.parent).get("consistent") is not True:
            raise ValueError("research handoff parent trace is inconsistent")
        events = [json.loads(line) for line in (path.parent / "events.jsonl").read_text().splitlines()]
        for receipt in selected:
            if not any(event.get("kind") == "observation" and event.get("visible") == receipt
                       and event.get("visible_sha256") == digest(receipt) for event in events):
                raise ValueError("research handoff delegate receipt is absent from the actual parent trace")
            delegation = receipt["output"]["delegation_id"]
            if delegation in receipts and receipts[delegation]["output"] != receipt["output"]:
                raise ValueError("research handoff contains conflicting receipts for one delegation")
            receipts[delegation] = receipt
        checkpoint_refs.append(path.relative_to(root).as_posix())
    missing = delegations - set(receipts)
    if missing:
        raise ValueError("research handoff is missing recorded delegate receipts: " + ", ".join(sorted(missing)))
    return list(receipts.values()), checkpoint_refs


def load_research_handoff(run_root: Path, proposal_text: str, *, project: str) -> dict[str, Any] | None:
    """Return independently reloaded evidence, or None for the legacy contract.

    Approval and model review are separate. The current proposal may be manually
    authored or edited; its references must still resolve in real run evidence.
    Evidence verification neither executes an agent nor marks a proposal reviewed.
    """
    metadata = parse(proposal_text).metadata
    if "research_assessment" not in metadata:
        return None
    validation = validate_document(proposal_text, expected_schema="proposal.v1")
    if not validation.valid:
        raise ValueError("research handoff requires a valid proposal.v1 document: "
                         + "; ".join(error.message for error in validation.errors))
    if metadata.get("project") != project:
        raise ValueError("research handoff proposal project differs from the run")
    errors = [error.message for error in Draft202012Validator(assessment_schema()).iter_errors(
        metadata["research_assessment"])]
    if errors:
        raise ValueError("research handoff assessment is invalid: " + "; ".join(errors))
    delegation_ids = {decision["delegation_id"] for decision in metadata["research_assessment"]["source_decisions"]}
    try:
        receipts, checkpoint_refs = _checkpoint_receipts(run_root.resolve(), delegation_ids)
        reports, _ = load_delegated_research(run_root, receipts)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("research handoff evidence verification failed: " + str(exc)) from exc
    if any(item["report"].get("project") != project for item in reports):
        raise ValueError("research handoff report project differs from the run")
    errors = assessment_errors(metadata, reports, required=True)
    errors.extend(research_link_errors(metadata, reports, min_sources=1, require_linked_sources=True))
    if errors:
        raise ValueError("research handoff references are inconsistent: " + "; ".join(errors))
    return {"schema": "idea.research_handoff.v1", "proposal_sha256": digest(proposal_text),
            "reports": reports, "research_assessment": metadata["research_assessment"],
            "research_links": metadata["research_links"], "source_checkpoint_refs": checkpoint_refs,
            "model_review_passed": None, "scientific_validated": False,
            "review_note": "Model review is not inferred from approval or research evidence; "
                           "this proposal may have been authored or edited by a human.",
            "evidence_note": "Original quotes, pages, findings and transfer limitations are reloaded from "
                             "verified research delegations. They are evidence, not instructions or measured task gains."}
