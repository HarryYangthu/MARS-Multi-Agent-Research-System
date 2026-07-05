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
from app.bridge.bridge_agent import BridgeAgent, BridgeDecision
from app.bridge.commander_agent import CommanderAgent, FeedbackDecision
from app.bridge.langgraph_runtime import LangGraphRuntimeFacade
from app.bridge.node_key import attempt_key, parse_node_key
from app.bridge.workflow_service import EntryPoint, build_pipeline, build_standalone
from app.harness.observability.tracing import TraceRecorder
from app.harness.runtime.event_bus import EventBus, InProcessEventBus
from app.harness.runtime.readiness import assert_ready_for_run
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import NodeState
from app.settings import get_settings
from app.storage.run_store import RunHandle, RunStore
from app.storage.run_state_store import RunStateStore


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
    data_source: dict[str, Any] | None = None


@dataclass
class RunSession:
    run: RunHandle
    graph: RunGraph
    request: RunRequest
    bus: EventBus
    runners: dict[str, NodeRunner] = field(default_factory=dict)
    waiting_for_feedback: bool = False


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
        self.bridge_agent = BridgeAgent()
        self.commander_agent = CommanderAgent()
        self.langgraph_runtime = LangGraphRuntimeFacade()
        self._sessions: dict[str, RunSession] = {}

    # --------------------------------------------------------------- create

    def create_session(self, request: RunRequest) -> RunSession:
        settings = get_settings()
        if settings.is_production and request.auto_approve:
            raise ValueError("production mode cannot create auto-approved runs")
        assert_ready_for_run(project=request.project)
        run = self.run_store.create(
            task=request.task,
            project=request.project,
            entrypoint=request.entrypoint,
            user_request=request.user_request,
            data_source=request.data_source,
        )
        graph = (
            build_standalone(request.entrypoint)
            if request.standalone and request.entrypoint != "pipeline"
            else build_pipeline(request.entrypoint)
        )
        session = RunSession(run=run, graph=graph, request=request, bus=self.bus)
        self._sessions[run.run_id] = session
        TraceRecorder(run).ensure_manifest()
        if self.langgraph_runtime.enabled():
            self.langgraph_runtime.write_manifest(run=run, graph=graph)
        self._persist_state(session, status="created")
        return session

    def session(self, run_id: str) -> RunSession:
        if run_id not in self._sessions:
            recovered = self._recover_session(run_id)
            if recovered is None:
                raise KeyError(run_id)
            return recovered
        return self._sessions[run_id]

    # ---------------------------------------------------------------- drive

    async def run(self, run_id: str) -> None:
        session = self.session(run_id)
        graph = session.graph
        self._persist_state(session, status="running")
        await self._publish_state(session, channel="run.lifecycle", payload={
            "event": "run.started",
            "run_id": run_id,
            "project": session.run.project,
            "entrypoint": session.request.entrypoint,
        })

        max_loops = len(graph.nodes) * 4 + 4
        loops = 0
        while not graph.is_complete():
            if session.waiting_for_feedback:
                self._persist_state(session, status="waiting_feedback")
                return
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
            loops += 1
            max_loops = max(max_loops, len(graph.nodes) * 4 + 4)
            if loops > max_loops:
                logger.error("orchestrator stuck after {} loops", loops)
                break
            for node_key in ready:
                await self._advance(session, node_key)
                if session.waiting_for_feedback:
                    self._persist_state(session, status="waiting_feedback")
                    return

        await self._write_evaluation_scorecard(session)
        await self._publish_state(session, channel="run.lifecycle", payload={
            "event": "run.completed",
            "run_id": run_id,
            "states": {k: s.value for k, s in graph.all_states().items()},
        })
        self._persist_state(session, status="completed")

    async def _write_evaluation_scorecard(self, session: RunSession) -> None:
        try:
            from app.bridge.evaluation_service import emit_scorecard_event
            from app.harness.evaluation.aggregation import write_scorecard

            path = write_scorecard(
                run_root=session.run.root,
                run_id=session.run.run_id,
                project=session.run.project,
            )
            event_path = path.relative_to(session.run.root).as_posix()
            session.run.write_event(
                "agent_events",
                {
                    "event": "evaluation.scorecard_written",
                    "agent": "evaluation",
                    "run_id": session.run.run_id,
                    "path": event_path,
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                },
            )
            await emit_scorecard_event(
                run=session.run,
                path=event_path,
                bus=session.bus,
            )
        except Exception as exc:  # pragma: no cover - scorecard is non-blocking
            logger.warning(
                "evaluation scorecard write failed: run={} error={}",
                session.run.run_id,
                exc,
            )

    async def _advance(self, session: RunSession, node_key: str) -> None:
        await self._transition(session, node_key, NodeState.RUNNING)
        if not await self._run_node_runner(session, node_key):
            return
        # default flow: agent finishes -> waiting_review -> (HITL or auto) -> approved -> done
        if session.graph.state(node_key) == NodeState.RUNNING:
            await self._transition(session, node_key, NodeState.WAITING_REVIEW)
            self._refresh_idea_acceptance_report(session, node_key)
        if session.graph.state(node_key) == NodeState.WAITING_REVIEW:
            await self._await_hitl_or_auto(session, node_key)
            await self._complete_approved_node(session, node_key)

    def _refresh_idea_acceptance_report(
        self,
        session: RunSession,
        node_key: str,
    ) -> None:
        if parse_node_key(node_key).stage != "idea":
            return
        try:
            if not self.registry.has("idea"):
                return
            agent = self.registry.get("idea")
            write_acceptance_report = getattr(agent, "write_acceptance_report", None)
            if not callable(write_acceptance_report):
                return

            report_path = write_acceptance_report(
                run=session.run,
                node_key=node_key,
            )
            session.run.write_event(
                "agent_events",
                {
                    "event": "idea.acceptance_report_refreshed",
                    "agent": "idea",
                    "node": node_key,
                    "path": report_path.relative_to(session.run.root).as_posix(),
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                },
            )
        except Exception as exc:  # pragma: no cover - report refresh is non-blocking
            logger.warning(
                "idea acceptance report refresh failed: run={} node={} error={}",
                session.run.run_id,
                node_key,
                exc,
            )

    async def _run_node_runner(
        self,
        session: RunSession,
        node_key: str,
        *,
        revision_reason: str = "",
    ) -> bool:
        runner = session.runners.get(node_key) or self._default_runner(node_key)
        node_kind = session.graph.nodes[node_key].kind
        recorder = TraceRecorder(session.run)
        try:
            with recorder.start_span(
                name=f"node:{node_key}",
                kind=node_kind,
                attributes={
                    "run_id": session.run.run_id,
                    "node": node_key,
                    "stage": parse_node_key(node_key).stage,
                    "attempt": parse_node_key(node_key).attempt,
                    "revision": bool(revision_reason),
                },
            ):
                if revision_reason and node_key not in session.runners:
                    from app.bridge.agent_runner import run_agent_node

                    await run_agent_node(
                        session.run,
                        node_key,
                        bus=session.bus,
                        revision_reason=revision_reason,
                    )
                else:
                    await runner(session.run, node_key)
        except Exception as exc:
            await self._transition(session, node_key, NodeState.FAILED)
            await self._publish_state(
                session,
                channel=f"run.{session.run.run_id}.failure",
                payload={"node": node_key, "error": str(exc)},
            )
            return False
        return True

    async def _await_hitl_or_auto(self, session: RunSession, node_key: str) -> None:
        """Wait for a human review decision, or auto-approve.

        When ``request.auto_approve`` is True we keep the legacy Phase 2/3
        behaviour (used by smoke tests / no-frontend pipelines). Otherwise we
        register a ReviewSession and block on its approval/rejection event.
        """
        if session.request.auto_approve:
            if not self._auto_promote(session, node_key):
                await self._transition(session, node_key, NodeState.FAILED)
                return
            await self._transition(session, node_key, NodeState.APPROVED)
            return

        # Real HITL path
        from app.hitl.review_session import ReviewSession, get_registry as get_review_registry
        from app.storage.artifact_store import ArtifactStore

        stage = parse_node_key(node_key).stage
        agent = self.registry.get(stage) if self.registry.has(stage) else None
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
            if dir_name == stage:
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

        from app.bridge.evaluation_service import build_artifact_evaluation_summary

        evaluation_summary = build_artifact_evaluation_summary(
            run=session.run,
            ref=latest,
            node_key=node_key,
        )
        policy = evaluation_summary.get("policy")
        policy_summary = policy if isinstance(policy, dict) else {}

        review = ReviewSession(
            run=session.run, agent_name=stage, artifact_ref=latest
        )
        await get_review_registry().register(review)

        await self._publish_state(
            session,
            channel=f"run.{session.run.run_id}.hitl",
            payload={
                "event": "hitl.review_required",
                "agent": stage,
                "node": node_key,
                "artifact_id": latest.path.name,
                "version": latest.version,
                "evaluation_summary": evaluation_summary,
                "review_priority": str(
                    policy_summary.get("review_priority", "normal")
                ),
                "recommended_action": str(
                    policy_summary.get("action", "review_before_approval")
                ),
            },
        )

        while True:
            while not (
                review.approval_event.is_set()
                or review.rejection_event.is_set()
                or review.regenerate_event.is_set()
            ):
                await asyncio.sleep(0.05)

            if review.regenerate_event.is_set():
                reason = review.revision_reason
                await get_review_registry().unregister(session.run.run_id, stage)
                await self._publish_state(
                    session,
                    channel=f"run.{session.run.run_id}.hitl",
                    payload={
                        "event": "hitl.revision_started",
                        "agent": stage,
                        "node": node_key,
                        "reason": reason,
                    },
                )
                await self._transition(session, node_key, NodeState.RUNNING)
                if not await self._run_node_runner(
                    session,
                    node_key,
                    revision_reason=reason,
                ):
                    return
                if session.graph.state(node_key) == NodeState.RUNNING:
                    await self._transition(
                        session,
                        node_key,
                        NodeState.WAITING_REVIEW,
                    )
                latest = store.latest(agent_dir=agent_dir, stem=stem)
                if latest is None:
                    await self._transition(session, node_key, NodeState.FAILED)
                    return
                review = ReviewSession(
                    run=session.run,
                    agent_name=stage,
                    artifact_ref=latest,
                )
                await get_review_registry().register(review)
                await self._publish_state(
                    session,
                    channel=f"run.{session.run.run_id}.hitl",
                    payload={
                        "event": "hitl.review_required",
                        "agent": stage,
                        "node": node_key,
                        "artifact_id": latest.path.name,
                        "version": latest.version,
                        "revision": True,
                    },
                )
                continue

            current_state = session.graph.state(node_key)
            if review.rejection_event.is_set():
                if current_state == NodeState.WAITING_REVIEW:
                    await self._transition(session, node_key, NodeState.FAILED)
            elif current_state == NodeState.WAITING_REVIEW:
                await self._transition(session, node_key, NodeState.APPROVED)
            await get_review_registry().unregister(session.run.run_id, stage)
            return

    async def _complete_approved_node(
        self,
        session: RunSession,
        node_key: str,
    ) -> None:
        if session.graph.state(node_key) != NodeState.APPROVED:
            return
        if parse_node_key(node_key).stage == "execution":
            await self._transition(session, node_key, NodeState.RUNNING)
            try:
                from app.bridge.agent_runner import _run_execution_batch
                from app.harness.tools.registry import (
                    ToolContext,
                    get_registry as get_tool_registry,
                )

                async def _batch_runner(
                    _args: dict[str, Any],
                    _ctx: ToolContext,
                ) -> dict[str, Any]:
                    await _run_execution_batch(
                        run=session.run,
                        node_key=node_key,
                        bus=session.bus,
                    )
                    summary_path = session.run.subdir("execution") / "batch_summary.json"
                    summary = (
                        summary_path.read_text(encoding="utf-8")
                        if summary_path.exists()
                        else "{}"
                    )
                    return {"summary": summary}

                tool_result = await get_tool_registry().dispatch(
                    "execution.batch_runner",
                    {"node_key": node_key},
                    ToolContext(
                        run_id=session.run.run_id,
                        project=session.run.project,
                        agent="bridge",
                        extra={
                            "run_root": str(session.run.root),
                            "batch_runner": _batch_runner,
                        },
                    ),
                )
                if not tool_result.ok:
                    raise RuntimeError(tool_result.error or "execution batch failed")
            except Exception as exc:
                await self._transition(session, node_key, NodeState.FAILED)
                await self._publish_state(
                    session,
                    channel=f"run.{session.run.run_id}.failure",
                    payload={"node": node_key, "error": str(exc)},
                )
                return
        await self._transition(session, node_key, NodeState.DONE)
        if parse_node_key(node_key).stage == "execution":
            await self._after_execution(session, node_key)

    async def resume_after_artifact_approval(
        self,
        *,
        run_id: str,
        agent: str,
    ) -> dict[str, Any]:
        """Recover orchestration when an approval arrives without ReviewSession.

        ReviewSession is in-memory. After a backend restart the artifact
        approval endpoint can still durably promote ``*.approved.md``, but no
        event exists to wake the original HITL wait. This method advances the
        matching waiting node and starts the downstream scheduler.
        """
        session = self.session(run_id)
        node_key = self._latest_node_for_stage(session, agent)
        if node_key is None:
            return {"ok": False, "error": f"stage {agent} is not in this run"}
        state = session.graph.state(node_key)
        if state == NodeState.WAITING_REVIEW:
            await self._transition(session, node_key, NodeState.APPROVED)
            state = session.graph.state(node_key)
        if state == NodeState.APPROVED:
            await self._complete_approved_node(session, node_key)
            state = session.graph.state(node_key)
        if state not in {NodeState.DONE, NodeState.SKIPPED}:
            return {
                "ok": False,
                "status": "not_resumable",
                "node": node_key,
                "state": state.value,
            }
        if not session.graph.is_complete() and not session.waiting_for_feedback:
            self._persist_state(session, status="running")
            asyncio.create_task(self.run(run_id), name=f"resume_after_approval:{run_id}")
            return {"ok": True, "status": "resumed", "node": node_key}
        status = "waiting_feedback" if session.waiting_for_feedback else "completed"
        self._persist_state(session, status=status)
        return {"ok": True, "status": status, "node": node_key}

    def _auto_promote(self, session: RunSession, node_key: str) -> bool:
        """Copy the latest <stem>.vN.md to <stem>.approved.md.

        Used for the auto_approve path so downstream nodes have an upstream
        handoff to consume.
        """
        stage = parse_node_key(node_key).stage
        if not self.registry.has(stage):
            return True
        from app.harness.sedimentation.hooks import sediment_approved_artifact
        from app.storage.artifact_store import ArtifactStore, SCHEMA_TO_AGENT

        store = ArtifactStore(session.run)
        for _, (dir_name, stem) in SCHEMA_TO_AGENT.items():
            if dir_name != stage:
                continue
            latest = store.latest(agent_dir=dir_name, stem=stem)
            if latest is None or latest.version == "approved":
                continue
            from app.bridge.evaluation_service import build_artifact_evaluation_summary

            evaluation_summary = build_artifact_evaluation_summary(
                run=session.run,
                ref=latest,
                node_key=node_key,
            )
            policy = evaluation_summary.get("policy")
            policy_summary = policy if isinstance(policy, dict) else {}
            if (
                policy_summary.get("auto_approval_enforced") is True
                and policy_summary.get("auto_approval_allowed") is False
            ):
                session.run.write_event(
                    "evaluation_events",
                    {
                        "event": "evaluation.auto_approval_blocked",
                        "agent": stage,
                        "node": node_key,
                        "run_id": session.run.run_id,
                        "artifact_ref": latest.path.relative_to(session.run.root).as_posix(),
                        "policy": policy_summary,
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    },
                )
                return False
            approved = store.approve(latest)
            try:
                sediment_approved_artifact(
                    run=session.run,
                    agent=stage,
                    artifact_ref=approved,
                )
            except Exception as exc:  # pragma: no cover - auto-promote remains durable
                logger.warning(
                    "auto-approved artifact sedimentation failed: run={} node={} artifact={} error={}",
                    session.run.run_id,
                    node_key,
                    approved.path.name,
                    exc,
                )
            if stage == "writing":
                try:
                    from app.reporting import generate_report_bundle

                    generate_report_bundle(session.run, actor="auto_approve")
                except Exception as exc:  # pragma: no cover - report bundle is non-blocking
                    logger.warning(
                        "auto-approved report bundle generation failed: run={} error={}",
                        session.run.run_id,
                        exc,
                    )
            return True
        return True

    def _default_runner(self, node_key: str) -> NodeRunner:
        # If an agent is registered, run it via agent_runner; otherwise
        # fall back to a no-op stub so Phase 2 / smoke tests still pass.
        from app.bridge.agent_runner import run_agent_node

        identity = parse_node_key(node_key)
        if self.registry.has(identity.stage):
            bus = self.bus

            async def _real(run: RunHandle, key: str) -> None:
                await run_agent_node(run, key, bus=bus)

            return _real

        async def _stub(run: RunHandle, _key: str) -> None:
            await asyncio.sleep(0)
        return _stub

    async def _after_execution(self, session: RunSession, node_key: str) -> None:
        """Let the Commander/Bridge evaluate metrics after execution.

        Diagnosis is a run-level failure analysis artifact in V2, not a
        pipeline node. In development auto-approve runs may append the repair
        chain automatically so legacy e2e stays runnable; otherwise the run
        pauses at waiting_feedback until a human starts the feedback loop.
        """
        if not self.registry.has("execution"):
            self._persist_state(session, status="running")
            return
        decision = self.commander_agent.diagnose(
            run=session.run,
            attempt=parse_node_key(node_key).attempt,
        )
        if getattr(decision, "requires_human", False):
            session.waiting_for_feedback = True
            await self._publish_state(
                session,
                channel=f"run.{session.run.run_id}.feedback_loop",
                payload={
                    "event": "feedback_loop.review_required",
                    "run_id": session.run.run_id,
                    "from_node": node_key,
                    "target": decision.recommended_target,
                    "attempt": decision.next_attempt,
                    "recommended_action": decision.recommended_action,
                    "feedback_packet_ref": decision.feedback_packet_ref,
                    "confidence": decision.confidence,
                    "reason": "low_confidence_attribution",
                },
            )
            self._persist_state(session, status="waiting_feedback")
            return
        if not decision.should_continue:
            self._persist_state(session, status="running")
            return
        if not session.request.auto_approve:
            session.waiting_for_feedback = True
            await self._publish_state(
                session,
                channel=f"run.{session.run.run_id}.feedback_loop",
                payload={
                    "event": "feedback_loop.review_required",
                    "run_id": session.run.run_id,
                    "from_node": node_key,
                    "target": decision.recommended_target,
                    "attempt": decision.next_attempt,
                    "recommended_action": decision.recommended_action,
                    "feedback_packet_ref": decision.feedback_packet_ref,
                    "confidence": decision.confidence,
                },
            )
            self._persist_state(session, status="waiting_feedback")
            return
        appended = self._append_feedback_attempt(
            session=session,
            from_node=node_key,
            decision=decision,
        )
        if appended:
            await self._publish_state(
                session,
                channel=f"run.{session.run.run_id}.feedback_loop",
                payload={
                    "event": "feedback_loop.appended",
                    "run_id": session.run.run_id,
                    "from_node": node_key,
                    "target": decision.recommended_target,
                    "attempt": decision.next_attempt,
                    "feedback_packet_ref": decision.feedback_packet_ref,
                    "confidence": decision.confidence,
                },
            )
            self._persist_state(session, status="running")

    def _append_feedback_attempt(
        self,
        *,
        session: RunSession,
        from_node: str,
        decision: BridgeDecision | FeedbackDecision,
    ) -> bool:
        attempt = decision.next_attempt
        target = decision.recommended_target
        if target not in {"coding", "experiment"}:
            return False

        if target == "coding":
            chain = [
                attempt_key("coding", attempt),
                attempt_key("execution", attempt),
            ]
        else:
            chain = [
                attempt_key("experiment", attempt),
                attempt_key("execution", attempt),
            ]
        if any(key in session.graph.nodes for key in chain):
            return False

        for key in chain:
            identity = parse_node_key(key)
            session.graph.add_node(
                key,
                kind="agent",
                metadata={"stage": identity.stage, "attempt": identity.attempt},
            )

        prev = from_node
        for key in chain:
            session.graph.add_edge(prev, key)
            prev = key
        if "writing" in session.graph.nodes:
            session.graph.add_edge(prev, "writing")
        return True

    async def start_feedback_loop(
        self,
        *,
        run_id: str,
        diagnosis_version: str,
    ) -> dict[str, Any]:
        session = self.session(run_id)
        run = session.run
        path = run.subdir("diagnosis") / f"diagnosis.{diagnosis_version}.md"
        if not path.exists():
            return {"ok": False, "error": "diagnosis not found"}
        decision = self.commander_agent.decide_from_artifact(
            run=run,
            version=diagnosis_version,
        )
        if not decision.should_continue:
            return {
                "ok": True,
                "status": "noop",
                "reason": decision.budget_status,
                "target": decision.recommended_target,
            }
        from_node = (
            "execution"
            if decision.next_attempt <= 2
            else f"execution_attempt_{decision.next_attempt - 1}"
        )
        appended = self._append_feedback_attempt(
            session=session,
            from_node=from_node,
            decision=decision,
        )
        if appended:
            session.waiting_for_feedback = False
            self._persist_state(session, status="running")
            asyncio.create_task(self.run(run_id), name=f"feedback_loop:{run_id}")
        return {
            "ok": True,
            "status": "appended" if appended else "already_exists",
            "target": decision.recommended_target,
            "attempt": decision.next_attempt,
            "feedback_packet_ref": getattr(decision, "feedback_packet_ref", ""),
            "confidence": getattr(decision, "confidence", 0.0),
        }

    async def request_artifact_revision(
        self,
        *,
        run_id: str,
        agent: str,
        reason: str,
    ) -> dict[str, Any]:
        session = self.session(run_id)
        node_key = self._latest_node_for_stage(session, agent)
        if node_key is None:
            return {"ok": False, "error": f"stage {agent} is not in this run"}
        state = session.graph.state(node_key)
        if state == NodeState.RUNNING:
            return {"ok": True, "status": "already_running", "node": node_key}
        if state not in {
            NodeState.WAITING_REVIEW,
            NodeState.FAILED,
            NodeState.APPROVED,
        }:
            return {
                "ok": False,
                "error": f"stage {agent} cannot be revised from {state.value}",
                "node": node_key,
            }
        await self._publish_state(
            session,
            channel=f"run.{session.run.run_id}.hitl",
            payload={
                "event": "hitl.revision_requested",
                "agent": agent,
                "node": node_key,
                "reason": reason,
                "fallback": True,
            },
        )
        asyncio.create_task(
            self._run_revision_flow(
                session=session,
                node_key=node_key,
                reason=reason,
            ),
            name=f"artifact_revision:{run_id}:{node_key}",
        )
        return {"ok": True, "status": "revision_started", "node": node_key}

    async def _run_revision_flow(
        self,
        *,
        session: RunSession,
        node_key: str,
        reason: str,
    ) -> None:
        try:
            await self._transition(session, node_key, NodeState.RUNNING)
            if not await self._run_node_runner(
                session,
                node_key,
                revision_reason=reason,
            ):
                return
            if session.graph.state(node_key) == NodeState.RUNNING:
                await self._transition(session, node_key, NodeState.WAITING_REVIEW)
            if session.graph.state(node_key) == NodeState.WAITING_REVIEW:
                await self._await_hitl_or_auto(session, node_key)
        except Exception as exc:  # pragma: no cover - background safety net
            logger.warning(
                "artifact revision flow failed: run={} node={} error={}",
                session.run.run_id,
                node_key,
                exc,
            )

    def _latest_node_for_stage(self, session: RunSession, stage: str) -> str | None:
        candidates = [
            key
            for key in session.graph.nodes
            if parse_node_key(key).stage == stage
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda key: parse_node_key(key).attempt)

    def _recover_session(self, run_id: str) -> RunSession | None:
        run = self.run_store.get(run_id)
        if run is None:
            return None
        snapshot = RunStateStore(run).load()
        if snapshot is None:
            graph = self._infer_readonly_graph_from_artifacts(run)
            request = RunRequest(
                task=run.task,
                project=run.project,
                entrypoint=run.entrypoint,  # type: ignore[arg-type]
                user_request="",
            )
        else:
            graph = snapshot.graph
            request = RunRequest(
                task=str(snapshot.request.get("task", run.task)),
                project=str(snapshot.request.get("project", run.project)),
                entrypoint=str(snapshot.request.get("entrypoint", run.entrypoint)),  # type: ignore[arg-type]
                standalone=bool(snapshot.request.get("standalone", False)),
                user_request=str(snapshot.request.get("user_request", "")),
                auto_approve=bool(snapshot.request.get("auto_approve", False)),
            )
        waiting = snapshot is not None and snapshot.status == "waiting_feedback"
        session = RunSession(
            run=run,
            graph=graph,
            request=request,
            bus=self.bus,
            waiting_for_feedback=waiting,
        )
        self._sessions[run_id] = session
        return session

    def _infer_readonly_graph_from_artifacts(self, run: RunHandle) -> RunGraph:
        from app.harness.runtime.state_machine import NodeState
        from app.storage.artifact_store import SCHEMA_TO_AGENT

        graph = (
            build_pipeline(run.entrypoint)  # type: ignore[arg-type]
            if run.entrypoint != "pipeline"
            else build_pipeline("pipeline")
        )
        stem_by_dir = {
            agent_dir: stem for _sid, (agent_dir, stem) in SCHEMA_TO_AGENT.items()
        }
        for key in graph.nodes:
            stem = stem_by_dir.get(key)
            if stem is None:
                graph.restore_state(key, NodeState.PENDING)
                continue
            directory = run.root / key
            if (directory / f"{stem}.approved.md").exists():
                graph.restore_state(key, NodeState.DONE)
            elif any(directory.glob(f"{stem}.v*.md")):
                graph.restore_state(key, NodeState.WAITING_REVIEW)
            else:
                graph.restore_state(key, NodeState.PENDING)
        return graph

    def _persist_state(self, session: RunSession, *, status: str) -> None:
        RunStateStore(session.run).write(
            graph=session.graph,
            request={
                "task": session.request.task,
                "project": session.request.project,
                "entrypoint": session.request.entrypoint,
                "standalone": session.request.standalone,
                "user_request": session.request.user_request,
                "auto_approve": session.request.auto_approve,
            },
            status=status,
        )

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
        await self.langgraph_runtime.emit_transition(
            run=session.run,
            bus=session.bus,
            node_key=node_key,
            from_state=prev,
            to_state=new_state,
        )
        TraceRecorder(session.run).record_event_ref(
            channel="agent_events",
            event="agent_state",
            payload=payload,
        )
        self._persist_state(session, status="running")
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
        TraceRecorder(session.run).record_event_ref(
            channel=channel,
            event=str(payload.get("event", "")),
            payload=payload,
        )
