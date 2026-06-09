"""Failure sedimentation → the ``failure_memory`` KB zone.

Unlike the per-agent success extractors (which run on agent completion), this
is called from the orchestrator when a node fails, so the self-heal loop and
future runs can retrieve "why did this fail last time".
"""
from __future__ import annotations

from app.harness.kb.memory_writer import write_to_zone
from app.harness.sedimentation.asset_metadata import make as make_metadata

ZONE = "failure_memory"


def record_failure(
    *,
    project: str,
    run_id: str,
    node: str,
    error: str,
    root_cause: str = "",
) -> int:
    text = f"Node '{node}' failed.\nError: {error}\n"
    if root_cause:
        text += f"Root cause: {root_cause}\n"
    meta = make_metadata(
        project=project,
        agent=node,
        run_id=run_id,
        schema="diagnosis.v1",
        extra={"kind": "failure", "failed_node": node},
    )
    return write_to_zone(zone=ZONE, text=text, metadata=meta)
