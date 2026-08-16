"""Idea-local contracts for the Co-Scientist deep discovery workflow.

The durable scientific records themselves come from ``harness.discovery``.
This module only defines orchestration state owned by the Idea Agent.  Keeping
the two layers separate lets V3.0 Core validate records without knowing how an
Idea Agent chooses to generate them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.harness.discovery import (
    HypothesisRecord,
    MetaReviewRecord,
    PairwiseMatchRecord,
    ReflectionRecord,
)


class IdeaMode(str, Enum):
    AUTO = "auto"
    FAST = "fast"
    DEEP = "deep"


class IdeaBudgetProfile(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    THOROUGH = "thorough"


class DeepDiscoveryConfig(BaseModel):
    """Bounded defaults for one Idea deep-discovery run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["idea_deep_discovery_config.v1"] = (
        "idea_deep_discovery_config.v1"
    )
    budget_profile: IdeaBudgetProfile = IdeaBudgetProfile.BALANCED
    initial_hypotheses: int = Field(default=8, ge=3, le=32)
    evolution_rounds: int = Field(default=2, ge=0, le=6)
    children_per_round: int = Field(default=4, ge=1, le=12)
    max_pairwise_matches: int = Field(default=16, ge=1, le=128)
    top_k: int = Field(default=3, ge=1, le=10)
    elo_k: float = Field(default=32.0, gt=0.0, le=128.0)
    proximity_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    seed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def top_k_must_fit_pool(self) -> DeepDiscoveryConfig:
        maximum = self.initial_hypotheses + (
            self.evolution_rounds * self.children_per_round
        )
        if self.top_k > maximum:
            raise ValueError("top_k cannot exceed the maximum hypothesis pool")
        return self

    @classmethod
    def from_request_extra(cls, extra: dict[str, Any]) -> DeepDiscoveryConfig:
        raw_profile = str(extra.get("idea_budget_profile") or "balanced").lower()
        try:
            profile = IdeaBudgetProfile(raw_profile)
        except ValueError as exc:
            raise ValueError(
                "idea_budget_profile must be fast, balanced, or thorough"
            ) from exc

        defaults: dict[IdeaBudgetProfile, dict[str, int]] = {
            IdeaBudgetProfile.FAST: {
                "initial_hypotheses": 4,
                "evolution_rounds": 1,
                "children_per_round": 2,
                "max_pairwise_matches": 6,
            },
            IdeaBudgetProfile.BALANCED: {
                "initial_hypotheses": 8,
                "evolution_rounds": 2,
                "children_per_round": 4,
                "max_pairwise_matches": 16,
            },
            IdeaBudgetProfile.THOROUGH: {
                "initial_hypotheses": 12,
                "evolution_rounds": 3,
                "children_per_round": 6,
                "max_pairwise_matches": 32,
            },
        }
        values: dict[str, Any] = {
            "budget_profile": profile,
            **defaults[profile],
            "top_k": 3,
            "seed": _int_value(extra.get("idea_seed"), default=0),
        }
        overrides = {
            "initial_hypotheses": "idea_initial_hypotheses",
            "evolution_rounds": "idea_evolution_rounds",
            "children_per_round": "idea_children_per_round",
            "max_pairwise_matches": "idea_max_pairwise_matches",
            "top_k": "idea_top_k",
        }
        for field_name, extra_name in overrides.items():
            if extra_name in extra:
                values[field_name] = _int_value(extra[extra_name], default=values[field_name])
        return cls.model_validate(values)


@dataclass(frozen=True)
class DiscoveryContext:
    run_id: str
    project: str
    research_question: str
    evidence_refs: tuple[str, ...]
    constraints: tuple[str, ...]
    context_hash: str


@dataclass(frozen=True)
class HypothesisDraft:
    mechanism: str
    statement: str
    testable_predictions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    constraints: tuple[str, ...]
    uncertainty: str


@dataclass(frozen=True)
class ReflectionDraft:
    correctness: str
    novelty: str
    falsifiability: str
    assumptions: tuple[str, ...]
    failure_modes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class PairwiseDecision:
    outcome: Literal["left", "right", "draw"]
    reason: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class EvolutionRequest:
    round_index: int
    operator: str
    parents: tuple[HypothesisRecord, ...]


@dataclass(frozen=True)
class MetaReviewDraft:
    recurring_errors: tuple[str, ...]
    successful_patterns: tuple[str, ...]
    evidence_gaps: tuple[str, ...]
    unexplored_regions: tuple[str, ...]
    next_round_guidance: tuple[str, ...]


class ProximityEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_id: str
    right_id: str
    similarity: float = Field(ge=0.0, le=1.0)
    exact_duplicate: bool = False


class ProximityGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["proximity_graph.v1"] = "proximity_graph.v1"
    round_index: int = Field(ge=0)
    clusters: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    edges: tuple[ProximityEdge, ...] = ()


class DeepDiscoveryState(BaseModel):
    """Checkpointed state; aggregate lists are replaced, never appended on disk."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["idea_deep_discovery_state.v1"] = (
        "idea_deep_discovery_state.v1"
    )
    run_id: str
    project: str
    input_hash: str
    config: DeepDiscoveryConfig
    backend_mode: str
    status: Literal["running", "waiting_selection", "selected", "failed"] = (
        "running"
    )
    completed_stages: tuple[str, ...] = ()
    hypotheses: tuple[HypothesisRecord, ...] = ()
    reflections: tuple[ReflectionRecord, ...] = ()
    matches: tuple[PairwiseMatchRecord, ...] = ()
    proximity_graphs: tuple[ProximityGraph, ...] = ()
    meta_reviews: tuple[MetaReviewRecord, ...] = ()
    top_hypothesis_ids: tuple[str, ...] = ()
    selected_hypothesis_id: str = ""
    warnings: tuple[str, ...] = ()


class HypothesisPoolView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["hypothesis_pool.v1"] = "hypothesis_pool.v1"
    run_id: str
    project: str
    status: Literal["running", "waiting_selection", "selected", "failed"]
    config: DeepDiscoveryConfig
    hypothesis_count: int = Field(ge=0)
    legal_count: int = Field(ge=0)
    match_count: int = Field(ge=0)
    top_hypothesis_ids: tuple[str, ...]
    selected_hypothesis_id: str = ""
    record_refs: dict[str, str]
    warnings: tuple[str, ...] = ()


class HypothesisSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["hypothesis_selection.v1"] = "hypothesis_selection.v1"
    selection_id: str
    run_id: str
    hypothesis_id: str
    actor: str
    reason: str = ""
    source: Literal["human", "auto", "recommended_for_hitl"]
    proposal_metadata: dict[str, Any]
    proposal_body: str


def stable_hash(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{stable_hash(*parts)[:16]}"


def stable_time(*parts: object) -> datetime:
    """Return a deterministic UTC timestamp for replayable internal records."""
    offset = int(stable_hash(*parts)[:8], 16) % (365 * 24 * 60 * 60)
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset)


def _int_value(value: object, *, default: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"expected integer, got {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected integer, got {value!r}") from exc
