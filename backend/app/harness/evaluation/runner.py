"""Evaluation runner."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.harness.evaluation.evaluators import (
    ArtifactQualityEvaluator,
    ProvenanceEvaluator,
    SchemaValidityEvaluator,
)
from app.harness.evaluation.models import EvaluationReport, EvaluationScope, EvaluationTarget
from app.harness.evaluation.registry import Evaluator


def _default_evaluators() -> tuple[Evaluator, ...]:
    return (SchemaValidityEvaluator(), ProvenanceEvaluator(), ArtifactQualityEvaluator())


@dataclass
class EvaluationRunner:
    evaluators: tuple[Evaluator, ...] = field(default_factory=_default_evaluators)

    def evaluate(self, target: EvaluationTarget) -> list[EvaluationReport]:
        return [evaluator.evaluate(target) for evaluator in self.evaluators]

    def evaluate_text(
        self,
        *,
        project: str,
        text: str,
        target_ref: str,
        expected_schema: str | None = None,
        target_schema: str | None = None,
        scope: EvaluationScope = "artifact",
    ) -> list[EvaluationReport]:
        target = EvaluationTarget(
            project=project,
            scope=scope,
            target_ref=target_ref,
            text=text,
            expected_schema=expected_schema,
            target_schema=target_schema,
        )
        return self.evaluate(target)
