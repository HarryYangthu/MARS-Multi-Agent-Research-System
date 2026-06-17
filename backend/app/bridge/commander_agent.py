"""CommanderAgent feedback attribution and self-evolution loop.

The conversational Commander remains in ``commander.py``. This module is the
main Agent's deterministic control surface for result diagnosis: observe a run,
attribute failure, write a bounded feedback packet, and record run-local
self-evolution memory.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from app.bridge.diagnostics import (
    DiagnosisAnalysis,
    DiagnosticsConfig,
    load_diagnostics_config,
    analyze_run,
)
from app.harness.context.budget_policy import (
    ContextBudgetPolicy,
    bound_feedback_text,
    load_context_budget_policy,
)
from app.harness.schema.frontmatter_parser import dumps as fm_dumps, parse as parse_fm
from app.harness.schema.validator import validate_document
from app.storage.agent_context_store import record_agent_memory_outcome
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle
from app.storage.self_evolution_store import append_learning_event


TARGET_AGENTS: frozenset[str] = frozenset({"experiment", "coding"})
LOW_CONFIDENCE_THRESHOLD = 0.55


@dataclass(frozen=True)
class FeedbackDecision:
    passed: bool
    should_continue: bool
    recommended_target: str
    recommended_action: str
    budget_status: str
    next_attempt: int
    requires_human: bool = False
    feedback_packet_ref: str = ""
    confidence: float = 0.0


@dataclass(frozen=True)
class RunObservation:
    run: RunHandle
    attempt: int
    config: DiagnosticsConfig
    analysis: DiagnosisAnalysis
    metrics_summary: dict[str, Any]
    curve_summary: dict[str, Any]
    log_summary: dict[str, Any]
    approved_artifact_refs: dict[str, str]
    attempt_history: list[dict[str, Any]]
    latest_diagnosis: dict[str, Any] | None


@dataclass(frozen=True)
class CommanderAttribution:
    passed: bool
    should_continue: bool
    target_agent: str
    confidence: float
    reason: str
    expected_fix: str
    failed_metrics: list[dict[str, Any]]
    suspected_causes: list[dict[str, Any]]
    evidence_refs: list[str]
    rejected_alternatives: list[dict[str, Any]]
    budget_status: str
    next_attempt: int
    requires_human: bool = False


@dataclass(frozen=True)
class FeedbackPacket:
    metadata: dict[str, Any]
    body: str
    path: Path | None = None

    @property
    def text(self) -> str:
        return fm_dumps(self.metadata, self.body)

    @property
    def target_agent(self) -> str:
        return str(self.metadata["target_agent"])

    @property
    def attempt(self) -> int:
        return int(self.metadata["attempt"])


class CommanderAgent:
    """Main Agent control logic for feedback-loop attribution."""

    name = "commander"
    diagnosis_schema = "diagnosis.v1"
    feedback_schema = "feedback_packet.v1"

    def diagnose(self, *, run: RunHandle, attempt: int) -> FeedbackDecision:
        observation = self.observe_run(run=run, attempt=attempt)
        attribution = self.diagnose_failure(observation)
        packet: FeedbackPacket | None = None
        feedback_packet_ref = ""
        if attribution.should_continue and attribution.target_agent in TARGET_AGENTS:
            packet = self.build_feedback_packet(
                observation=observation,
                attribution=attribution,
            )
            packet = self.write_feedback_packet(run=run, packet=packet)
            feedback_packet_ref = packet.path.relative_to(run.root).as_posix() if packet.path else ""

        ledger_ref = self.write_attempt_ledger_summary(run=run)
        self.write_diagnosis(
            run=run,
            observation=observation,
            attribution=attribution,
            feedback_packet_ref=feedback_packet_ref,
            attempt_ledger_ref=ledger_ref,
        )
        self.write_learning_memory(
            run=run,
            observation=observation,
            attribution=attribution,
            feedback_packet_ref=feedback_packet_ref,
        )

        return self.decide_feedback_action(
            attribution=attribution,
            feedback_packet_ref=feedback_packet_ref,
        )

    def observe_run(self, *, run: RunHandle, attempt: int) -> RunObservation:
        config = load_diagnostics_config(run.project)
        analysis = analyze_run(run, config)
        return RunObservation(
            run=run,
            attempt=attempt,
            config=config,
            analysis=analysis,
            metrics_summary=_summarize_metrics(run.subdir("execution") / "metrics.json"),
            curve_summary=_summarize_curves(run.subdir("execution") / "curves"),
            log_summary=_summarize_logs(run.subdir("execution")),
            approved_artifact_refs=_approved_artifact_refs(run),
            attempt_history=_attempt_history(run),
            latest_diagnosis=_latest_diagnosis(run),
        )

    def diagnose_failure(self, observation: RunObservation) -> CommanderAttribution:
        analysis = observation.analysis
        failed_metrics = [f.to_metadata() for f in analysis.failed_metrics]
        suspected = [c.to_metadata() for c in analysis.suspected_causes]
        evidence_refs = list(analysis.evidence_refs)

        if analysis.passed:
            return CommanderAttribution(
                passed=True,
                should_continue=False,
                target_agent="writing",
                confidence=1.0,
                reason="All configured metrics passed.",
                expected_fix="Proceed to Writing with the successful run evidence.",
                failed_metrics=failed_metrics,
                suspected_causes=suspected,
                evidence_refs=evidence_refs,
                rejected_alternatives=[],
                budget_status="not_applicable",
                next_attempt=observation.attempt,
            )

        if observation.attempt >= observation.config.max_iterations:
            return CommanderAttribution(
                passed=False,
                should_continue=False,
                target_agent="writing",
                confidence=0.9,
                reason="Feedback-loop iteration budget is exhausted.",
                expected_fix="Stop repair attempts and write a failure analysis report.",
                failed_metrics=failed_metrics,
                suspected_causes=suspected,
                evidence_refs=evidence_refs,
                rejected_alternatives=[],
                budget_status="exhausted",
                next_attempt=observation.attempt,
            )

        target, confidence, reason = self._select_target(observation)
        rejected = self._rejected_alternatives(target=target, observation=observation)
        requires_human = _target_flip_low_confidence(
            history=observation.attempt_history,
            target=target,
            confidence=confidence,
        )
        if requires_human:
            return CommanderAttribution(
                passed=False,
                should_continue=False,
                target_agent="none",
                confidence=confidence,
                reason=(
                    "Attribution target changed with low confidence; pausing for "
                    "human review instead of continuing automatic trial-and-error."
                ),
                expected_fix="Review the latest diagnosis and choose a feedback target.",
                failed_metrics=failed_metrics,
                suspected_causes=suspected,
                evidence_refs=evidence_refs,
                rejected_alternatives=rejected,
                budget_status="within_budget",
                next_attempt=observation.attempt + 1,
                requires_human=True,
            )

        return CommanderAttribution(
            passed=False,
            should_continue=True,
            target_agent=target,
            confidence=confidence,
            reason=reason,
            expected_fix=self._expected_fix(target),
            failed_metrics=failed_metrics,
            suspected_causes=suspected,
            evidence_refs=evidence_refs,
            rejected_alternatives=rejected,
            budget_status="within_budget",
            next_attempt=observation.attempt + 1,
        )

    def build_feedback_packet(
        self,
        *,
        observation: RunObservation,
        attribution: CommanderAttribution,
    ) -> FeedbackPacket:
        context_policy = load_context_budget_policy()
        memory_candidates = self._memory_candidates(observation, attribution)
        context_refs = list(
            dict.fromkeys(
                [
                    *attribution.evidence_refs,
                    *observation.approved_artifact_refs.values(),
                    context_policy.ledger_summary_ref,
                ]
            )
        )
        metadata: dict[str, Any] = {
            "schema": self.feedback_schema,
            "project": observation.run.project,
            "agent": self.name,
            "run_id": observation.run.run_id,
            "target_agent": attribution.target_agent,
            "attempt": attribution.next_attempt,
            "source_attempt": observation.attempt,
            "confidence": attribution.confidence,
            "why_this_agent": attribution.reason,
            "evidence_refs": attribution.evidence_refs,
            "failed_metrics": attribution.failed_metrics,
            "do_next": self._do_next(attribution.target_agent, observation),
            "avoid_repeating": self._avoid_repeating(attribution.target_agent),
            "context_refs": context_refs,
            "memory_candidates": memory_candidates,
            "created": datetime.now(tz=timezone.utc).isoformat(),
        }
        body = self._render_feedback_body(metadata, observation, attribution)
        return FeedbackPacket(metadata=metadata, body=body)

    def write_feedback_packet(
        self, *, run: RunHandle, packet: FeedbackPacket
    ) -> FeedbackPacket:
        path = run.subdir("diagnosis") / f"feedback_packet.attempt_{packet.attempt}.md"
        result = validate_document(packet.text, expected_schema=self.feedback_schema)
        if not result.valid:
            raise ValueError(f"feedback packet invalid: {result.first_error()}")
        path.write_text(packet.text, encoding="utf-8")
        return FeedbackPacket(metadata=packet.metadata, body=packet.body, path=path)

    def decide_feedback_action(
        self,
        *,
        attribution: CommanderAttribution,
        feedback_packet_ref: str,
    ) -> FeedbackDecision:
        return FeedbackDecision(
            passed=attribution.passed,
            should_continue=attribution.should_continue and bool(feedback_packet_ref),
            recommended_target=attribution.target_agent,
            recommended_action=attribution.expected_fix,
            budget_status=attribution.budget_status,
            next_attempt=attribution.next_attempt,
            requires_human=attribution.requires_human,
            feedback_packet_ref=feedback_packet_ref,
            confidence=attribution.confidence,
        )

    def decide_from_artifact(self, *, run: RunHandle, version: str) -> FeedbackDecision:
        path = run.subdir("diagnosis") / f"diagnosis.{version}.md"
        parsed = parse_fm(path.read_text(encoding="utf-8"))
        metadata = parsed.metadata
        attempt = int(metadata.get("attempt", 1) or 1)
        target = str(metadata.get("recommended_target", "none"))
        feedback_ref = str(metadata.get("feedback_packet_ref", ""))
        budget_status = str(metadata.get("budget_status", "not_applicable"))
        passed = bool(metadata.get("passed", False))
        requires_human = bool(metadata.get("requires_human", False))
        return FeedbackDecision(
            passed=passed,
            should_continue=(
                not passed
                and budget_status == "within_budget"
                and target in TARGET_AGENTS
                and bool(feedback_ref)
                and not requires_human
            ),
            recommended_target=target,
            recommended_action=str(metadata.get("recommended_action", "")),
            budget_status=budget_status,
            next_attempt=attempt + 1,
            requires_human=requires_human,
            feedback_packet_ref=feedback_ref,
            confidence=float(metadata.get("confidence", 0.0) or 0.0),
        )

    def write_diagnosis(
        self,
        *,
        run: RunHandle,
        observation: RunObservation,
        attribution: CommanderAttribution,
        feedback_packet_ref: str,
        attempt_ledger_ref: str,
    ) -> None:
        metadata: dict[str, Any] = {
            "schema": self.diagnosis_schema,
            "project": run.project,
            "agent": self.name,
            "run_id": run.run_id,
            "attempt": observation.attempt,
            "passed": attribution.passed,
            "failed_metrics": attribution.failed_metrics,
            "suspected_causes": attribution.suspected_causes,
            "recommended_target": attribution.target_agent,
            "recommended_action": attribution.expected_fix,
            "evidence_refs": attribution.evidence_refs,
            "budget_status": attribution.budget_status,
            "created": datetime.now(tz=timezone.utc).isoformat(),
            "max_iterations": observation.config.max_iterations,
            "confidence": attribution.confidence,
            "requires_human": attribution.requires_human,
            "attribution": {
                "target_agent": attribution.target_agent,
                "why_this_agent": attribution.reason,
                "expected_fix": attribution.expected_fix,
            },
            "rejected_alternatives": attribution.rejected_alternatives,
            "feedback_packet_ref": feedback_packet_ref,
            "attempt_ledger_ref": attempt_ledger_ref,
            "memory_candidates": self._memory_candidates(observation, attribution),
        }
        ArtifactStore(run).write(
            text=fm_dumps(metadata, self._render_diagnosis_body(observation, attribution)),
            agent_dir="diagnosis",
            stem="diagnosis",
            expected_schema=self.diagnosis_schema,
        )

    def write_attempt_ledger_summary(
        self,
        *,
        run: RunHandle,
        policy: ContextBudgetPolicy | None = None,
    ) -> str:
        active_policy = policy or load_context_budget_policy()
        packets = sorted(run.subdir("diagnosis").glob("feedback_packet.attempt_*.md"))
        lines = ["# Attempt Ledger Summary", ""]
        if not packets:
            lines.append("- No previous feedback packets.")
        else:
            recent_count = max(1, active_policy.recent_packet_count)
            older = max(0, len(packets) - recent_count)
            if older:
                lines.append(f"- {older} older feedback packet(s) collapsed.")
            lines.append(
                f"- Budget policy: keep latest {recent_count} packet(s), collapse older attempts."
            )
            for path in packets[-recent_count:]:
                try:
                    metadata = parse_fm(path.read_text(encoding="utf-8")).metadata
                except Exception:
                    continue
                lines.append(
                    "- attempt {attempt}: target={target}, confidence={confidence}, ref={ref}".format(
                        attempt=metadata.get("attempt"),
                        target=metadata.get("target_agent"),
                        confidence=metadata.get("confidence"),
                        ref=path.name,
                    )
                )
        path = run.subdir("diagnosis") / "attempt_ledger_summary.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path.relative_to(run.root).as_posix()

    def write_learning_memory(
        self,
        *,
        run: RunHandle,
        observation: RunObservation,
        attribution: CommanderAttribution,
        feedback_packet_ref: str,
    ) -> None:
        candidates = self._memory_candidates(observation, attribution)
        memory_outcomes = self._record_memory_outcomes(
            run=run,
            observation=observation,
            attribution=attribution,
        )
        event = {
            "attempt": observation.attempt,
            "target_agent": attribution.target_agent,
            "confidence": attribution.confidence,
            "reason": attribution.reason,
            "expected_fix": attribution.expected_fix,
            "passed": attribution.passed,
            "should_continue": attribution.should_continue,
            "failed_metrics": attribution.failed_metrics,
            "evidence_refs": attribution.evidence_refs,
            "feedback_packet_ref": feedback_packet_ref,
            "metric_snapshot": observation.metrics_summary,
            "memory_outcomes": memory_outcomes,
        }
        append_learning_event(run=run, event=event, memory_candidates=candidates)

    def _record_memory_outcomes(
        self,
        *,
        run: RunHandle,
        observation: RunObservation,
        attribution: CommanderAttribution,
    ) -> list[dict[str, Any]]:
        preferred = (
            attribution.target_agent
            if attribution.target_agent in TARGET_AGENTS and not attribution.passed
            else None
        )
        usages = _latest_memory_usages(run=run, preferred_agent=preferred)
        outcomes: list[dict[str, Any]] = []
        for agent, memory_ids in usages.items():
            if not attribution.passed and agent != attribution.target_agent:
                continue
            stale_ids = record_agent_memory_outcome(
                agent,
                memory_ids=memory_ids,
                run_id=run.run_id,
                attempt=observation.attempt,
                success=attribution.passed,
            )
            outcomes.append(
                {
                    "agent": agent,
                    "memory_ids": memory_ids,
                    "success": attribution.passed,
                    "stale_ids": list(stale_ids),
                }
            )
        return outcomes

    def _select_target(self, observation: RunObservation) -> tuple[str, float, str]:
        allowed = set(observation.config.allowed_targets) & TARGET_AGENTS
        if not allowed:
            return "coding", 0.4, "No configured feedback target was available; using coding fallback."

        high_config = any(
            cause.kind == "config_sanity" and cause.severity == "high"
            for cause in observation.analysis.suspected_causes
        )
        high_code = any(
            cause.kind == "code_change_risk" and cause.severity == "high"
            for cause in observation.analysis.suspected_causes
        )
        if high_config and "experiment" in allowed:
            return (
                "experiment",
                0.85,
                "Experiment design/config sanity has a high-severity issue.",
            )
        if high_code and "coding" in allowed:
            return "coding", 0.85, "Coding change risk is the strongest failure signal."
        default = observation.config.default_target
        if default in allowed:
            return default, 0.65, "Metrics missed thresholds; no higher-confidence upstream cause dominated."
        target = sorted(allowed)[0]
        return target, 0.6, "Metrics missed thresholds; selected the first allowed feedback target."

    def _rejected_alternatives(
        self, *, target: str, observation: RunObservation
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for candidate in sorted(TARGET_AGENTS):
            if candidate == target:
                continue
            out.append(
                {
                    "target_agent": candidate,
                    "confidence": 0.35,
                    "reason": f"Less direct evidence than {target}.",
                }
            )
        if not observation.config.enable_idea_loop:
            out.append(
                {
                    "target_agent": "idea",
                    "confidence": 0.0,
                    "reason": "Idea-loop feedback is disabled by project diagnostics config.",
                }
            )
        return out

    def _expected_fix(self, target: str) -> str:
        if target == "experiment":
            return (
                "Deepen the canceller memory-depth ablation (expert_count / memory taps) "
                "per the feedback packet, then rerun execution."
            )
        if target == "coding":
            return (
                "Increase the memory-polynomial canceller depth in a focused code_spec "
                "revision (keep Paper_Total_0327 / forward(x, stream_label) frozen), then rerun."
            )
        return "Pause for human review before deciding the next feedback target."

    def _do_next(
        self, target: str, observation: RunObservation
    ) -> list[str]:
        if target == "experiment":
            return [
                "RES (residual) missed target: the ablation grid under-provisioned canceller memory depth.",
                "Expand the memory-depth axis (expert_count / memory taps) toward the true 12-tap PIM memory.",
                "Keep the sweep bounded (<=16 ablations) and state the expected RES (dB) improvement.",
            ]
        return [
            "RES (residual) missed target: the memory-polynomial canceller has too few taps to cancel the PIM memory.",
            "Increase canceller memory depth (more taps); keep Paper_Total_0327 and forward(x, stream_label) frozen.",
            "Propose the smallest patch that lowers RES; do not touch protected baseline/ surfaces (Gate 5).",
        ]

    def _avoid_repeating(self, target: str) -> list[str]:
        if target == "experiment":
            return [
                "Do not repeat an ablation matrix that already missed thresholds.",
                "Do not expand the sweep without naming the expected signal.",
            ]
        return [
            "Do not apply broad refactors while the feedback loop is active.",
            "Do not touch protected baseline files unless Gate 5 explicitly allows it.",
        ]

    def _memory_candidates(
        self,
        observation: RunObservation,
        attribution: CommanderAttribution,
    ) -> list[dict[str, Any]]:
        if attribution.target_agent not in TARGET_AGENTS or attribution.confidence < 0.6:
            return []
        return [
            {
                "id": (
                    f"{observation.run.run_id}:attempt_{attribution.next_attempt}:"
                    f"{attribution.target_agent}"
                ),
                "agent": attribution.target_agent,
                "text": attribution.expected_fix,
                "validity_scope": f"project={observation.run.project}; target={attribution.target_agent}",
                "counterexample_risk": "May overfit to this run's synthetic/mock metric pattern.",
                "evidence_refs": attribution.evidence_refs,
                "source_attempt": observation.attempt,
                "ttl": "5_runs",
                "status": "pending_review",
            }
        ]

    def _render_feedback_body(
        self,
        metadata: dict[str, Any],
        observation: RunObservation,
        attribution: CommanderAttribution,
    ) -> str:
        metric_lines = "\n".join(
            "- `{metric}` observed `{observed}` target `{target}`".format(**item)
            for item in attribution.failed_metrics
            if {"metric", "observed", "target"}.issubset(item)
        ) or "- No metric gap recorded."
        do_next = "\n".join(f"- {item}" for item in metadata["do_next"])
        avoid = "\n".join(f"- {item}" for item in metadata["avoid_repeating"])
        return (
            "# Commander Feedback Packet\n\n"
            f"Target Agent: `{attribution.target_agent}`\n\n"
            f"Why: {attribution.reason}\n\n"
            "## Failed Metrics\n"
            f"{metric_lines}\n\n"
            "## Do Next\n"
            f"{do_next}\n\n"
            "## Avoid Repeating\n"
            f"{avoid}\n\n"
            "## Context Budget\n"
            "Use referenced files as evidence; do not inline full metrics, curves, or logs.\n\n"
            "## Snapshot\n"
            f"- Metrics summary: `{json.dumps(observation.metrics_summary, ensure_ascii=False)[:800]}`\n"
            f"- Curve summary: `{json.dumps(observation.curve_summary, ensure_ascii=False)[:800]}`\n"
        )

    def _render_diagnosis_body(
        self,
        observation: RunObservation,
        attribution: CommanderAttribution,
    ) -> str:
        causes = "\n".join(
            f"- `{item.get('kind')}` ({item.get('severity', 'medium')}): {item.get('summary')}"
            for item in attribution.suspected_causes
        ) or "- No blocking cause detected."
        return (
            f"# Commander Diagnosis attempt {observation.attempt}\n\n"
            f"Passed: `{str(attribution.passed).lower()}`.\n\n"
            f"Recommended target: `{attribution.target_agent}`.\n\n"
            f"Confidence: `{attribution.confidence:.2f}`.\n\n"
            f"Reason: {attribution.reason}\n\n"
            "## Suspected Causes\n"
            f"{causes}\n\n"
            "## Recommended Action\n"
            f"{attribution.expected_fix}\n"
        )


def load_feedback_context_for_agent(
    *,
    run: RunHandle,
    agent: str,
    attempt: int,
    max_tokens: int | None = None,
    policy: ContextBudgetPolicy | None = None,
) -> dict[str, Any] | None:
    """Return a bounded Commander feedback context for the target Agent."""
    if attempt <= 1 or agent not in TARGET_AGENTS:
        return None
    active_policy = (policy or load_context_budget_policy()).with_max_tokens(max_tokens)
    exact = run.subdir("diagnosis") / f"feedback_packet.attempt_{attempt}.md"
    candidates = [exact] if exact.exists() else sorted(
        run.subdir("diagnosis").glob("feedback_packet.attempt_*.md")
    )
    for path in reversed(candidates):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        try:
            metadata = parse_fm(text).metadata
        except Exception:
            continue
        if metadata.get("target_agent") != agent:
            continue
        bounded = bound_feedback_text(
            text,
            policy=active_policy,
            extra_prune_reasons=("non_target_agent_feedback_excluded",),
        )
        context_refs_raw = metadata.get("context_refs", [])
        context_refs = (
            [str(item) for item in context_refs_raw]
            if isinstance(context_refs_raw, list)
            else []
        )
        return {
            "text": bounded.text,
            "metadata": metadata,
            "path": path.relative_to(run.root).as_posix(),
            "context_refs": context_refs,
            **bounded.to_metadata(),
        }
    return None


def _summarize_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "metrics": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"exists": True, "error": "invalid_json"}
    rows = raw if isinstance(raw, list) else []
    by_metric: dict[str, list[float]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics", {})
        if not isinstance(metrics, dict):
            continue
        for key, value in metrics.items():
            try:
                by_metric.setdefault(str(key), []).append(float(value))
            except (TypeError, ValueError):
                continue
    return {
        "exists": True,
        "row_count": len(rows),
        "metrics": {
            key: {"min": min(values), "max": max(values), "mean": mean(values)}
            for key, values in by_metric.items()
            if values
        },
    }


def _summarize_curves(curves_dir: Path) -> dict[str, Any]:
    if not curves_dir.exists():
        return {"exists": False, "curves": []}
    curves: list[dict[str, Any]] = []
    for path in sorted(curves_dir.glob("*.json"))[:8]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        values = raw.get("values") if isinstance(raw, dict) else raw
        if not isinstance(values, list) or not values:
            continue
        nums: list[float] = []
        for value in values:
            try:
                nums.append(float(value))
            except (TypeError, ValueError):
                continue
        if nums:
            curves.append(
                {
                    "path": path.name,
                    "points": len(nums),
                    "start": nums[0],
                    "end": nums[-1],
                    "delta": nums[-1] - nums[0],
                }
            )
    return {"exists": True, "curves": curves}


def _summarize_logs(execution_dir: Path) -> dict[str, Any]:
    logs = sorted(execution_dir.glob("*.log"))
    lines: list[str] = []
    for path in logs[-3:]:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        lines.extend(text[-8:])
    return {"log_files": [p.name for p in logs[-3:]], "tail": lines[-24:]}


def _approved_artifact_refs(run: RunHandle) -> dict[str, str]:
    stems = {
        "idea": "idea_proposal.approved.md",
        "experiment": "experiment_plan.approved.md",
        "coding": "code_spec.approved.md",
        "execution": "run_log.approved.md",
        "writing": "research_report.approved.md",
    }
    refs: dict[str, str] = {}
    for agent, filename in stems.items():
        path = run.subdir(agent) / filename
        if path.exists():
            refs[agent] = f"{agent}/{filename}"
    return refs


def _latest_diagnosis(run: RunHandle) -> dict[str, Any] | None:
    versions = sorted(run.subdir("diagnosis").glob("diagnosis.v*.md"))
    if not versions:
        return None
    try:
        return parse_fm(versions[-1].read_text(encoding="utf-8")).metadata
    except Exception:
        return None


def _attempt_history(run: RunHandle) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for path in sorted(run.subdir("diagnosis").glob("diagnosis.v*.md")):
        try:
            metadata = parse_fm(path.read_text(encoding="utf-8")).metadata
        except Exception:
            continue
        history.append(
            {
                "path": path.relative_to(run.root).as_posix(),
                "attempt": metadata.get("attempt"),
                "target_agent": (
                    metadata.get("attribution", {}).get("target_agent")
                    if isinstance(metadata.get("attribution"), dict)
                    else metadata.get("recommended_target")
                ),
                "confidence": metadata.get("confidence", 0.0),
                "passed": metadata.get("passed", False),
            }
        )
    return history


def _latest_memory_usages(
    *,
    run: RunHandle,
    preferred_agent: str | None,
) -> dict[str, list[str]]:
    context_dir = run.subdir("context")
    agents = (preferred_agent,) if preferred_agent else tuple(sorted(TARGET_AGENTS))
    usages: dict[str, list[str]] = {}
    for agent in agents:
        if agent is None:
            continue
        paths = sorted(
            context_dir.glob(f"{agent}*_context_pack.v*.json"),
            key=lambda path: path.stat().st_mtime,
        )
        for path in reversed(paths):
            ids = _memory_ids_from_context_pack(path)
            if ids:
                usages[agent] = ids
                break
    return usages


def _memory_ids_from_context_pack(path: Path) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, dict):
        return []
    summary = raw.get("summary")
    if not isinstance(summary, dict):
        return []
    metadata = summary.get("metadata")
    if not isinstance(metadata, dict):
        return []
    memory_sources = metadata.get("memory_sources")
    if not isinstance(memory_sources, dict):
        return []
    ids_raw = memory_sources.get("long_term_memory_ids")
    if not isinstance(ids_raw, list):
        return []
    ids = [str(item).strip() for item in ids_raw if str(item).strip()]
    return list(dict.fromkeys(ids))


def _target_flip_low_confidence(
    *,
    history: list[dict[str, Any]],
    target: str,
    confidence: float,
) -> bool:
    if confidence >= LOW_CONFIDENCE_THRESHOLD or not history:
        return False
    previous = history[-1]
    prev_target = str(previous.get("target_agent", ""))
    try:
        prev_confidence = float(previous.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        prev_confidence = 0.0
    return bool(
        prev_target
        and prev_target != target
        and prev_confidence < LOW_CONFIDENCE_THRESHOLD
    )
