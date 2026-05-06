"""Thin wrapper around `ArtifactStore.approve` to centralize the approval
side-effect (audit log + WS event)."""
from __future__ import annotations

from app.harness.runtime.event_bus import EventBus
from app.hitl.review_session import ReviewSession
from app.storage.artifact_store import ArtifactRef


async def approve(
    *, session: ReviewSession, bus: EventBus, actor: str = "user"
) -> ArtifactRef:
    approved = session.approve(actor=actor)
    await bus.publish(
        f"run.{session.run.run_id}.hitl",
        {
            "event": "hitl.approved",
            "agent": session.agent_name,
            "artifact_id": approved.path.name,
            "version": approved.version,
        },
    )
    return approved


async def reject(
    *, session: ReviewSession, bus: EventBus, reason: str = "", actor: str = "user"
) -> None:
    session.reject(reason=reason, actor=actor)
    await bus.publish(
        f"run.{session.run.run_id}.hitl",
        {
            "event": "hitl.rejected",
            "agent": session.agent_name,
            "reason": reason,
        },
    )
