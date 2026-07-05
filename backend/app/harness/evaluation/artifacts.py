"""Persist and read evaluation reports."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.harness.evaluation.models import EvaluationReport
from app.harness.evaluation.runner import EvaluationRunner
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.harness.schema.validator import validate_document

_EVAL_RE = re.compile(
    r"^(?P<stem>.+?)\.(?P<version>v\d+|approved)\.(?P<evaluator>.+)\.eval\.md$"
)


def write_report(path: Path, report: EvaluationReport) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.to_markdown(), encoding="utf-8")
    return path


def evaluator_slug(evaluator_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", evaluator_id).strip("_")
    return slug or "evaluator"


def report_path_for_artifact(
    *,
    artifact_path: Path,
    stem: str,
    version: str,
    evaluator_id: str,
) -> Path:
    return artifact_path.parent / "evals" / f"{stem}.{version}.{evaluator_slug(evaluator_id)}.eval.md"


def write_reports_for_artifact(
    *,
    project: str,
    artifact_path: Path,
    run_root: Path,
    stem: str,
    version: str,
    expected_schema: str | None,
    runner: EvaluationRunner | None = None,
) -> list[Path]:
    text = artifact_path.read_text(encoding="utf-8")
    target_ref = artifact_path.relative_to(run_root).as_posix()
    reports = (runner or EvaluationRunner()).evaluate_text(
        project=project,
        text=text,
        target_ref=target_ref,
        expected_schema=expected_schema,
        target_schema=expected_schema,
    )
    written: list[Path] = []
    for report in reports:
        path = report_path_for_artifact(
            artifact_path=artifact_path,
            stem=stem,
            version=version,
            evaluator_id=report.evaluator,
        )
        write_report(path, report)
        written.append(path)
    return written


def copy_reports_for_approval(*, source_path: Path, approved_path: Path, stem: str) -> list[Path]:
    written: list[Path] = []
    version = _artifact_version(source_path.name, stem)
    if version is None:
        return written
    prefix = f"{stem}.{version}."
    suffix = ".eval.md"
    eval_dir = source_path.parent / "evals"
    for source_eval in sorted(eval_dir.glob(f"{stem}.{version}.*.eval.md")):
        evaluator_part = source_eval.name.removeprefix(prefix).removesuffix(suffix)
        target = approved_path.parent / "evals" / f"{stem}.approved.{evaluator_part}.eval.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source_eval.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(target)
    return written


def read_reports_for_artifact(
    *,
    run_root: Path,
    agent_dir: str,
    stem: str,
    version: str,
) -> list[dict[str, Any]]:
    directory = run_root / agent_dir
    reports: list[dict[str, Any]] = []
    for path in sorted((directory / "evals").glob(f"{stem}.{version}.*.eval.md")):
        item = _read_report(path=path, run_root=run_root)
        if item is not None:
            reports.append(item)
    return reports


def read_all_reports(*, run_root: Path) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for path in sorted(run_root.glob("*/evals/*.eval.md")):
        item = _read_report(path=path, run_root=run_root)
        if item is not None:
            reports.append(item)
    return reports


def _read_report(*, path: Path, run_root: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    validation = validate_document(text, expected_schema="evaluation_report.v1")
    if not validation.valid:
        return None
    parsed = parse_frontmatter(text)
    match = _EVAL_RE.match(path.name)
    evaluator = match.group("evaluator") if match else ""
    return {
        "path": path.relative_to(run_root).as_posix(),
        "filename": path.name,
        "evaluator_slug": evaluator,
        "metadata": parsed.metadata,
        "text": text,
    }


def _artifact_version(filename: str, stem: str) -> str | None:
    prefix = f"{stem}."
    suffix = ".md"
    if not filename.startswith(prefix) or not filename.endswith(suffix):
        return None
    version = filename.removeprefix(prefix).removesuffix(suffix)
    return version or None
