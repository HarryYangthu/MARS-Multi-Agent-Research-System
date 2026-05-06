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
from app.harness.runtime.state_machine import NodeState
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

    # --------------------------------------------------------------- create

    def create_session(self, request: RunRequest) -> RunSession:
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
        session = RunSession(run=run, graph=graph, request=request, bus=self.bus)
        self._sessions[run.run_id] = session
        return session

    def session(self, run_id: str) -> RunSession:
        if run_id not in self._sessions:
            raise KeyError(run_id)
        return self._sessions[run_id]

    # ---------------------------------------------------------------- drive

    async def run(self, run_id: str) -> None:
        session = self.session(run_id)
        graph = session.graph
        await self._publish_state(session, channel="run.lifecycle", payload={
            "event": "run.started",
            "run_id": run_id,
            "project": session.run.project,
            "entrypoint": session.request.entrypoint,
        })

        max_loops = len(graph.nodes) * 4 + 4
        loops = 0
        while not graph.is_complete():
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

        await self._publish_state(session, channel="run.lifecycle", payload={
            "event": "run.completed",
            "run_id": run_id,
            "states": {k: s.value for k, s in graph.all_states().items()},
        })

    async def _advance(self, session: RunSession, node_key: str) -> None:
        runner = session.runners.get(node_key) or self._default_runner(node_key)
        await self._transition(session, node_key, NodeState.RUNNING)
        try:
            await runner(session.run, node_key)
        except Exception as exc:
            await self._transition(session, node_key, NodeState.FAILED)
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
            await self._transition(session, node_key, NodeState.FAILED)
        else:
            await self._transition(session, node_key, NodeState.APPROVED)
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

    # ----------------------------------------------------- state + emission

    async def _transition(
        self,
        session: RunSession,
        node_key: str,
        new_state: NodeState,
    ) -> None:
        prev = session.graph.state(node_key)
        if prev == new_state:
            return
        session.graph.transition(node_key, new_state)
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
