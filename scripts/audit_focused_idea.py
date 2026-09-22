"""Audit a completed real Idea run and export compact, reproducible evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from loguru import logger

from app.agents.idea.acceptance import inspect_native_idea_run
from app.agents.idea.focused_research import focused_handoff
from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.schema.frontmatter_parser import parse
from app.storage.run_store import RunStore
from app.storage.artifact_store import ArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    run = RunStore().get(args.run_id)
    if run is None:
        raise ValueError("run not found")
    versions = [ref for ref in ArtifactStore(run).list_versions(agent_dir="idea", stem="idea_proposal")
                if ref.version.startswith("v")]
    if not versions:
        raise ValueError("run has no versioned Idea proposal")
    proposal = versions[-1].path
    text = proposal.read_text()
    audit = inspect_native_idea_run(run, proposal)
    if not audit["passed"]:
        raise ValueError("run has not passed: " + "; ".join(audit["errors"]))
    handoff = focused_handoff(run.root, text, run.project)
    if not handoff["model_review_passed"]:
        raise ValueError("independent model review is incomplete")
    metadata = parse(text).metadata
    knowledge_path = run.root / "input/project_knowledge.v1.json"
    if knowledge_path.exists():
        knowledge = json.loads(knowledge_path.read_text())
        if digest(knowledge["content"]) != knowledge["sha256"]:
            raise ValueError("knowledge snapshot hash mismatch")
        knowledge_contents = [knowledge["content"]]
    else:
        from app.harness.context.folder_context import load_folder_context
        knowledge = load_folder_context(run.project, run.root)
        if knowledge is None:
            raise ValueError("run has no project knowledge snapshot")
        knowledge_contents = [file["content"] for file in knowledge["files"]]
    trace = Path(handoff["review_receipt"]["trace_root"])
    events = [json.loads(line) for line in (trace / "events.jsonl").read_text().splitlines()]
    for event in events:
        if event["kind"] == "model_request":
            if not all(any(content in message.get("content", "") for message in event["visible"])
                       for content in knowledge_contents):
                raise ValueError("full project knowledge missing from an actual model input")
    checkpoints = json.loads((trace / "checkpoint.json").read_text())
    urls: set[str] = set()
    for observation in checkpoints["history"]:
        if observation.get("tool", "").startswith("search."):
            output = observation.get("output") or {}
            urls.update(hit["url"] for hit in output.get("hits", []) if hit.get("url"))
    sources = []
    for sid, readings in handoff["source_readings"].items():
        sources.append({"source_id": sid, "title": readings[0]["title"], "sha256": readings[0]["sha256"],
            "archive": readings[0]["download_path"], "read_windows": len(readings),
            "visible_pages": sorted({page["page"] for reading in readings for page in reading.get("visible_pages", [])}),
            "whole_pdf_read_claimed": False})
    from app.harness.agent_loop.context import reading_coverage_index
    requests = [event for event in events if event["kind"] == "model_request"]
    responses = [event for event in events if event["kind"] == "model_response"]
    report = {"run_id": run.run_id, "proposal_version": versions[-1].version, "schema_valid": True, "host_audit_passed": True,
        "independent_review_passed": True,
        "cross_model_review_passed": handoff["review_receipt"]["cross_model"], "review": handoff["review_receipt"],
        "knowledge_sha256": knowledge["sha256"], "full_knowledge_in_every_model_request": True,
        "search_hits_unique_urls": len(urls), "selection_decisions": len(metadata["research_context"]["sources"]),
        "adopted_sources": sources, "counts": audit["counts"], "proposal_sha256": digest(text),
        "human_summary": metadata["human_summary"],
        "usage": checkpoints.get("usage"), "usage_complete": checkpoints.get("usage_complete"),
        "first_model_request_at": requests[0]["time"] if requests else None,
        "last_model_response_at": responses[-1]["time"] if responses else None,
        "document_revisions": sum(event["kind"] == "document_revision" for event in events),
        "reading_coverage": reading_coverage_index(checkpoints["history"]),
        "reviews": [event["visible"] for event in events if event["kind"] == "reflection"],
        "simulation_executed": False, "scientific_validated": False}
    target = run.root / "idea/focused_verification.json"
    atomic_json(target, report)
    logger.info("Audit passed: {} adopted sources, {} model calls; evidence {}", len(sources),
                report["counts"]["model_requests"], target)


if __name__ == "__main__":
    main()
