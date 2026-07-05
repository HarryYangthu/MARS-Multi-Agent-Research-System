"""Evaluator registry."""
from __future__ import annotations

from typing import Protocol

from app.harness.evaluation.models import EvaluationReport, EvaluationTarget


class Evaluator(Protocol):
    id: str
    version: int

    def evaluate(self, target: EvaluationTarget) -> EvaluationReport:
        """Evaluate a target and return one normalized report."""


class EvaluationRegistry:
    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, evaluator: Evaluator) -> None:
        self._evaluators[evaluator.id] = evaluator

    def get(self, evaluator_id: str) -> Evaluator:
        return self._evaluators[evaluator_id]

    def all(self) -> tuple[Evaluator, ...]:
        return tuple(self._evaluators.values())
