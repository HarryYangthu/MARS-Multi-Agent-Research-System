"""Archive cancelled delegation identity without changing its loop checkpoint."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.agents.idea.research_gap import failure_record
from app.harness.agent_loop import AgentLoopPolicy
from app.harness.agent_loop.trace import atomic_json


def archive_research_cancellation(*, root: Path, trace: Path, target: Path, delegation_id: str,
                                  min_sources: int, policy: AgentLoopPolicy, gap: str,
                                  project: str) -> dict[str, Any]:
    destination = target / "failure.json"
    if destination.exists():
        previous = json.loads(destination.read_text())
        if not isinstance(previous, dict) or previous.get("delegation_id") != delegation_id:
            raise ValueError("existing research failure has a different identity")
        return previous
    checkpoint = trace / "checkpoint.json"
    failure: dict[str, Any] = {"usage_complete": False}
    try:
        data = checkpoint.read_bytes()
        state = json.loads(data)
        failure = failure_record(delegation_id=delegation_id, trace_ref=trace.relative_to(root).as_posix(),
            checkpoint=state, min_sources=min_sources, max_tool_steps=policy.max_tool_steps,
            max_model_calls=policy.max_model_calls, gap=gap, project=project)
        counts = state.get("counts", {})
        known_counts = (isinstance(counts, dict) and isinstance(counts.get("model_requests"), int)
                        and isinstance(counts.get("model_responses"), int))
        usage_complete = (state.get("usage_complete") is True and state.get("pending") != "model"
                          and known_counts and counts["model_requests"] == counts["model_responses"])
        events = trace / "events.jsonl"
        if events.is_file():
            try:
                rows = [json.loads(line) for line in events.read_text().splitlines()]
                requests = {row["request"] for row in rows if row.get("kind") == "model_request"}
                responses = {row["request"] for row in rows if row.get("kind") == "model_response"}
                if requests - responses:
                    usage_complete = False
            except (OSError, ValueError, KeyError, TypeError) as exc:
                usage_complete = False
                failure["event_read_error"] = {"type": type(exc).__name__}
        failure.update(checkpoint_status=state["status"], pending=state.get("pending"),
                       usage_complete=usage_complete,
                       checkpoint_sha256=hashlib.sha256(data).hexdigest())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        failure["usage_complete"] = False
        failure["checkpoint_read_error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
    failure.update(schema="research.failure.v1", delegation_id=delegation_id,
                   status="interrupted", failure_type="research_cancelled", automatic_replay=False,
                   checkpoint_available=checkpoint.is_file(), checkpoint_ref=checkpoint.relative_to(root).as_posix(),
                   trace_ref=trace.relative_to(root).as_posix(), usable_as_final_evidence=False, scientific_validated=False)
    atomic_json(destination, failure)
    return failure
