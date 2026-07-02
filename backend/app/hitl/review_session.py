"""HITL review sessions.

A session is keyed by (run_id, agent_name). It captures:
- the latest version of the artifact
- audit trail of human actions (comment / edit / approve / reject / regenerate)
- a pair of asyncio events for orchestrator coordination

The orchestrator parks a node in WAITING_REVIEW and awaits the session's
``approval_event``; once the user approves (REST /api/artifacts/.../approve)
the orchestrator advances. ``rejection_event`` fires for explicit reject.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.hitl.audit_log import AuditEntry, append as append_audit
from app.storage.artifact_store import ArtifactRef, ArtifactStore
from app.storage.run_store import RunHandle


@dataclass
class ReviewSession:
    run: RunHandle
    agent_name: str
    artifact_ref: ArtifactRef
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    rejection_event: asyncio.Event = field(default_factory=asyncio.Event)
    regenerate_event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: str | None = None  # "approve" | "reject" | "regenerate"
    revision_reason: str = ""

    @property
    def audit_path(self):  # type: ignore[no-untyped-def]
        return self.run.subdir("hitl") / "review_log.jsonl"

    def comment(self, text: str, *, actor: str = "user") -> None:
        append_audit(
            self.audit_path,
            AuditEntry(
                run_id=self.run.run_id,
                agent=self.agent_name,
                action="comment",
                actor=actor,
                detail={"text": text},
            ),
        )

    def record_edit(self, ref: ArtifactRef, *, actor: str = "user") -> None:
        self.artifact_ref = ref
        append_audit(
            self.audit_path,
            AuditEntry(
                run_id=self.run.run_id,
                agent=self.agent_name,
                action="edit",
                actor=actor,
                detail={"version": ref.version, "path": str(ref.path)},
            ),
        )

    def approve(self, *, actor: str = "user") -> ArtifactRef:
        store = ArtifactStore(self.run)
        approved = store.approve(self.artifact_ref)
        self.decision = "approve"
        append_audit(
            self.audit_path,
            AuditEntry(
                run_id=self.run.run_id,
                agent=self.agent_name,
                action="approve",
                actor=actor,
                detail={"version": self.artifact_ref.version},
            ),
        )
        self.approval_event.set()
        return approved

    def reject(self, *, reason: str = "", actor: str = "user") -> None:
        self.decision = "reject"
        append_audit(
            self.audit_path,
            AuditEntry(
                run_id=self.run.run_id,
                agent=self.agent_name,
                action="reject",
                actor=actor,
                detail={"reason": reason},
            ),
        )
        self.rejection_event.set()

    def request_regenerate(self, *, reason: str = "", actor: str = "user") -> None:
        self.decision = "regenerate"
        self.revision_reason = reason
        append_audit(
            self.audit_path,
            AuditEntry(
                run_id=self.run.run_id,
                agent=self.agent_name,
                action="regenerate",
                actor=actor,
                detail={"reason": reason},
            ),
        )
        self.regenerate_event.set()

    def to_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run.run_id,
            "agent": self.agent_name,
            "artifact_path": str(self.artifact_ref.path),
            "version": self.artifact_ref.version,
            "decision": self.decision,
        }


class ReviewRegistry:
    """Process-wide store of pending sessions, keyed by (run_id, agent)."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], ReviewSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, session: ReviewSession) -> None:
        async with self._lock:
            self._sessions[(session.run.run_id, session.agent_name)] = session

    async def unregister(self, run_id: str, agent: str) -> None:
        async with self._lock:
            self._sessions.pop((run_id, agent), None)

    def get(self, run_id: str, agent: str) -> ReviewSession | None:
        return self._sessions.get((run_id, agent))

    def list(self, run_id: str | None = None) -> list[ReviewSession]:
        if run_id is None:
            return list(self._sessions.values())
        return [s for k, s in self._sessions.items() if k[0] == run_id]


_registry: ReviewRegistry | None = None


def get_registry() -> ReviewRegistry:
    global _registry
    if _registry is None:
        _registry = ReviewRegistry()
    return _registry


def reset_registry_for_tests() -> None:
    global _registry
    _registry = None


__all__ = ["ReviewRegistry", "ReviewSession", "get_registry"]
