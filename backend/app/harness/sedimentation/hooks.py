"""Sedimentation hook fired by the orchestrator after each Agent completes."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from app.harness.evaluation.aggregation import has_blocker, worst_decision
from app.harness.evaluation.artifacts import write_report
from app.harness.evaluation.models import EvaluationReport
from app.harness.evaluation.runner import EvaluationRunner
from app.harness.kb.config import MemoryProfile, current_profile, lifecycle_config, mock_policy, write_gate
from app.harness.kb.models import EvalStatus, infer_memory_type
from app.harness.kb.stores import QUARANTINE_ZONE
from app.harness.memory.importance import calculate_importance
from app.harness.schema.frontmatter_parser import parse as fm_parse
from app.harness.sedimentation.asset_metadata import make as make_metadata
from app.harness.sedimentation.extractors import run_extractor


class RunLike(Protocol):
    run_id: str
    project: str
    root: Path
    meta: dict[str, Any]


class ArtifactRefLike(Protocol):
    path: Path


def on_agent_completed(
    *, agent: str, project: str, run_id: str, artifact_text: str
) -> dict[str, Any]:
    """Parse the artifact, route through the per-agent extractor."""
    parsed = fm_parse(artifact_text)
    schema = str(parsed.metadata.get("schema") or "")
    written = run_extractor(
        agent=agent,
        project=project,
        run_id=run_id,
        schema=schema,
        text=artifact_text,
        metadata=parsed.metadata,
    )
    logger.info(
        "sedimentation: agent={} project={} run={} schema={} chunks_written={}",
        agent, project, run_id, schema, written,
    )
    return {"agent": agent, "schema": schema, "chunks_written": written}


def sediment_approved_artifact(
    *,
    run: RunLike,
    agent: str,
    artifact_ref: ArtifactRefLike,
    profile: MemoryProfile | None = None,
) -> dict[str, Any]:
    """Gate and write an approved artifact into Memory v2.

    This is the approved-only hot path. It is intentionally synchronous because
    V0's file-backed store is small and deterministic.
    """
    selected_profile = profile or current_profile()
    gate = write_gate(selected_profile)
    text = artifact_ref.path.read_text(encoding="utf-8")
    parsed = fm_parse(text)
    schema = str(parsed.metadata.get("schema") or "")
    source_path = _relative_to_repo_or_run(run=run, path=artifact_ref.path)
    reports = EvaluationRunner().evaluate_text(
        project=run.project,
        text=text,
        target_ref=source_path,
        expected_schema=schema or None,
        target_schema=schema or None,
    )
    scorecard_paths = _write_scorecards(run=run, agent=agent, reports=reports)
    eval_passed = _eval_passed(reports)
    is_mock = _detect_mock(text=text, metadata=parsed.metadata, run=run)
    salience = _salience(agent=agent, metadata=parsed.metadata, text=text)
    min_salience = float(gate.get("min_salience", 0.0) or 0.0)
    target_zone_override = ""
    blocked_reason = ""
    if is_mock:
        target_zone_override = str(mock_policy().get("zone", QUARANTINE_ZONE) or QUARANTINE_ZONE)
        blocked_reason = "mock_quarantine"
    elif bool(gate.get("require_real_execution", False)) and _requires_real_execution(run=run):
        target_zone_override = QUARANTINE_ZONE
        blocked_reason = "real_execution_required"
    elif bool(gate.get("require_eval_pass", False)) and not eval_passed:
        target_zone_override = QUARANTINE_ZONE
        blocked_reason = "eval_not_passed"
    elif salience < min_salience:
        return {
            "agent": agent,
            "schema": schema,
            "chunks_written": 0,
            "skipped": True,
            "reason": "salience_below_threshold",
            "salience": salience,
        }

    eval_status = EvalStatus(
        passed=eval_passed,
        checks={report.evaluator: report.decision == "pass" for report in reports},
        scorecard=scorecard_paths[0] if scorecard_paths else "",
        decision=worst_decision(reports),
        blocking=has_blocker(reports),
        reason=blocked_reason,
    )
    written = _run_extractor_gated(
        agent=agent,
        project=run.project,
        run_id=run.run_id,
        schema=schema,
        text=text,
        metadata=parsed.metadata,
        source_path=source_path,
        is_mock=is_mock,
        eval_status=eval_status,
        salience=salience,
        target_zone_override=target_zone_override,
    )
    evaluation_written = _sediment_evaluation_reports(
        run=run,
        agent=agent,
        reports=reports,
        scorecard_paths=scorecard_paths,
        target_artifact=source_path,
        is_mock=is_mock,
        target_zone_override=target_zone_override,
    )
    logger.info(
        "memory sediment approved: agent={} project={} run={} schema={} profile={} chunks_written={} evaluation_chunks={} mock={} eval_passed={}",
        agent,
        run.project,
        run.run_id,
        schema,
        selected_profile,
        written,
        evaluation_written,
        is_mock,
        eval_passed,
    )
    return {
        "agent": agent,
        "schema": schema,
        "chunks_written": written,
        "is_mock": is_mock,
        "eval_passed": eval_passed,
        "profile": selected_profile,
        "scorecards": scorecard_paths,
        "evaluation_chunks_written": evaluation_written,
    }


def _run_extractor_gated(
    *,
    agent: str,
    project: str,
    run_id: str,
    schema: str,
    text: str,
    metadata: dict[str, Any],
    source_path: str,
    is_mock: bool,
    eval_status: EvalStatus,
    salience: float,
    target_zone_override: str,
) -> int:
    from app.harness.kb.memory_writer import write_to_zone
    from app.harness.sedimentation.extractors import REGISTRY

    extractor = REGISTRY.get(agent)
    if extractor is None:
        return 0
    ttl_default = int(lifecycle_config().get("default_ttl_days", 180) or 180)
    run_archive_ttl = int(lifecycle_config().get("run_archive_ttl_days", 365) or 365)
    written = 0
    for zone, body, extra in extractor(text, metadata, run_id):
        target_zone = target_zone_override or zone
        meta = make_metadata(
            project=project,
            agent=agent,
            run_id=run_id,
            schema=schema,
            extra={
                **extra,
                "source_path": source_path,
                "is_mock": is_mock,
                "approved": True,
            },
        )
        kind = str(extra.get("kind", ""))
        ttl_days = run_archive_ttl if zone == "run_archive" else ttl_default
        written += write_to_zone(
            zone=target_zone,
            text=body,
            metadata=meta,
            memory_type=infer_memory_type(zone=zone, kind=kind),
            source_path=source_path,
            run_id=run_id,
            agent=agent,
            schema=schema,
            is_mock=is_mock,
            confidence=0.9 if eval_status.passed else 0.55,
            eval_status=eval_status,
            salience=salience,
            ttl_days=int(mock_policy().get("ttl_days", 3) or 3) if is_mock else ttl_days,
            approved=True,
        )
    return written


def _write_scorecards(
    *, run: RunLike, agent: str, reports: list[EvaluationReport]
) -> list[str]:
    out: list[str] = []
    eval_dir = run.root / "evaluation"
    for index, report in enumerate(reports, start=1):
        path = eval_dir / f"{agent}_{report.evaluator.replace('.', '_')}.v{index}.md"
        write_report(path, report)
        out.append(path.relative_to(run.root).as_posix())
    return out


def _sediment_evaluation_reports(
    *,
    run: RunLike,
    agent: str,
    reports: list[EvaluationReport],
    scorecard_paths: list[str],
    target_artifact: str,
    is_mock: bool,
    target_zone_override: str,
) -> int:
    from app.harness.kb.memory_writer import write_to_zone

    written = 0
    ttl_days = int(lifecycle_config().get("default_ttl_days", 180) or 180)
    for report, scorecard_path in zip(reports, scorecard_paths, strict=False):
        target_zone = target_zone_override or "methodology"
        report_status = EvalStatus(
            passed=report.decision == "pass" and not report.blocking,
            checks={report.evaluator: report.decision == "pass"},
            scorecard=scorecard_path,
            decision=report.decision,
            blocking=report.blocking,
        )
        metadata = make_metadata(
            project=run.project,
            agent="evaluation",
            run_id=run.run_id,
            schema="evaluation_report.v1",
            extra={
                "kind": "evaluation_report",
                "source_path": scorecard_path,
                "target_artifact": target_artifact,
                "target_agent": agent,
                "evaluator": report.evaluator,
                "decision": report.decision,
                "blocking": report.blocking,
                "is_mock": is_mock,
                "approved": True,
            },
        )
        salience = 0.8 if report.blocking or report.findings else 0.45
        confidence = report.overall_score if report.overall_score is not None else 0.7
        written += write_to_zone(
            zone=target_zone,
            text=report.to_markdown(),
            metadata=metadata,
            memory_type="procedural" if report.evaluator.startswith("contract.") else "episodic",
            source_path=scorecard_path,
            run_id=run.run_id,
            agent="evaluation",
            schema="evaluation_report.v1",
            is_mock=is_mock,
            confidence=confidence,
            eval_status=report_status,
            salience=salience,
            ttl_days=int(mock_policy().get("ttl_days", 3) or 3) if is_mock else ttl_days,
            approved=True,
        )
    return written


def _eval_passed(reports: list[EvaluationReport]) -> bool:
    return bool(reports) and all(
        report.decision == "pass" and not report.blocking for report in reports
    )


def _detect_mock(*, text: str, metadata: dict[str, Any], run: RunLike) -> bool:
    if bool(metadata.get("is_mock", False)):
        return True
    lowered = text.lower()
    if "mock_provider" in lowered or "mock_simulation" in lowered or "模拟产物" in text:
        return True
    if str(metadata.get("debate_mode", "")).lower() == "mock_debate":
        return True
    return str(run.meta.get("mock", "")).lower() in {"1", "true", "yes"}


def _salience(*, agent: str, metadata: dict[str, Any], text: str) -> float:
    importance = calculate_importance(agent=agent, metadata=metadata, text=text)
    base = importance.score
    if metadata.get("quality_warnings"):
        base = max(base, 0.65)
    if agent == "execution":
        base = max(base, 0.8)
    return max(0.0, min(1.0, base))


def _requires_real_execution(*, run: RunLike) -> bool:
    raw_mock = str(run.meta.get("mock", "")).lower()
    if raw_mock in {"1", "true", "yes"}:
        return True
    raw_execution = str(run.meta.get("execution_mode", "")).lower()
    return raw_execution in {"mock", "mock_simulation"}


def _relative_to_repo_or_run(*, run: RunLike, path: Path) -> str:
    try:
        return path.relative_to(run.root).as_posix()
    except ValueError:
        return str(path)
