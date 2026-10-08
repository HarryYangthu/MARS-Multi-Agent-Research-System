"""Recover an unapproved Idea draft without replaying a completed invocation."""
from __future__ import annotations

import json
from pathlib import Path

from app.harness.agent_loop.context import reading_coverage_index, source_receipt_index
from app.harness.agent_loop.revision_seed import load_failed_revision_seed


def failed_idea_revision_context(root: Path, project: str) -> dict[str, str]:
    seed = load_failed_revision_seed(root, project=project, agent="idea", schema="proposal.v1")
    if seed is None:
        return {}
    ref = seed.receipt["candidate_ref"].removesuffix("#candidate")
    readings = list(seed.observations)
    return {"revision_candidate": "Previously model-generated draft; NOT accepted or approved. Source: " + ref
            + "\nRevise against the current requirements. Preserve useful work but verify all claims.\n" + seed.candidate,
        "revision_reading_index": "Historical reading index, not observations of this new invocation. "
            "The native revision loop verifies and restores these original immutable receipts without tool replay. "
            "Read again only to close a specific missing window; do not invent receipts or inherit acceptance.\n"
            + json.dumps({"source_ref": ref, "sources": source_receipt_index(readings),
                          "coverage": reading_coverage_index(readings)}, ensure_ascii=False)}
