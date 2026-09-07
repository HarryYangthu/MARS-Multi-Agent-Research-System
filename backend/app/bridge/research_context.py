"""Caller-owned research material, preserved across agents and run recovery."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictStr

from app.harness.agent_loop.trace import atomic_json
from app.storage.run_store import RunHandle


class ResearchContext(BaseModel):
    """Text excerpts, never server paths or privileged instructions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    background: StrictStr = ""
    baseline_code: StrictStr = ""
    data_description: StrictStr = ""
    analysis_results: StrictStr = ""
    metric_definition: StrictStr = ""
    literature: StrictStr = ""

    def supplied(self) -> dict[str, str]:
        return {key: value for key, value in self.model_dump().items() if value.strip()}


def archive_research_context(run: RunHandle, context: ResearchContext) -> str | None:
    """Archive exact supplied text and return the receipt bound into run options."""
    supplied = context.supplied()
    if not supplied:
        return None
    target = run.subdir("input") / "research_context.v1.json"
    document = {"schema_id": "research_context.v1", "project": run.project,
                "run_id": run.run_id, "context": supplied}
    if target.exists():
        raise ValueError("research context already archived; create a new run to change its inputs")
    atomic_json(target, document)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def load_research_context(run: RunHandle, extra: dict[str, Any]) -> dict[str, str]:
    """Fail on lost/corrupt inputs instead of silently researching without them."""
    target = run.subdir("input") / "research_context.v1.json"
    expected = extra.get("research_context_sha256")
    if expected is None and not target.exists():
        return {}  # Historical runs did not carry caller-supplied research text.
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("research context has no valid input receipt")
    if not target.resolve().is_relative_to(run.root.resolve()):
        raise ValueError("research context must stay inside its run")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ValueError("archived research context is missing or unreadable") from exc
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("archived research context checksum mismatch")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("archived research context is not valid JSON") from exc
    if (not isinstance(document, dict) or document.get("schema_id") != "research_context.v1"
            or document.get("project") != run.project or document.get("run_id") != run.run_id):
        raise ValueError("archived research context belongs to a different run or schema")
    return ResearchContext.model_validate(document.get("context")).supplied()
