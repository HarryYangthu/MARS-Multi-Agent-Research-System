"""Per-agent memory selection policy.

The policy is deliberately small and deterministic. It translates an Agent
identity and purpose into KB zones/types that are allowed to enter Context V2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.harness.kb.stores import MAIN_ZONES

MemoryTypeName = Literal["semantic", "episodic", "procedural"]


@dataclass(frozen=True)
class MemorySelectionPolicy:
    zones: tuple[str, ...]
    memory_types: tuple[MemoryTypeName, ...]
    top_k: int
    min_score: float


_DEFAULT = MemorySelectionPolicy(
    zones=MAIN_ZONES,
    memory_types=("semantic", "episodic", "procedural"),
    top_k=5,
    min_score=0.25,
)

_AGENT_POLICIES: dict[str, MemorySelectionPolicy] = {
    "commander": MemorySelectionPolicy(
        zones=("methodology", "run_archive"),
        memory_types=("episodic", "procedural"),
        top_k=6,
        min_score=0.25,
    ),
    "idea": MemorySelectionPolicy(
        zones=("literature", "methodology", "run_archive"),
        memory_types=("semantic", "episodic", "procedural"),
        top_k=6,
        min_score=0.25,
    ),
    "experiment": MemorySelectionPolicy(
        zones=("methodology", "run_archive"),
        memory_types=("episodic", "procedural"),
        top_k=5,
        min_score=0.25,
    ),
    "coding": MemorySelectionPolicy(
        zones=("code_assets", "methodology", "run_archive"),
        memory_types=("episodic", "procedural"),
        top_k=5,
        min_score=0.25,
    ),
    "execution": MemorySelectionPolicy(
        zones=("run_archive", "methodology"),
        memory_types=("episodic", "procedural"),
        top_k=5,
        min_score=0.25,
    ),
    "writing": MemorySelectionPolicy(
        zones=("methodology", "run_archive", "literature"),
        memory_types=("semantic", "episodic", "procedural"),
        top_k=5,
        min_score=0.25,
    ),
}


def policy_for_agent(agent: str, *, purpose: str = "draft") -> MemorySelectionPolicy:
    del purpose
    return _AGENT_POLICIES.get(agent.strip().lower(), _DEFAULT)
