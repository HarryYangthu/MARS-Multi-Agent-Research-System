"""Thin wrapper around `ArtifactStore.approve` to centralize the approval
side-effect (audit log + WS event)."""
from __future__ import annotations

from loguru import logger

from app.harness.runtime.event_bus import EventBus
from app.harness.sedimentation.hooks import sediment_approved_artifact
from app.reporting import generate_report_bundle
from app.hitl.review_session import ReviewSession
from app.storage.artifact_store import ArtifactRef


async def approve(
    *, session: ReviewSession, bus: EventBus, actor: str = "user"
) -> ArtifactRef:
    approved = session.approve(actor=actor)
    try:
        sediment_approved_artifact(
            run=session.run,
            agent=session.agent_name,
            artifact_ref=approved,
        )
    except Exception as exc:  # pragma: no cover - approval must remain durable
        logger.warning(
            "approved artifact sedimentation failed: run={} agent={} artifact={} error={}",
            session.run.run_id,
            session.agent_name,
            approved.path.name,
            exc,
        )
    await bus.publish(
        f"run.{session.run.run_id}.hitl",
        {
            "event": "hitl.approved",
            "agent": session.agent_name,
            "artifact_id": approved.path.name,
            "version": approved.version,
        },
    )
    if session.agent_name == "writing":
        try:
            generate_report_bundle(session.run, actor=actor)
        except Exception as exc:  # pragma: no cover - approval remains durable
            logger.warning(
                "report bundle generation failed after approval: run={} error={}",
                session.run.run_id,
                exc,
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


async def request_revision(
    *, session: ReviewSession, bus: EventBus, reason: str = "", actor: str = "user"
) -> None:
    session.request_regenerate(reason=reason, actor=actor)
    await bus.publish(
        f"run.{session.run.run_id}.hitl",
        {
            "event": "hitl.revision_requested",
            "agent": session.agent_name,
            "reason": reason,
        },
    )
