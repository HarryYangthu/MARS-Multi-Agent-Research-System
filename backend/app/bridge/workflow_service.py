"""Constructs the V0 linear pipeline RunGraph.

★ Critical CLAUDE.md hard constraint #10: the linear topology
``Idea → Experiment → Coding → Execution → Writing`` must NOT be hard-coded
inside ``harness/runtime/run_graph.py``. It lives here, in the product
orchestration layer, where it's free to evolve project-by-project.
"""
from __future__ import annotations

from typing import Literal

from app.harness.runtime.run_graph import RunGraph

# The five Agent stages as keys (these strings double as event channel names
# and as agent registry lookup keys).
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


def build_pipeline(entrypoint: EntryPoint = "pipeline") -> RunGraph:
    """Build the linear DAG and pre-skip any stages preceding ``entrypoint``.

    ``entrypoint='pipeline'`` keeps every node pending. Any single-Agent name
    keeps that node and skips all upstream stages.
    """
    g = RunGraph()
    for stage in LINEAR_STAGES:
        g.add_node(stage, kind="agent")
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
    g.add_node(agent_name, kind="agent", metadata={"standalone": True})
    g.set_entrypoint(agent_name)
    return g
