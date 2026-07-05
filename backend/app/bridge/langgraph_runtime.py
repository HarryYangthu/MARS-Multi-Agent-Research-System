"""LangGraph runtime facade for MARS V2.

The existing V2 orchestrator remains the compatibility driver while V2 moves
workflow topology, checkpoint metadata, and HITL events toward LangGraph.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypedDict

from loguru import logger

from app.harness.runtime.event_bus import EventBus
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import NodeState
from app.settings import get_settings
from app.storage.run_store import RunHandle


class MarsGraphState(TypedDict, total=False):
    run_id: str
    node: str
    states: dict[str, str]
    interrupted_node: str


@dataclass(frozen=True)
class LangGraphCompileResult:
    engine: str
    compiled: Any | None
    nodes: list[str]
    edges: list[dict[str, str]]
    entrypoints: list[str]
    fallback_reason: str = ""


class LangGraphRuntimeFacade:
    def enabled(self) -> bool:
        return get_settings().mars_graph_engine == "langgraph"

    def compile(self, graph: RunGraph) -> LangGraphCompileResult:
        nodes = list(graph.nodes.keys())
        edges = [{"src": edge.src, "dst": edge.dst} for edge in graph.edges]
        if not self.enabled():
            return LangGraphCompileResult(
                engine="legacy",
                compiled=None,
                nodes=nodes,
                edges=edges,
                entrypoints=graph.entrypoints,
                fallback_reason="MARS_GRAPH_ENGINE=legacy",
            )
        try:
            from langgraph.graph import END, START, StateGraph
        except Exception as exc:  # pragma: no cover - optional runtime fallback
            return LangGraphCompileResult(
                engine="langgraph",
                compiled=None,
                nodes=nodes,
                edges=edges,
                entrypoints=graph.entrypoints,
                fallback_reason=f"langgraph unavailable: {exc}",
            )

        builder = StateGraph(MarsGraphState)
        for node_key in nodes:
            builder.add_node(node_key, _node_passthrough(node_key))
        entrypoints = graph.entrypoints or [node for node in nodes if not graph.predecessors(node)]
        for entrypoint in entrypoints:
            builder.add_edge(START, entrypoint)
        for edge in graph.edges:
            builder.add_edge(edge.src, edge.dst)
        terminal_nodes = [node for node in nodes if not graph.successors(node)]
        for node_key in terminal_nodes:
            builder.add_edge(node_key, END)
        compiled = builder.compile()
        return LangGraphCompileResult(
            engine="langgraph",
            compiled=compiled,
            nodes=nodes,
            edges=edges,
            entrypoints=entrypoints,
        )

    def write_manifest(self, *, run: RunHandle, graph: RunGraph) -> dict[str, Any]:
        result = self.compile(graph)
        manifest = {
            "schema": "langgraph_runtime.v2",
            "engine": result.engine,
            "run_id": run.run_id,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "nodes": result.nodes,
            "edges": result.edges,
            "entrypoints": result.entrypoints,
            "checkpoint_namespace": f"mars:v2:{run.run_id}",
            "legacy_state_compat": True,
            "fallback_reason": result.fallback_reason,
        }
        path = run.subdir("context") / "langgraph_runtime.v2.json"
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        if result.fallback_reason:
            logger.warning("LangGraph runtime manifest wrote with fallback: {}", result.fallback_reason)
        return manifest

    async def emit_transition(
        self,
        *,
        run: RunHandle,
        bus: EventBus,
        node_key: str,
        from_state: NodeState,
        to_state: NodeState,
    ) -> None:
        if not self.enabled():
            return
        event = _transition_event(to_state)
        if not event:
            return
        payload = {
            "event": event,
            "run_id": run.run_id,
            "node": node_key,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        run.write_event("langgraph_events", payload)
        await bus.publish(f"run.{run.run_id}.langgraph", payload)


def _node_passthrough(node_key: str) -> Any:
    def _run(state: MarsGraphState) -> MarsGraphState:
        states = dict(state.get("states", {}))
        states[node_key] = "visited"
        return {"run_id": state.get("run_id", ""), "node": node_key, "states": states}

    return _run


def _transition_event(state: NodeState) -> str:
    if state == NodeState.RUNNING:
        return "langgraph.node.started"
    if state == NodeState.WAITING_REVIEW:
        return "langgraph.node.interrupted"
    if state == NodeState.APPROVED:
        return "langgraph.node.resumed"
    return ""
