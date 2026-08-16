"""Deterministic Pareto and MAP-Elites archive algorithms."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.harness.discovery.canonical import stable_hash
from app.harness.discovery.models import (
    ArchiveSnapshot,
    CandidateEvaluation,
    ObjectiveDirection,
    ObjectiveSpec,
)


@dataclass(frozen=True)
class ParetoUpdate:
    accepted: bool
    candidate_id: str
    removed_candidate_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class NicheElite:
    niche: str
    candidate_id: str
    quality: float


def evaluation_errors(
    evaluation: CandidateEvaluation,
    objectives: tuple[ObjectiveSpec, ...],
) -> tuple[str, ...]:
    errors: list[str] = []
    if not evaluation.hard_constraints_passed:
        errors.append("hard constraints did not pass")
    for objective in objectives:
        metric = evaluation.canonical_metrics.get(objective.name)
        if metric is None:
            errors.append(f"missing objective '{objective.name}'")
            continue
        if not math.isfinite(metric.value):
            errors.append(f"objective '{objective.name}' is not finite")
        if metric.direction != objective.direction:
            errors.append(f"objective '{objective.name}' direction mismatch")
    return tuple(errors)


def dominates(
    left: CandidateEvaluation,
    right: CandidateEvaluation,
    objectives: tuple[ObjectiveSpec, ...],
) -> bool:
    """Return true when left is no worse everywhere and better somewhere."""
    if evaluation_errors(left, objectives) or evaluation_errors(right, objectives):
        raise ValueError("dominance requires valid evaluations")
    strictly_better = False
    for objective in objectives:
        left_value = left.canonical_metrics[objective.name].value
        right_value = right.canonical_metrics[objective.name].value
        if objective.direction == ObjectiveDirection.MINIMIZE:
            if left_value > right_value:
                return False
            strictly_better = strictly_better or left_value < right_value
        else:
            if left_value < right_value:
                return False
            strictly_better = strictly_better or left_value > right_value
    return strictly_better


class ParetoArchive:
    def __init__(self, objectives: tuple[ObjectiveSpec, ...]) -> None:
        if not objectives:
            raise ValueError("at least one objective is required")
        names = [objective.name for objective in objectives]
        if len(names) != len(set(names)):
            raise ValueError("objective names must be unique")
        self.objectives = objectives
        self._evaluations: dict[str, CandidateEvaluation] = {}

    def add(self, evaluation: CandidateEvaluation) -> ParetoUpdate:
        errors = evaluation_errors(evaluation, self.objectives)
        if errors:
            return ParetoUpdate(False, evaluation.candidate_id, reasons=errors)
        before = set(self.candidate_ids())
        self._evaluations[evaluation.candidate_id] = evaluation
        after = set(self.candidate_ids())
        return ParetoUpdate(
            evaluation.candidate_id in after,
            evaluation.candidate_id,
            removed_candidate_ids=tuple(sorted(before - after)),
            reasons=() if evaluation.candidate_id in after else ("candidate is dominated",),
        )

    def candidate_ids(self) -> tuple[str, ...]:
        candidate_ids = sorted(self._evaluations)
        front = [
            candidate_id
            for candidate_id in candidate_ids
            if not any(
                other_id != candidate_id
                and dominates(
                    self._evaluations[other_id],
                    self._evaluations[candidate_id],
                    self.objectives,
                )
                for other_id in candidate_ids
            )
        ]
        return tuple(front)

    def evaluations(self) -> tuple[CandidateEvaluation, ...]:
        return tuple(self._evaluations[candidate_id] for candidate_id in self.candidate_ids())

    def snapshot(
        self,
        *,
        run_id: str,
        iteration: int,
        niche_elites: dict[str, str] | None = None,
        negative_candidate_ids: tuple[str, ...] = (),
        quarantined_candidate_ids: tuple[str, ...] = (),
        lineage_refs: tuple[str, ...] = (),
        budget_snapshot: dict[str, float] | None = None,
        stop_reason: str = "",
    ) -> ArchiveSnapshot:
        content: dict[str, Any] = {
            "run_id": run_id,
            "iteration": iteration,
            "pareto_candidate_ids": self.candidate_ids(),
            "niche_elites": dict(sorted((niche_elites or {}).items())),
            "negative_candidate_ids": sorted(set(negative_candidate_ids)),
            "quarantined_candidate_ids": sorted(set(quarantined_candidate_ids)),
            "lineage_refs": sorted(set(lineage_refs)),
            "budget_snapshot": dict(sorted((budget_snapshot or {}).items())),
            "stop_reason": stop_reason,
        }
        snapshot_hash = stable_hash(content)
        return ArchiveSnapshot(
            snapshot_id=f"archive_{snapshot_hash.removeprefix('sha256:')[:24]}",
            run_id=run_id,
            iteration=iteration,
            pareto_candidate_ids=self.candidate_ids(),
            niche_elites=content["niche_elites"],
            negative_candidate_ids=tuple(content["negative_candidate_ids"]),
            quarantined_candidate_ids=tuple(content["quarantined_candidate_ids"]),
            lineage_refs=tuple(content["lineage_refs"]),
            budget_snapshot=content["budget_snapshot"],
            stop_reason=stop_reason,
            snapshot_hash=snapshot_hash,
        )


class MapElitesArchive:
    """Keep one deterministic scalar-quality elite per caller-defined niche."""

    def __init__(self, *, maximize: bool = True) -> None:
        self.maximize = maximize
        self._elites: dict[str, NicheElite] = {}

    def add(self, *, niche: str, candidate_id: str, quality: float) -> bool:
        if not niche or not candidate_id:
            raise ValueError("niche and candidate_id are required")
        if not math.isfinite(quality):
            return False
        proposed = NicheElite(niche=niche, candidate_id=candidate_id, quality=quality)
        current = self._elites.get(niche)
        if current is None or self._preferred(proposed, current):
            self._elites[niche] = proposed
            return True
        return False

    def elites(self) -> tuple[NicheElite, ...]:
        return tuple(self._elites[niche] for niche in sorted(self._elites))

    def candidate_ids(self) -> dict[str, str]:
        return {elite.niche: elite.candidate_id for elite in self.elites()}

    def _preferred(self, proposed: NicheElite, current: NicheElite) -> bool:
        if proposed.quality == current.quality:
            return proposed.candidate_id < current.candidate_id
        if self.maximize:
            return proposed.quality > current.quality
        return proposed.quality < current.quality
