"""Shared evaluation data model.

The evaluation layer is deliberately artifact-oriented: evaluators inspect
markdown, metadata, events, metrics, and project config. They do not import
concrete Agent classes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.harness.schema.frontmatter_parser import dumps as fm_dumps

EvaluationScope = Literal["artifact", "run", "benchmark", "model_backend"]
EvaluationDecision = Literal["pass", "warn", "revise", "block", "fail"]
EvaluationSeverity = Literal["info", "low", "medium", "high", "blocker"]


@dataclass(frozen=True)
class EvaluationTarget:
    project: str
    scope: EvaluationScope
    target_ref: str
    text: str
    expected_schema: str | None = None
    target_schema: str | None = None


@dataclass(frozen=True)
class EvaluationFinding:
    id: str
    severity: EvaluationSeverity
    category: str
    message: str
    evidence_refs: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class EvaluationReport:
    project: str
    scope: EvaluationScope
    target_ref: str
    evaluator: str
    evaluator_version: int
    decision: EvaluationDecision
    blocking: bool
    target_schema: str | None = None
    overall_score: float | None = None
    scores: dict[str, float] = field(default_factory=dict)
    findings: tuple[EvaluationFinding, ...] = ()
    recommended_actions: tuple[str, ...] = ()
    created: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )

    def to_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "schema": "evaluation_report.v1",
            "project": self.project,
            "scope": self.scope,
            "target_ref": self.target_ref,
            "target_schema": self.target_schema,
            "evaluator": self.evaluator,
            "evaluator_version": self.evaluator_version,
            "decision": self.decision,
            "overall_score": self.overall_score,
            "blocking": self.blocking,
            "scores": dict(self.scores),
            "findings": [f.to_metadata() for f in self.findings],
            "recommended_actions": list(self.recommended_actions),
            "created": self.created,
        }
        return metadata

    def to_markdown(self) -> str:
        return fm_dumps(self.to_metadata(), self.render_body())

    def render_body(self) -> str:
        lines = [
            f"# Evaluation: {self.evaluator}",
            "",
            f"Target: `{self.target_ref}`",
            f"Decision: `{self.decision}`",
        ]
        if self.overall_score is not None:
            lines.append(f"Score: `{self.overall_score:.3f}`")
        lines.extend(["", "## Findings"])
        if not self.findings:
            lines.append("- No findings.")
        else:
            for finding in self.findings:
                refs = ", ".join(f"`{ref}`" for ref in finding.evidence_refs)
                lines.append(
                    f"- `{finding.severity}` `{finding.category}`: "
                    f"{finding.message} Evidence: {refs}"
                )
        if self.recommended_actions:
            lines.extend(["", "## Recommended Actions"])
            for action in self.recommended_actions:
                lines.append(f"- {action}")
        return "\n".join(lines) + "\n"
