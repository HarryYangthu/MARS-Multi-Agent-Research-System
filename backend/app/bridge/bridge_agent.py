"""BridgeAgent for V2 diagnosis and feedback-loop routing.

It is deliberately not registered as a normal Agent. The bridge uses it as a
product-level controller that combines hard rules with model-ready natural
language reasoning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.bridge.diagnostics import (
    DiagnosisAnalysis,
    DiagnosticsConfig,
    analyze_run,
    load_diagnostics_config,
)
from app.harness.schema.frontmatter_parser import dumps as fm_dumps, parse as parse_fm
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle


@dataclass(frozen=True)
class BridgeDecision:
    passed: bool
    should_continue: bool
    recommended_target: str
    recommended_action: str
    budget_status: str
    next_attempt: int


class BridgeAgent:
    name = "bridge"
    output_schema = "diagnosis.v1"

    def diagnose(self, *, run: RunHandle, attempt: int) -> BridgeDecision:
        config = load_diagnostics_config(run.project)
        analysis = analyze_run(run, config)
        decision = self._decide(config=config, analysis=analysis, attempt=attempt)
        self._write_diagnosis(
            run=run,
            attempt=attempt,
            config=config,
            analysis=analysis,
            decision=decision,
        )
        return decision

    def decide_from_artifact(self, *, run: RunHandle, version: str) -> BridgeDecision:
        path = run.subdir("diagnosis") / f"diagnosis.{version}.md"
        parsed = parse_fm(path.read_text(encoding="utf-8"))
        metadata = parsed.metadata
        attempt = int(metadata.get("attempt", 1) or 1)
        passed = bool(metadata.get("passed", False))
        budget_status = str(metadata.get("budget_status", "not_applicable"))
        target = str(metadata.get("recommended_target", "none"))
        return BridgeDecision(
            passed=passed,
            should_continue=not passed and budget_status == "within_budget",
            recommended_target=target,
            recommended_action=str(metadata.get("recommended_action", "")),
            budget_status=budget_status,
            next_attempt=attempt + 1,
        )

    def _decide(
        self,
        *,
        config: DiagnosticsConfig,
        analysis: DiagnosisAnalysis,
        attempt: int,
    ) -> BridgeDecision:
        if analysis.passed:
            return BridgeDecision(
                passed=True,
                should_continue=False,
                recommended_target="writing",
                recommended_action="Proceed to Writing; configured metrics passed.",
                budget_status="not_applicable",
                next_attempt=attempt,
            )

        if attempt >= config.max_iterations:
            return BridgeDecision(
                passed=False,
                should_continue=False,
                recommended_target="writing",
                recommended_action="Stop feedback loop and write a failure analysis report; iteration budget is exhausted.",
                budget_status="exhausted",
                next_attempt=attempt,
            )

        target = self._select_target(config=config, analysis=analysis)
        return BridgeDecision(
            passed=False,
            should_continue=True,
            recommended_target=target,
            recommended_action=self._action_for_target(target),
            budget_status="within_budget",
            next_attempt=attempt + 1,
        )

    def _select_target(
        self,
        *,
        config: DiagnosticsConfig,
        analysis: DiagnosisAnalysis,
    ) -> str:
        allowed = set(config.allowed_targets)
        if "experiment" in allowed:
            for cause in analysis.suspected_causes:
                if cause.kind == "config_sanity" and cause.severity == "high":
                    return "experiment"
        if "coding" in allowed:
            for cause in analysis.suspected_causes:
                if cause.kind == "code_change_risk" and cause.severity == "high":
                    return "coding"
        default = config.default_target
        if default in allowed:
            return default
        return next(iter(allowed), "none")

    def _action_for_target(self, target: str) -> str:
        if target == "experiment":
            return "Revise the experiment plan, then rerun execution with the approved code spec."
        if target == "coding":
            return "Generate a focused code_spec revision and patch diff for human review before rerunning execution."
        return "No supported feedback target is available."

    def _write_diagnosis(
        self,
        *,
        run: RunHandle,
        attempt: int,
        config: DiagnosticsConfig,
        analysis: DiagnosisAnalysis,
        decision: BridgeDecision,
    ) -> None:
        metadata: dict[str, Any] = {
            "schema": self.output_schema,
            "project": run.project,
            "agent": self.name,
            "run_id": run.run_id,
            "attempt": attempt,
            "passed": decision.passed,
            "failed_metrics": [f.to_metadata() for f in analysis.failed_metrics],
            "suspected_causes": [c.to_metadata() for c in analysis.suspected_causes],
            "recommended_target": decision.recommended_target,
            "recommended_action": decision.recommended_action,
            "evidence_refs": list(analysis.evidence_refs),
            "budget_status": decision.budget_status,
            "created": datetime.now(tz=timezone.utc).isoformat(),
            "max_iterations": config.max_iterations,
        }
        body = self._render_body(analysis=analysis, decision=decision, attempt=attempt)
        ArtifactStore(run).write(
            text=fm_dumps(metadata, body),
            agent_dir="diagnosis",
            stem="diagnosis",
            expected_schema=self.output_schema,
        )

    def _render_body(
        self,
        *,
        analysis: DiagnosisAnalysis,
        decision: BridgeDecision,
        attempt: int,
    ) -> str:
        lines = [
            f"# Diagnosis attempt {attempt}",
            "",
            f"Passed: `{str(decision.passed).lower()}`.",
            f"Recommended target: `{decision.recommended_target}`.",
            f"Budget status: `{decision.budget_status}`.",
            "",
            "## Evidence",
        ]
        if analysis.failed_metrics:
            for failure in analysis.failed_metrics:
                lines.append(
                    f"- `{failure.metric}` observed `{failure.observed:.6g}` vs target `{failure.target:.6g}` ({failure.direction})."
                )
        else:
            lines.append("- All configured metrics passed.")
        lines.extend(["", "## Suspected Causes"])
        if analysis.suspected_causes:
            for cause in analysis.suspected_causes:
                lines.append(f"- `{cause.kind}` ({cause.severity}): {cause.summary}")
        else:
            lines.append("- No blocking cause detected.")
        lines.extend(["", "## Recommended Action", decision.recommended_action])
        return "\n".join(lines) + "\n"
