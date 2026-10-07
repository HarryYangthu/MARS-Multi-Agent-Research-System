"""Restore the exact human revision input when resuming its existing invocation."""
from __future__ import annotations

import json

from app.bridge.task_runtime import resumable_task
from app.harness.context.runtime_pack import read_material
from app.storage.run_store import RunHandle


def resume_revision_reason(run: RunHandle, node: str, invocation: str) -> str:
    task = resumable_task(run, node)
    if task.invocation_id != invocation or task.project != run.project:
        raise ValueError("resume revision belongs to another invocation or project")
    if "human_revision_request" not in task.required_context_refs:
        return ""
    path = run.root / "agent_traces" / task.agent / invocation / "checkpoint.json"
    metadata = json.loads(path.read_text())["context_metadata"]
    if metadata.get("task_contract") != task.model_dump():
        raise ValueError("resume revision context no longer matches its bound task")
    references = [ref for ref, item in metadata.get("materials", {}).items()
                  if item.get("source") == "human_revision_request" and item.get("protected")]
    if len(references) != 1:
        raise ValueError("resume revision requires its exact original human feedback material")
    source = read_material(run.root, references[0], 0, 2_000_000)
    prefix = ("[untrusted upstream:human_revision_request]\n"
              "Human reviewer rejected the current draft and requested a revised version. Feedback: ")
    if source["truncated"] or not source["content"].startswith(prefix):
        raise ValueError("resume revision feedback format differs from its original input")
    reason = source["content"][len(prefix):]
    if not reason.strip():
        raise ValueError("resume revision feedback is empty")
    return reason
