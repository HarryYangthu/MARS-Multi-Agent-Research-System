"""Deterministic contract evaluators."""
from __future__ import annotations

from app.harness.evaluation.models import (
    EvaluationFinding,
    EvaluationReport,
    EvaluationTarget,
)
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.harness.schema.validator import validate_document


class SchemaValidityEvaluator:
    id = "contract.schema_validity"
    version = 1

    def evaluate(self, target: EvaluationTarget) -> EvaluationReport:
        result = validate_document(target.text, expected_schema=target.expected_schema)
        target_schema = target.target_schema or target.expected_schema or result.schema_id
        if result.valid:
            return EvaluationReport(
                project=target.project,
                scope=target.scope,
                target_ref=target.target_ref,
                target_schema=target_schema,
                evaluator=self.id,
                evaluator_version=self.version,
                decision="pass",
                blocking=False,
                overall_score=1.0,
                scores={"schema_validity": 1.0},
            )

        findings = tuple(
            EvaluationFinding(
                id=f"schema_{idx:03d}",
                severity="blocker",
                category="schema",
                message=f"{error.path}: {error.message}",
                evidence_refs=(target.target_ref,),
            )
            for idx, error in enumerate(result.errors, start=1)
        )
        return EvaluationReport(
            project=target.project,
            scope=target.scope,
            target_ref=target.target_ref,
            target_schema=target_schema,
            evaluator=self.id,
            evaluator_version=self.version,
            decision="block",
            blocking=True,
            overall_score=0.0,
            scores={"schema_validity": 0.0},
            findings=findings,
            recommended_actions=("Repair frontmatter until JSON Schema validation passes.",),
        )


class ProvenanceEvaluator:
    id = "contract.provenance"
    version = 1

    def evaluate(self, target: EvaluationTarget) -> EvaluationReport:
        findings: list[EvaluationFinding] = []
        try:
            parsed = parse_frontmatter(target.text)
            metadata = parsed.metadata
        except Exception:
            metadata = {}
        for key in ("schema", "project", "agent"):
            if not metadata.get(key):
                findings.append(
                    EvaluationFinding(
                        id=f"provenance_missing_{key}",
                        severity="medium",
                        category="provenance",
                        message=f"Missing frontmatter field `{key}`.",
                        evidence_refs=(target.target_ref,),
                    )
                )
        if not target.target_ref:
            findings.append(
                EvaluationFinding(
                    id="provenance_missing_target_ref",
                    severity="high",
                    category="provenance",
                    message="Missing target_ref/source path.",
                    evidence_refs=(),
                )
            )
        if findings:
            return EvaluationReport(
                project=target.project,
                scope=target.scope,
                target_ref=target.target_ref,
                target_schema=target.target_schema or target.expected_schema,
                evaluator=self.id,
                evaluator_version=self.version,
                decision="warn",
                blocking=False,
                overall_score=0.6,
                scores={"provenance": 0.6},
                findings=tuple(findings),
                recommended_actions=("Add source/project/agent provenance before long-term memory write.",),
            )
        return EvaluationReport(
            project=target.project,
            scope=target.scope,
            target_ref=target.target_ref,
            target_schema=target.target_schema or target.expected_schema,
            evaluator=self.id,
            evaluator_version=self.version,
            decision="pass",
            blocking=False,
            overall_score=1.0,
            scores={"provenance": 1.0},
        )
