"""Constructs the V2 linear pipeline RunGraph.

★ Critical CLAUDE.md hard constraint #10: the linear topology
``Idea → Experiment → Coding → Execution → Writing`` must NOT be hard-coded
inside ``harness/runtime/run_graph.py``. It lives here, in the product
orchestration layer, where it's free to evolve project-by-project.
"""
from __future__ import annotations

from typing import Literal
import yaml

from app.harness.runtime.run_graph import RunGraph
from app.settings import repo_root
from app.bridge.node_key import parse_node_key

LINEAR_STAGES: tuple[str, ...] = (
    "idea",
    "experiment",
    "coding",
    "execution",
    "writing",
)

EntryPoint = Literal[
    "pipeline",  # full linear chain
    "idea",
    "experiment",
    "coding",
    "execution",
    "writing",
]


def scheduler_parallelism() -> int:
    raw = yaml.safe_load((repo_root() / "configs/workflow.yaml").read_text()) or {}
    value = raw.get("workflow", {}).get("scheduler", {}).get("max_parallel_nodes", 2)
    if type(value) is not int or not 1 <= value <= 32:
        raise ValueError("workflow.scheduler.max_parallel_nodes must be an integer in [1,32]")
    return value


def ready_batch(graph: RunGraph, limit: int) -> list[str]:
    """Admit independent nodes while serializing shared stage and workspace writes."""
    if limit < 1:
        raise ValueError("parallel limit must be positive")
    selected: list[str] = []
    held: set[str] = set()
    for key in graph.ready_nodes():
        stage = parse_node_key(key).stage
        resources = {"artifact:" + stage}
        if stage in {"coding", "execution"}:
            resources.add("project_workspace")
        declared = graph.metadata(key).get("exclusive_resources", [])
        if not isinstance(declared, list) or any(not isinstance(item, str) for item in declared):
            raise ValueError("exclusive_resources must be a list of resource names")
        resources.update(declared)
        if resources & held:
            continue
        selected.append(key)
        held.update(resources)
        if len(selected) == limit:
            break
    return selected


def build_pipeline(entrypoint: EntryPoint = "pipeline") -> RunGraph:
    """Build the linear DAG and pre-skip any stages preceding ``entrypoint``.

    ``entrypoint='pipeline'`` keeps every node pending. Any single-Agent name
    keeps that node and skips all upstream stages.
    """
    g = RunGraph()
    for stage in LINEAR_STAGES:
        g.add_node(stage, kind="agent", metadata={"stage": stage, "attempt": 1})
    for src, dst in zip(LINEAR_STAGES, LINEAR_STAGES[1:], strict=False):
        g.add_edge(src, dst)

    if entrypoint == "pipeline":
        g.set_entrypoint(LINEAR_STAGES[0])
        return g

    if entrypoint not in LINEAR_STAGES:
        raise ValueError(f"unknown entrypoint '{entrypoint}'")

    g.set_entrypoint(entrypoint)
    # Skip everything before the entrypoint.
    seen_entry = False
    for stage in LINEAR_STAGES:
        if stage == entrypoint:
            seen_entry = True
            continue
        if not seen_entry:
            g.skip(stage)

    return g


def build_standalone(agent_name: str) -> RunGraph:
    """Single-node RunGraph for the Standalone product mode."""
    if agent_name not in LINEAR_STAGES:
        raise ValueError(f"unknown agent '{agent_name}'")
    g = RunGraph()
    g.add_node(
        agent_name,
        kind="agent",
        metadata={"standalone": True, "stage": agent_name, "attempt": 1},
    )
    g.set_entrypoint(agent_name)
    return g
