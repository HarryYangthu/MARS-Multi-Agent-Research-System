"""Post-training data export candidates derived from evaluated artifacts.

This module intentionally stops at data construction. It does not create
preference pairs, rewards, checkpoints, or any training job.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness.evaluation.artifacts import read_reports_for_artifact
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.harness.schema.validator import validate_document

_ARTIFACT_RE = re.compile(r"^(?P<stem>.+?)\.(?P<version>v\d+|approved)\.md$")
_AGENT_DIRS = frozenset(
    {"idea", "experiment", "coding", "execution", "diagnosis", "writing"}
)
_DECISION_RANK = {
    "pass": 0,
    "warn": 1,
    "revise": 2,
    "block": 3,
    "fail": 4,
}


@dataclass(frozen=True)
class PostTrainingExportOptions:
    include_drafts: bool = False
    include_body_chars: int = 6000
    min_artifact_score: float = 0.65
    allowed_decisions: tuple[str, ...] = ("pass", "warn")
    destination: str = "events/post_training_export.jsonl"


def build_post_training_records(
    *,
    run_root: Path,
    run_id: str,
    project: str,
    options: PostTrainingExportOptions | None = None,
) -> list[dict[str, Any]]:
    opts = options or PostTrainingExportOptions()
    run_label = _run_outcome_label(run_root)
    artifacts = _discover_artifacts(run_root=run_root, include_drafts=opts.include_drafts)
    records: list[dict[str, Any]] = []
    for artifact in artifacts:
        records.append(
            _build_record(
                run_root=run_root,
                run_id=run_id,
                project=project,
                artifact=artifact,
                run_label=run_label,
                options=opts,
            )
        )
    return records


def write_post_training_export(
    *,
    run_root: Path,
    run_id: str,
    project: str,
    options: PostTrainingExportOptions | None = None,
) -> dict[str, Any]:
    opts = options or PostTrainingExportOptions()
    records = build_post_training_records(
        run_root=run_root,
        run_id=run_id,
        project=project,
        options=opts,
    )
    target = _resolve_destination(run_root=run_root, destination=opts.destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    manifest = _manifest_for_records(
        run_root=run_root,
        run_id=run_id,
        project=project,
        path=target,
        records=records,
        options=opts,
    )
    manifest_path = target.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest


def read_post_training_export(
    *,
    run_root: Path,
    run_id: str,
    project: str,
    destination: str = "events/post_training_export.jsonl",
    preview_limit: int = 5,
) -> dict[str, Any] | None:
    target = _resolve_destination(run_root=run_root, destination=destination)
    if not target.exists():
        return None
    records = _read_jsonl(target)
    manifest_path = target.with_suffix(".manifest.json")
    if manifest_path.exists():
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            parsed["records_preview"] = records[:preview_limit]
            return parsed
    return _manifest_for_records(
        run_root=run_root,
        run_id=run_id,
        project=project,
        path=target,
        records=records,
        options=PostTrainingExportOptions(destination=destination),
        preview_limit=preview_limit,
    )


def _build_record(
    *,
    run_root: Path,
    run_id: str,
    project: str,
    artifact: dict[str, str],
    run_label: dict[str, Any],
    options: PostTrainingExportOptions,
) -> dict[str, Any]:
    path = run_root / artifact["ref"]
    text = path.read_text(encoding="utf-8")
    validation = validate_document(text)
    parsed = parse_frontmatter(text)
    reports = read_reports_for_artifact(
        run_root=run_root,
        agent_dir=artifact["agent"],
        stem=artifact["stem"],
        version=artifact["version"],
    )
    evaluation = _evaluation_summary(reports=reports, fallback_ref=artifact["ref"])
    labels = _labels(
        validation_valid=validation.valid,
        artifact_ref=artifact["ref"],
        reports=reports,
        evaluation=evaluation,
        run_label=run_label,
    )
    training_eligible = _training_eligible(
        artifact=artifact,
        evaluation=evaluation,
        labels=labels,
        options=options,
    )
    preference = _preference_candidate(
        run_root=run_root,
        artifact=artifact,
        evaluation=evaluation,
        training_eligible=training_eligible,
    )
    return {
        "schema": "post_training_example.v1",
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "run_id": run_id,
        "project": project,
        "artifact": {
            "ref": artifact["ref"],
            "agent": artifact["agent"],
            "stem": artifact["stem"],
            "version": artifact["version"],
            "schema": validation.schema_id,
            "approved": artifact["version"] == "approved",
        },
        "output": {
            "format": "markdown_with_yaml_frontmatter",
            "frontmatter": parsed.metadata,
            "body": _truncate(parsed.body, options.include_body_chars),
            "truncated": len(parsed.body) > options.include_body_chars,
        },
        "evaluation": evaluation,
        "labels": labels,
        "preference_candidate": preference,
        "training_eligible": training_eligible,
    }


def _discover_artifacts(*, run_root: Path, include_drafts: bool) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    for agent in sorted(_AGENT_DIRS):
        directory = run_root / agent
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            match = _ARTIFACT_RE.match(path.name)
            if match is None:
                continue
            version = match.group("version")
            if version != "approved" and not include_drafts:
                continue
            artifacts.append(
                {
                    "agent": agent,
                    "stem": match.group("stem"),
                    "version": version,
                    "ref": path.relative_to(run_root).as_posix(),
                }
            )
    artifacts.sort(key=lambda item: (item["agent"], item["stem"], item["version"]))
    return artifacts


def _evaluation_summary(
    *,
    reports: list[dict[str, Any]],
    fallback_ref: str,
) -> dict[str, Any]:
    items = [_report_item(report) for report in reports]
    decisions = [
        decision
        for item in items
        if isinstance(decision := item.get("decision"), str)
        and decision in _DECISION_RANK
    ]
    decision = (
        max(decisions, key=lambda item: _DECISION_RANK[item])
        if decisions
        else "pass"
    )
    scores = [
        float(score)
        for item in items
        if isinstance(score := item.get("overall_score"), int | float)
    ]
    blocking = any(
        item.get("blocking") is True or item.get("decision") in {"block", "fail"}
        for item in items
    )
    return {
        "target_ref": fallback_ref,
        "decision": decision,
        "blocking": blocking,
        "overall_score": round(sum(scores) / len(scores), 6) if scores else None,
        "report_count": len(items),
        "evaluator_versions": _evaluator_versions(items),
        "evidence_refs": _evidence_refs(items, fallback=fallback_ref),
        "reports": items,
    }


def _report_item(report: dict[str, Any]) -> dict[str, Any]:
    metadata = report.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    scores = metadata.get("scores")
    findings = metadata.get("findings")
    return {
        "path": report.get("path"),
        "target_ref": metadata.get("target_ref"),
        "target_schema": metadata.get("target_schema"),
        "evaluator": metadata.get("evaluator"),
        "evaluator_version": metadata.get("evaluator_version"),
        "decision": metadata.get("decision"),
        "blocking": bool(metadata.get("blocking")),
        "overall_score": metadata.get("overall_score"),
        "scores": scores if isinstance(scores, dict) else {},
        "findings": findings if isinstance(findings, list) else [],
    }


def _labels(
    *,
    validation_valid: bool,
    artifact_ref: str,
    reports: list[dict[str, Any]],
    evaluation: dict[str, Any],
    run_label: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    report_items = [_report_item(report) for report in reports]
    return {
        "schema_validity": _schema_validity_label(
            validation_valid=validation_valid,
            artifact_ref=artifact_ref,
            items=report_items,
        ),
        "baseline_preservation": _baseline_label(
            artifact_ref=artifact_ref,
            items=report_items,
        ),
        "artifact_score": _artifact_score_label(
            artifact_ref=artifact_ref,
            evaluation=evaluation,
        ),
        "outcome_passed": run_label,
    }


def _schema_validity_label(
    *,
    validation_valid: bool,
    artifact_ref: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    report = _find_report(items, "contract.schema_validity")
    score = _as_float(report.get("overall_score")) if report is not None else None
    value = bool(score and score >= 1.0) if score is not None else validation_valid
    return _label(
        name="schema_validity",
        value=value,
        score=score if score is not None else (1.0 if validation_valid else 0.0),
        source="contract.schema_validity",
        reports=[report] if report is not None else [],
        fallback_ref=artifact_ref,
    )


def _baseline_label(
    *,
    artifact_ref: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    quality = _find_report(items, "artifact_quality.rubric")
    score: float | None = None
    source = "artifact_quality.rubric"
    if quality is not None:
        scores = quality.get("scores")
        if isinstance(scores, dict):
            for key, value in scores.items():
                if str(key).startswith("baseline"):
                    score = _as_float(value)
                    break
    return _label(
        name="baseline_preservation",
        value=None if score is None else score >= 0.65,
        score=score,
        source=source,
        reports=[quality] if quality is not None else [],
        fallback_ref=artifact_ref,
    )


def _artifact_score_label(
    *,
    artifact_ref: str,
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    score = _as_float(evaluation.get("overall_score"))
    reports = [
        report
        for report in _as_dict_list(evaluation.get("reports"))
        if isinstance(report.get("evaluator"), str)
    ]
    return _label(
        name="artifact_score",
        value=score,
        score=score,
        source="evaluation.aggregate",
        reports=reports,
        fallback_ref=artifact_ref,
    )


def _run_outcome_label(run_root: Path) -> dict[str, Any]:
    scorecard_path = run_root / "events" / "evaluation_scorecard.json"
    quality_gate_path = run_root / "events" / "evaluation_quality_gate.json"
    scorecard = _read_json_object(scorecard_path)
    gate = _read_json_object(quality_gate_path)
    decision = str(scorecard.get("overall_decision", ""))
    gate_status = str(gate.get("gate", ""))
    value: bool | None = None
    if decision:
        value = decision not in {"block", "fail"}
    if gate_status:
        value = gate_status != "block" and gate.get("completion_allowed") is not False
    evidence_refs = []
    if scorecard_path.exists():
        evidence_refs.append("events/evaluation_scorecard.json")
    if quality_gate_path.exists():
        evidence_refs.append("events/evaluation_quality_gate.json")
    evaluator_versions: dict[str, int] = {}
    if scorecard:
        evaluator_versions["evaluation_scorecard"] = 1
    if gate:
        evaluator_versions["evaluation_quality_gate"] = 1
    return {
        "name": "outcome_passed",
        "value": value,
        "score": _as_float(scorecard.get("overall_score")),
        "source": "evaluation_scorecard",
        "evaluator_versions": evaluator_versions,
        "evidence_refs": evidence_refs,
    }


def _training_eligible(
    *,
    artifact: dict[str, str],
    evaluation: dict[str, Any],
    labels: dict[str, dict[str, Any]],
    options: PostTrainingExportOptions,
) -> bool:
    if artifact["version"] != "approved":
        return False
    if labels["schema_validity"].get("value") is not True:
        return False
    if evaluation.get("blocking") is True:
        return False
    decision = str(evaluation.get("decision", ""))
    if decision not in options.allowed_decisions:
        return False
    score = _as_float(evaluation.get("overall_score"))
    if score is not None and score < options.min_artifact_score:
        return False
    outcome = labels["outcome_passed"].get("value")
    return outcome is not False


def _preference_candidate(
    *,
    run_root: Path,
    artifact: dict[str, str],
    evaluation: dict[str, Any],
    training_eligible: bool,
) -> dict[str, Any]:
    sibling_refs = _sibling_refs(run_root=run_root, artifact=artifact)
    decision = str(evaluation.get("decision", "pass"))
    label = _quality_label(decision=decision, training_eligible=training_eligible)
    return {
        "role": "chosen_candidate" if training_eligible else "review_candidate",
        "quality_label": label,
        "weight": _quality_weight(decision=decision, training_eligible=training_eligible),
        "chosen_ref": artifact["ref"] if artifact["version"] == "approved" else None,
        "sibling_candidate_refs": sibling_refs,
        "pair_constructed": False,
    }


def _sibling_refs(*, run_root: Path, artifact: dict[str, str]) -> list[str]:
    directory = run_root / artifact["agent"]
    refs: list[str] = []
    for path in sorted(directory.glob(f"{artifact['stem']}.*.md")):
        if path.name == Path(artifact["ref"]).name:
            continue
        if _ARTIFACT_RE.match(path.name) is None:
            continue
        refs.append(path.relative_to(run_root).as_posix())
    return refs


def _label(
    *,
    name: str,
    value: Any,
    score: float | None,
    source: str,
    reports: list[dict[str, Any]],
    fallback_ref: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "score": score,
        "source": source,
        "evaluator_versions": _evaluator_versions(reports),
        "evidence_refs": _evidence_refs(reports, fallback=fallback_ref),
    }


def _find_report(items: list[dict[str, Any]], evaluator: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("evaluator") == evaluator:
            return item
    return None


def _evaluator_versions(items: list[dict[str, Any]]) -> dict[str, int]:
    versions: dict[str, int] = {}
    for item in items:
        evaluator = item.get("evaluator")
        version = item.get("evaluator_version")
        if isinstance(evaluator, str) and isinstance(version, int):
            versions[evaluator] = version
    return versions


def _evidence_refs(items: list[dict[str, Any]], *, fallback: str) -> list[str]:
    refs: list[str] = []
    for item in items:
        findings = item.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            raw_refs = finding.get("evidence_refs")
            if not isinstance(raw_refs, list):
                continue
            refs.extend(str(ref) for ref in raw_refs if isinstance(ref, str))
    if not refs:
        refs.append(fallback)
    return sorted(set(refs))


def _quality_label(*, decision: str, training_eligible: bool) -> str:
    if training_eligible and decision == "pass":
        return "accepted"
    if training_eligible:
        return "accepted_with_warnings"
    if decision in {"block", "fail"}:
        return "blocked"
    if decision == "revise":
        return "needs_revision"
    return "review_required"


def _quality_weight(*, decision: str, training_eligible: bool) -> float:
    if not training_eligible:
        return 0.0 if decision in {"block", "fail"} else 0.25
    if decision == "pass":
        return 1.0
    return 0.75


def _manifest_for_records(
    *,
    run_root: Path,
    run_id: str,
    project: str,
    path: Path,
    records: list[dict[str, Any]],
    options: PostTrainingExportOptions,
    preview_limit: int = 5,
) -> dict[str, Any]:
    rel_path = path.relative_to(run_root).as_posix()
    eligible_count = sum(1 for record in records if record.get("training_eligible") is True)
    return {
        "schema": "post_training_export_manifest.v1",
        "run_id": run_id,
        "project": project,
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "path": rel_path,
        "record_count": len(records),
        "eligible_count": eligible_count,
        "include_drafts": options.include_drafts,
        "min_artifact_score": options.min_artifact_score,
        "allowed_decisions": list(options.allowed_decisions),
        "records_preview": records[:preview_limit],
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _resolve_destination(*, run_root: Path, destination: str) -> Path:
    rel = Path(destination)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("post-training export destination must stay under the run root")
    return run_root / rel


def _truncate(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit]


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _as_dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


__all__ = [
    "PostTrainingExportOptions",
    "build_post_training_records",
    "read_post_training_export",
    "write_post_training_export",
]
