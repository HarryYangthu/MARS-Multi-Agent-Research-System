"""Caller-owned research material, preserved across agents and run recovery."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.bridge.idea_input_context import validate_idea_context, validate_idea_extra
from app.harness.agent_loop.trace import atomic_json
from app.storage.run_store import RunHandle


def archive_research_context(run: RunHandle, context: dict[str, str]) -> str | None:
    """Archive exact supplied text and return the receipt bound into run options."""
    supplied = validate_idea_context(context)
    if not supplied:
        return None
    target = run.subdir("input") / "research_context.v1.json"
    document = {"schema_id": "research_context.v1", "project": run.project,
                "run_id": run.run_id, "context": supplied}
    if target.exists():
        raise ValueError("research context already archived; create a new run to change its inputs")
    atomic_json(target, document)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def load_research_context(run: RunHandle, extra: dict[str, Any], *, allow_legacy: bool = True) -> dict[str, str]:
    """Fail on lost/corrupt inputs instead of silently researching without them."""
    target = run.subdir("input") / "research_context.v1.json"
    expected = extra.get("research_context_sha256")
    if expected is None and not target.exists():
        # Historical unreceipted inputs retain their Idea-only contract.
        return validate_idea_extra(extra) if allow_legacy else {}
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
    context = validate_idea_context(document.get("context"))
    if context != validate_idea_extra(extra):
        raise ValueError("research context differs between run options and archived input")
    return context
