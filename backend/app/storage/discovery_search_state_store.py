"""Rebuildable adaptive-search state for one Discovery run."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.harness.discovery.models import (
    CandidateEvaluation,
    CandidateRecord,
    FidelityLevel,
    ObjectiveDirection,
    ResearchTaskContract,
)
from app.harness.discovery.sampling import BanditArm, update_arm
from app.storage.discovery_common import (
    DiscoveryPaths,
    atomic_write_json,
    discovery_lock,
    model_payload,
    read_json,
)


class SearchArmState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arm_id: str = Field(min_length=1)
    pulls: int = Field(default=0, ge=0)
    total_reward: float = 0.0


class SearchCandidateState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    primary_value: float
    quality: float
    hard_constraints_passed: bool
    fidelity: FidelityLevel
    evaluation_count: int = Field(ge=1)
    evaluated_iteration: int = Field(ge=0)
    offspring_count: int = Field(default=0, ge=0)


class DiscoverySearchState(BaseModel):
    """Derived state used by parent and bandit selection.

    Candidate/evaluation records remain authoritative. Rebuilding this record
    makes reward updates idempotent after process recovery.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["discovery_search_state.v1"] = "discovery_search_state.v1"
    run_id: str = Field(min_length=1)
    primary_objective: str = Field(min_length=1)
    primary_direction: ObjectiveDirection
    model_arms: tuple[SearchArmState, ...] = ()
    operator_arms: tuple[SearchArmState, ...] = ()
    candidates: tuple[SearchCandidateState, ...] = ()
    observed_evaluation_ids: tuple[str, ...] = ()
    best_candidate_id: str = ""
    best_quality: float | None = None
    valid_candidates: int = Field(default=0, ge=0)
    since_last_improvement: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class DiscoverySearchStateStore:
    """Persist adaptive state derived deterministically from immutable truth."""

    def __init__(self, run_root: Path, *, run_id: str) -> None:
        self.paths = DiscoveryPaths(run_root=run_root, run_id=run_id)
        self.path = self.paths.root / "search" / "state.json"

    def load(self) -> DiscoverySearchState | None:
        if not self.path.exists():
            return None
        return DiscoverySearchState.model_validate(read_json(self.path))

    def rebuild(
        self,
        *,
        contract: ResearchTaskContract,
        candidates: tuple[CandidateRecord, ...],
        evaluations: tuple[CandidateEvaluation, ...],
    ) -> DiscoverySearchState:
        if contract.run_id != self.paths.run_id:
            raise ValueError("search-state contract does not match store run")
        if not contract.objectives:
            raise ValueError("adaptive search requires at least one objective")
        primary = contract.objectives[0]
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        offspring_counts: dict[str, int] = defaultdict(int)
        for record in candidates:
            for parent_id in record.parent_ids:
                offspring_counts[parent_id] += 1

        model_arms: dict[str, BanditArm] = {}
        operator_arms: dict[str, BanditArm] = {}
        evaluations_by_candidate: dict[str, list[CandidateEvaluation]] = defaultdict(list)
        observed_ids: list[str] = []
        best_quality: float | None = None
        best_candidate_id = ""
        valid_candidates = 0
        since_last_improvement = 0
        min_delta = _non_negative_number(
            contract.stop_policy.get("min_improvement_delta"),
            default=0.0,
        )

        ordered = sorted(evaluations, key=lambda item: (item.created_at, item.evaluation_id))
        for evaluation in ordered:
            candidate = by_id.get(evaluation.candidate_id)
            if candidate is None:
                continue
            evaluations_by_candidate[evaluation.candidate_id].append(evaluation)
            metric = evaluation.canonical_metrics.get(primary.name)
            if metric is None or not math.isfinite(metric.value):
                continue
            observed_ids.append(evaluation.evaluation_id)

        candidate_states: list[SearchCandidateState] = []
        outcomes: list[tuple[CandidateRecord, float, bool]] = []
        for candidate_id, candidate in by_id.items():
            candidate_evaluations = evaluations_by_candidate.get(candidate_id, [])
            if not candidate_evaluations:
                continue
            fidelity = max(
                (item.fidelity for item in candidate_evaluations),
                key=_fidelity_rank,
            )
            cohort = [
                item for item in candidate_evaluations if item.fidelity == fidelity
            ]
            values: list[tuple[CandidateEvaluation, float]] = []
            for evaluation in cohort:
                metric = evaluation.canonical_metrics.get(primary.name)
                if metric is None or not math.isfinite(metric.value):
                    values = []
                    break
                values.append((evaluation, metric.value))
            if not values:
                continue
            candidate_state = _candidate_state(
                candidate=candidate,
                values=values,
                direction=primary.direction,
                offspring_count=offspring_counts[candidate_id],
            )
            hard_constraints_passed = all(
                evaluation.hard_constraints_passed for evaluation, _ in values
            )
            outcomes.append(
                (
                    candidate,
                    candidate_state.quality,
                    hard_constraints_passed,
                )
            )
            if hard_constraints_passed:
                candidate_states.append(candidate_state)

        # One proposal contributes exactly one bandit pull and one patience
        # observation.  Repeated seeds and promoted fidelities are evidence for
        # that proposal, not additional search actions.
        for candidate, quality, hard_constraints_passed in sorted(
            outcomes,
            key=lambda item: _candidate_order(item[0]),
        ):
            reward = _objective_reward(
                quality=quality,
                best_quality=best_quality,
                hard_constraints_passed=hard_constraints_passed,
            )
            model_id = candidate.model_name.strip()
            if model_id:
                model_arms[model_id] = update_arm(
                    model_arms.get(model_id, BanditArm(model_id)),
                    reward=reward,
                )
            operator_id = candidate.operator.strip()
            if operator_id:
                operator_arms[operator_id] = update_arm(
                    operator_arms.get(operator_id, BanditArm(operator_id)),
                    reward=reward,
                )
            if not hard_constraints_passed:
                continue
            valid_candidates += 1
            improved = best_quality is None or quality > best_quality + min_delta
            if improved:
                best_quality = quality
                best_candidate_id = candidate.candidate_id
                since_last_improvement = 0
            else:
                since_last_improvement += 1

        state = DiscoverySearchState(
            run_id=contract.run_id,
            primary_objective=primary.name,
            primary_direction=primary.direction,
            model_arms=tuple(
                _arm_state(arm) for _, arm in sorted(model_arms.items())
            ),
            operator_arms=tuple(
                _arm_state(arm) for _, arm in sorted(operator_arms.items())
            ),
            candidates=tuple(
                sorted(candidate_states, key=lambda item: item.candidate_id)
            ),
            observed_evaluation_ids=tuple(observed_ids),
            best_candidate_id=best_candidate_id,
            best_quality=best_quality,
            valid_candidates=valid_candidates,
            since_last_improvement=since_last_improvement,
        )
        with discovery_lock(self.paths):
            atomic_write_json(self.path, model_payload(state))
        return state


def _candidate_state(
    *,
    candidate: CandidateRecord,
    values: list[tuple[CandidateEvaluation, float]],
    direction: ObjectiveDirection,
    offspring_count: int,
) -> SearchCandidateState:
    fidelity = max((item[0].fidelity for item in values), key=_fidelity_rank)
    at_fidelity = [value for evaluation, value in values if evaluation.fidelity == fidelity]
    primary_value = sum(at_fidelity) / len(at_fidelity)
    return SearchCandidateState(
        candidate_id=candidate.candidate_id,
        primary_value=primary_value,
        quality=_quality(primary_value, direction),
        hard_constraints_passed=True,
        fidelity=fidelity,
        evaluation_count=len(at_fidelity),
        evaluated_iteration=candidate.iteration,
        offspring_count=offspring_count,
    )


def _arm_state(arm: BanditArm) -> SearchArmState:
    return SearchArmState(
        arm_id=arm.arm_id,
        pulls=arm.pulls,
        total_reward=arm.total_reward,
    )


def _quality(value: float, direction: ObjectiveDirection) -> float:
    return -value if direction == ObjectiveDirection.MINIMIZE else value


def _objective_reward(
    *,
    quality: float,
    best_quality: float | None,
    hard_constraints_passed: bool,
) -> float:
    if best_quality is None:
        return 0.0 if hard_constraints_passed else -1.0
    delta = quality - best_quality
    if hard_constraints_passed:
        return delta
    return min(delta, -1e-12)


def _fidelity_rank(value: FidelityLevel) -> int:
    return {
        FidelityLevel.F0: 0,
        FidelityLevel.F1: 1,
        FidelityLevel.F2: 2,
        FidelityLevel.F3: 3,
        FidelityLevel.F4: 4,
    }[value]


def _candidate_order(candidate: CandidateRecord) -> tuple[int, int, datetime, str]:
    raw_ordinal = candidate.metadata.get("discovery_ordinal")
    ordinal = (
        raw_ordinal
        if isinstance(raw_ordinal, int) and not isinstance(raw_ordinal, bool)
        else 2**31 - 1
    )
    return candidate.iteration, ordinal, candidate.created_at, candidate.candidate_id


def _non_negative_number(value: object, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("min_improvement_delta must be a non-negative number")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved < 0.0:
        raise ValueError("min_improvement_delta must be a non-negative number")
    return resolved
