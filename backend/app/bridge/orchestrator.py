"""Orchestrator: api -> bridge entrypoint.

Receives a high-level RunRequest, builds a RunGraph via workflow_service,
walks it node-by-node, and pushes events through the event_bus. Agent
implementations are looked up via agent_registry (reverse dependency).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.bridge.agent_registry import AgentRegistry, get_registry
from app.bridge.workflow_service import EntryPoint, build_pipeline, build_standalone
from app.harness.runtime.event_bus import EventBus, InProcessEventBus
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import (
    NodeState,
    ReviewState,
    RunState,
    is_terminal,
    run_assert_transition,
)
from app.storage import run_state_store
from app.storage.run_state_store import RunStateRecord
from app.storage.run_store import RunHandle, RunStore


# When no real agent is registered for a node we fall back to a stub that
# just transitions running -> waiting_review -> approved -> done. This keeps
# the e2e wiring testable in Phase 2 *before* Phase 3 ships real agents.
NodeRunner = Callable[[RunHandle, str], Awaitable[None]]


@dataclass
class RunRequest:
    task: str
    project: str
    entrypoint: EntryPoint = "pipeline"
    standalone: bool = False
    user_request: str = ""
    auto_approve: bool = False  # Phase 4: when False, wait for HITL approve


@dataclass
class RunSession:
    run: RunHandle
    graph: RunGraph
    request: RunRequest
    bus: EventBus
    runners: dict[str, NodeRunner] = field(default_factory=dict)
    run_status: RunState = RunState.CREATED
    idempotency_key: str | None = None
    attempts: dict[str, int] = field(default_factory=dict)
    feedback_attempts: int = 0
    created_at: str = ""
    # Runtime-only control flags (not persisted; reset on recovery).
    cancel_requested: bool = False
    pause_requested: bool = False


class Orchestrator:
    def __init__(
        self,
        *,
        run_store: RunStore | None = None,
        registry: AgentRegistry | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.run_store = run_store or RunStore()
        self.registry = registry or get_registry()
        self.bus = bus or InProcessEventBus()
        self._sessions: dict[str, RunSession] = {}
        # idempotency_key -> run_id, so a double-submit returns the same run.
        self._idempotency: dict[str, str] = {}

    # --------------------------------------------------------------- create

    def create_session(
        self, request: RunRequest, *, idempotency_key: str | None = None
    ) -> RunSession:
        if idempotency_key and idempotency_key in self._idempotency:
            return self._sessions[self._idempotency[idempotency_key]]
        run = self.run_store.create(
            task=request.task,
            project=request.project,
            entrypoint=request.entrypoint,
            user_request=request.user_request,
        )
        graph = (
            build_standalone(request.entrypoint)
            if request.standalone and request.entrypoint != "pipeline"
            else build_pipeline(request.entrypoint)
        )
        session = RunSession(
            run=run,
            graph=graph,
            request=request,
            bus=self.bus,
            run_status=RunState.CREATED,
            idempotency_key=idempotency_key,
            created_at=run.created_at,
        )
        self._sessions[run.run_id] = session
        if idempotency_key:
            self._idempotency[idempotency_key] = run.run_id
        self._persist(session)
        return session

    def session(self, run_id: str) -> RunSession:
        if run_id not in self._sessions:
            raise KeyError(run_id)
        return self._sessions[run_id]

    # ------------------------------------------------------------ recovery

    def rehydrate_session(
        self, run: RunHandle, record: RunStateRecord
    ) -> RunSession:
        """Rebuild an in-memory session from a persisted run_state.json."""
        graph = RunGraph.from_dict(record.graph)
        request = RunRequest(
            task=run.task,
            project=run.project,
            entrypoint=run.entrypoint,  # type: ignore[arg-type]
        )
        session = RunSession(
            run=run,
            graph=graph,
            request=request,
            bus=self.bus,
            run_status=RunState(record.run_status),
            idempotency_key=record.idempotency_key,
            attempts=dict(record.attempts),
            feedback_attempts=record.feedback_attempts,
            created_at=record.created_at,
        )
        self._sessions[run.run_id] = session
        if record.idempotency_key:
            self._idempotency[record.idempotency_key] = run.run_id
        return session

    def get_or_load_session(self, run_id: str) -> RunSession | None:
        """Return a live session, loading it from disk if not in memory.

        This is what lets the API serve a run's state after a restart — even
        for already-finished runs whose session was never re-driven.
        """
        if run_id in self._sessions:
            return self._sessions[run_id]
        run = self.run_store.get(run_id)
        if run is None:
            return None
        record = run_state_store.read(run.root)
        if record is None:
            return None
        return self.rehydrate_session(run, record)

    def recover_all(self) -> list[str]:
        """Scan runs/ on startup and rehydrate every run with a state file.

        Non-terminal runs are marked WAITING_HUMAN (interrupted) so a human
        can resume/retry them — we don't auto-restart background work. Returns
        the list of run_ids that were interrupted.
        """
        interrupted: list[str] = []
        for run in self.run_store.list():
            record = run_state_store.read(run.root)
            if record is None:
                continue
            session = self.rehydrate_session(run, record)
            from app.harness.runtime.state_machine import run_is_terminal

            if not run_is_terminal(session.run_status):
                # Was mid-flight when the process died.
                session.run_status = RunState.WAITING_HUMAN
                self._log_transition(
                    session, "run", run.run_id,
                    record.run_status, RunState.WAITING_HUMAN.value,
                    "recovery", "interrupted by restart",
                )
                self._persist(session)
                interrupted.append(run.run_id)
        if interrupted:
            logger.warning("recovered {} interrupted run(s): {}", len(interrupted), interrupted)
        return interrupted

    # --------------------------------------------------------- run commands

    def request_pause(self, run_id: str) -> None:
        self.session(run_id).pause_requested = True

    def request_cancel(self, run_id: str) -> None:
        session = self.session(run_id)
        session.cancel_requested = True
        # If the run isn't actively looping, cancel it right now.
        if session.run_status in (RunState.CREATED, RunState.WAITING_HUMAN):
            self._set_run_status(session, RunState.CANCELLED, actor="user")

    def prepare_retry(self, run_id: str) -> None:
        """Reset FAILED nodes to PENDING so a fresh run() reschedules them."""
        session = self.session(run_id)
        session.cancel_requested = False
        session.pause_requested = False
        for key, state in session.graph.all_states().items():
            if state == NodeState.FAILED:
                session.graph.transition(key, NodeState.PENDING)
                self._log_transition(
                    session, "node", key,
                    NodeState.FAILED.value, NodeState.PENDING.value,
                    "user", "retry",
                )
        self._persist(session)

    def delete_run(self, run_id: str) -> bool:
        """Drop the session and delete the run directory from disk."""
        import shutil

        session = self._sessions.pop(run_id, None)
        if session is not None and session.idempotency_key:
            self._idempotency.pop(session.idempotency_key, None)
        run = self.run_store.get(run_id)
        if run is not None:
            shutil.rmtree(run.root, ignore_errors=True)
            return True
        return session is not None

    # ---------------------------------------------------------------- drive

    async def run(self, run_id: str) -> None:
        session = self.session(run_id)
        graph = session.graph
        self._set_run_status(session, RunState.RUNNING)
        await self._publish_state(session, channel="run.lifecycle", payload={
            "event": "run.started",
            "run_id": run_id,
            "project": session.run.project,
            "entrypoint": session.request.entrypoint,
        })

        max_loops = len(graph.nodes) * 4 + 4
        loops = 0
        while not graph.is_complete():
            if session.cancel_requested:
                self._set_run_status(session, RunState.CANCELLED)
                logger.info("run {} cancelled by request", run_id)
                break
            if session.pause_requested:
                # Cooperative pause: stop scheduling new nodes, keep the run
                # alive in WAITING_HUMAN so it can be resumed.
                self._set_run_status(session, RunState.WAITING_HUMAN)
                logger.info("run {} paused by request", run_id)
                return
            loops += 1
            if loops > max_loops:
                logger.error("orchestrator stuck after {} loops", loops)
                break
            ready = graph.ready_nodes()
            if not ready:
                await asyncio.sleep(0)
                # If no node is ready and we're not complete, that means
                # there's a node stuck in WAITING_REVIEW or RUNNING. For V0
                # the dummy/test driver advances those externally.
                non_terminal = [
                    k for k, s in graph.all_states().items()
                    if s in (NodeState.RUNNING, NodeState.WAITING_REVIEW)
                ]
                if not non_terminal:
                    break
                await asyncio.sleep(0.05)
                continue
            for node_key in ready:
                await self._advance(session, node_key)

        self._finalize_run_status(session)
        await self._publish_state(session, channel="run.lifecycle", payload={
            "event": "run.completed",
            "run_id": run_id,
            "run_status": session.run_status.value,
            "states": {k: s.value for k, s in graph.all_states().items()},
        })

    async def _advance(self, session: RunSession, node_key: str) -> None:
        runner = session.runners.get(node_key) or self._default_runner(node_key)
        await self._transition(session, node_key, NodeState.RUNNING)
        try:
            await runner(session.run, node_key)
        except Exception as exc:
            if await self._maybe_self_heal(session, node_key, str(exc)):
                return  # nodes reset to PENDING; the run loop reschedules them
            await self._transition(
                session, node_key, NodeState.FAILED, reason=str(exc)
            )
            await self._publish_state(
                session,
                channel=f"run.{session.run.run_id}.failure",
                payload={"node": node_key, "error": str(exc)},
            )
            return
        # default flow: agent finishes -> waiting_review -> (HITL or auto) -> approved -> done
        if session.graph.state(node_key) == NodeState.RUNNING:
            await self._transition(session, node_key, NodeState.WAITING_REVIEW)
        if session.graph.state(node_key) == NodeState.WAITING_REVIEW:
            await self._await_hitl_or_auto(session, node_key)
        if session.graph.state(node_key) == NodeState.APPROVED:
            # Execution: the human just approved the proposed experiment grid —
            # NOW run the simulations live (propose → confirm → simulate).
            if node_key == "execution" and self.registry.has("execution"):
                await self._run_execution_after_approval(session)
            await self._transition(session, node_key, NodeState.DONE)

    async def _await_hitl_or_auto(self, session: RunSession, node_key: str) -> None:
        """Wait for a human review decision, or auto-approve.

        When ``request.auto_approve`` is True we keep the legacy Phase 2/3
        behaviour (used by smoke tests / no-frontend pipelines). Otherwise we
        register a ReviewSession and block on its approval/rejection event.
        """
        if session.request.auto_approve:
            self._auto_promote(session, node_key)
            await self._transition(session, node_key, NodeState.APPROVED)
            return

        # Real HITL path
        from app.hitl.review_session import ReviewSession, get_registry as get_review_registry
        from app.storage.artifact_store import ArtifactStore

        agent = self.registry.get(node_key) if self.registry.has(node_key) else None
        if agent is None:
            # No registered agent → nothing to review; auto-approve.
            await self._transition(session, node_key, NodeState.APPROVED)
            return

        store = ArtifactStore(session.run)
        # Map agent name -> artifact stem via SCHEMA_TO_AGENT inverse.
        from app.storage.artifact_store import SCHEMA_TO_AGENT

        stem: str | None = None
        agent_dir: str | None = None
        for _, (dir_name, candidate_stem) in SCHEMA_TO_AGENT.items():
            if dir_name == node_key:
                stem = candidate_stem
                agent_dir = dir_name
                break
        if not (stem and agent_dir):
            await self._transition(session, node_key, NodeState.APPROVED)
            return

        latest = store.latest(agent_dir=agent_dir, stem=stem)
        if latest is None:
            # Agent didn't produce any artifact (real error). Halt the chain
            # — silently auto-approving here used to skip HITL entirely.
            logger.warning(
                "no artifact for {}; halting run with FAILED instead of auto-approving",
                node_key,
            )
            await self._transition(session, node_key, NodeState.FAILED)
            return
        if latest.version == "approved":
            # Already promoted (e.g. someone hit /approve while we were
            # transitioning). Nothing to wait for.
            await self._transition(session, node_key, NodeState.APPROVED)
            return

        review = ReviewSession(
            run=session.run, agent_name=node_key, artifact_ref=latest
        )
        await get_review_registry().register(review)

        await self._publish_state(
            session,
            channel=f"run.{session.run.run_id}.hitl",
            payload={
                "event": "hitl.review_required",
                "agent": node_key,
                "artifact_id": latest.path.name,
                "version": latest.version,
            },
        )

        # Wait until either approval or rejection fires. We poll every 100ms
        # so that the test orchestrator can drive the events synchronously.
        while not (review.approval_event.is_set() or review.rejection_event.is_set()):
            await asyncio.sleep(0.05)

        if review.rejection_event.is_set():
            self._log_transition(
                session, "review", node_key,
                ReviewState.PENDING.value, ReviewState.REJECTED.value, "user", "",
            )
            await self._transition(
                session, node_key, NodeState.FAILED, actor="user", reason="rejected"
            )
        else:
            self._log_transition(
                session, "review", node_key,
                ReviewState.PENDING.value, ReviewState.APPROVED.value, "user", "",
            )
            await self._transition(
                session, node_key, NodeState.APPROVED, actor="user", reason="approved"
            )
        await get_review_registry().unregister(session.run.run_id, node_key)

    def _auto_promote(self, session: RunSession, node_key: str) -> None:
        """Copy the latest <stem>.vN.md to <stem>.approved.md.

        Used for the auto_approve path so downstream nodes have an upstream
        handoff to consume.
        """
        if not self.registry.has(node_key):
            return
        from app.storage.artifact_store import ArtifactStore, SCHEMA_TO_AGENT

        store = ArtifactStore(session.run)
        for _, (dir_name, stem) in SCHEMA_TO_AGENT.items():
            if dir_name != node_key:
                continue
            latest = store.latest(agent_dir=dir_name, stem=stem)
            if latest is None or latest.version == "approved":
                continue
            store.approve(latest)
            return

    def _default_runner(self, node_key: str) -> NodeRunner:
        # If an agent is registered, run it via agent_runner; otherwise
        # fall back to a no-op stub so Phase 2 / smoke tests still pass.
        from app.bridge.agent_runner import run_agent_node

        if self.registry.has(node_key):
            bus = self.bus

            async def _real(run: RunHandle, key: str) -> None:
                await run_agent_node(run, key, bus=bus)

            return _real

        async def _stub(run: RunHandle, _key: str) -> None:
            await asyncio.sleep(0)
        return _stub

    # ------------------------------------------------ durable run-state

    def _persist(self, session: RunSession) -> None:
        """Snapshot the run to runs/<id>/run_state.json (survives restart)."""
        record = RunStateRecord(
            run_id=session.run.run_id,
            run_status=session.run_status.value,
            graph=session.graph.to_dict(),
            idempotency_key=session.idempotency_key,
            attempts=dict(session.attempts),
            feedback_attempts=session.feedback_attempts,
            created_at=session.created_at or session.run.created_at,
        )
        run_state_store.write(session.run.root, record)

    def _log_transition(
        self,
        session: RunSession,
        scope: str,
        key: str,
        frm: str,
        to: str,
        actor: str,
        reason: str,
    ) -> None:
        session.run.write_event(
            "state_transitions",
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "scope": scope,
                "key": key,
                "from": frm,
                "to": to,
                "actor": actor,
                "reason": reason,
            },
        )

    def _set_run_status(
        self, session: RunSession, new_status: RunState, *, actor: str = "system"
    ) -> None:
        prev = session.run_status
        if prev == new_status:
            return
        run_assert_transition(prev, new_status)
        session.run_status = new_status
        self._log_transition(
            session, "run", session.run.run_id, prev.value, new_status.value, actor, ""
        )
        self._persist(session)

    def _finalize_run_status(self, session: RunSession) -> None:
        """Pick the terminal/paused run status after the drive loop exits."""
        if session.run_status in (RunState.CANCELLED,):
            return
        states = session.graph.all_states().values()
        if any(s == NodeState.FAILED for s in states):
            self._set_run_status(session, RunState.FAILED)
        elif all(is_terminal(s) for s in states):
            self._set_run_status(session, RunState.COMPLETED)
        else:
            # Stuck on a node awaiting human action (WAITING_REVIEW).
            self._set_run_status(session, RunState.WAITING_HUMAN)

    # ------------------------------------------------------ self-heal loop

    async def _maybe_self_heal(
        self, session: RunSession, node_key: str, error: str
    ) -> bool:
        """Diagnose a node failure and re-route the run, if budget allows.

        Returns True if the run was re-routed (caller should let the drive loop
        reschedule). Always records the failure to failure_memory first.
        """
        from app.execution.config import get_execution_config
        from app.harness.sedimentation.failure_memory import record_failure

        try:
            record_failure(
                project=session.run.project,
                run_id=session.run.run_id,
                node=node_key,
                error=error,
            )
        except Exception:  # KB must never break a run
            logger.exception("failed to write failure_memory for {}", node_key)

        max_attempts = get_execution_config().feedback_max_attempts
        if session.feedback_attempts >= max_attempts:
            logger.warning(
                "feedback budget ({}) exhausted for run {} — halting",
                max_attempts,
                session.run.run_id,
            )
            return False

        from app.bridge.diagnosis import diagnose

        decision = diagnose(
            session.run,
            failed_node=node_key,
            error=error,
            attempt=session.feedback_attempts + 1,
        )
        if decision.action == "manual":
            return False

        session.feedback_attempts += 1
        self._set_run_status(session, RunState.REPAIRING)
        await self._publish_state(
            session,
            channel=f"run.{session.run.run_id}.failure",
            payload={
                "event": "diagnosis.created",
                "node": node_key,
                "action": decision.action,
                "target": decision.target_node,
                "attempt": session.feedback_attempts,
            },
        )
        self._reroute(session, decision.target_node)
        self._set_run_status(session, RunState.RUNNING)
        logger.info(
            "self-heal: run {} re-routing to '{}' (attempt {}/{})",
            session.run.run_id,
            decision.target_node,
            session.feedback_attempts,
            max_attempts,
        )
        return True

    async def _run_execution_after_approval(self, session: RunSession) -> None:
        from app.bridge.agent_runner import run_execution_batch

        try:
            await run_execution_batch(run=session.run, bus=session.bus)
        except Exception:  # pragma: no cover — batch must not crash the run
            logger.exception(
                "execution batch failed for run {}", session.run.run_id
            )

    def _descendants(self, graph: RunGraph, key: str) -> set[str]:
        seen: set[str] = set()
        stack = list(graph.successors(key))
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(graph.successors(n))
        return seen

    def _reroute(self, session: RunSession, target_node: str) -> None:
        """Reset the target node and everything downstream back to PENDING."""
        graph = session.graph
        targets = {target_node} | self._descendants(graph, target_node)
        for key in sorted(targets):
            state = graph.state(key)
            if state in (NodeState.PENDING, NodeState.SKIPPED):
                continue
            graph.reset_to_pending(key)
            self._log_transition(
                session, "node", key, state.value, NodeState.PENDING.value,
                "diagnosis", "reroute",
            )
        self._persist(session)

    # ----------------------------------------------------- state + emission

    async def _transition(
        self,
        session: RunSession,
        node_key: str,
        new_state: NodeState,
        *,
        actor: str = "system",
        reason: str = "",
    ) -> None:
        prev = session.graph.state(node_key)
        if prev == new_state:
            return
        session.graph.transition(node_key, new_state)
        if new_state == NodeState.RUNNING:
            session.attempts[node_key] = session.attempts.get(node_key, 0) + 1
        self._log_transition(
            session, "node", node_key, prev.value, new_state.value, actor, reason
        )
        self._persist(session)
        payload = {
            "agent": node_key,
            "run_id": session.run.run_id,
            "from_state": prev.value,
            "to_state": new_state.value,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        session.run.write_event("agent_events", payload)
        await self._publish_state(
            session,
            channel=f"run.{session.run.run_id}.agent_state",
            payload=payload,
        )

    async def _publish_state(
        self,
        session: RunSession,
        *,
        channel: str,
        payload: dict[str, Any],
    ) -> None:
        await session.bus.publish(channel, payload)
        session.run.write_event("websocket_events", {"channel": channel, **payload})
