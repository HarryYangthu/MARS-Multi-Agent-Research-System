"""Create and read report bundle manifests for completed Writing Agent runs."""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.harness.schema.frontmatter_parser import parse
from app.reporting.data_pack import collect_report_data_pack
from app.reporting.generators import (
    pretty_json,
    write_research_deck,
    write_research_docx,
    write_results_workbook,
)
from app.settings import repo_root
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle


def generate_report_bundle(run: RunHandle, *, actor: str = "system") -> dict[str, Any]:
    cfg = _reporting_config()
    deliverables_dir = run.root / str(cfg.get("deliverables_dir", "writing/deliverables"))
    deliverables_dir.mkdir(parents=True, exist_ok=True)

    data_pack = collect_report_data_pack(run)
    data_pack_path = run.subdir("writing") / str(cfg.get("data_pack_filename", "report_data_pack.v1.json"))
    data_pack_path.write_text(pretty_json(data_pack), encoding="utf-8")
    _event(run, "reporting.data_pack_written", {"path": _relative(run, data_pack_path), "actor": actor})

    deliverables: list[dict[str, Any]] = []
    errors: list[str] = []
    markdown_ref = _markdown_source(run)
    if markdown_ref is not None:
        deliverables.append(_completed_deliverable(run, "markdown", markdown_ref))
    else:
        deliverables.append({"kind": "markdown", "path": "writing/research_report.approved.md", "status": "skipped", "error": "approved markdown report not found"})

    _run_writer(
        run=run,
        kind="excel",
        path=deliverables_dir / str(_format_config(cfg, "excel").get("filename", "results_workbook.xlsx")),
        data_pack=data_pack,
        writer=write_results_workbook,
        deliverables=deliverables,
        errors=errors,
    )
    _run_writer(
        run=run,
        kind="word",
        path=deliverables_dir / str(_format_config(cfg, "word").get("filename", "research_report.docx")),
        data_pack=data_pack,
        writer=write_research_docx,
        deliverables=deliverables,
        errors=errors,
    )
    _run_writer(
        run=run,
        kind="powerpoint",
        path=deliverables_dir / str(_format_config(cfg, "powerpoint").get("filename", "research_deck.pptx")),
        data_pack=data_pack,
        writer=write_research_deck,
        deliverables=deliverables,
        errors=errors,
    )

    qa_status = _qa_status(run=run, data_pack=data_pack, deliverables=deliverables, errors=errors)
    metadata = {
        "schema": "report_bundle.v1",
        "project": run.project,
        "agent": "writing",
        "run_id": run.run_id,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "data_pack": _relative(run, data_pack_path),
        "deliverables": deliverables,
        "source_refs": data_pack.get("source_refs", []),
        "qa_status": qa_status,
        "generation_errors": errors,
    }
    body = _bundle_body(run=run, metadata=metadata, data_pack=data_pack)
    ref = ArtifactStore(run).write_metadata(
        metadata=metadata,
        body=body,
        expected_schema="report_bundle.v1",
    )
    _event(
        run,
        "reporting.bundle_verified",
        {
            "manifest": _relative(run, ref.path),
            "status": qa_status["status"],
            "actor": actor,
        },
    )
    return {
        "exists": True,
        "manifest": _relative(run, ref.path),
        "metadata": metadata,
        "body": body,
    }


def read_latest_report_bundle(run: RunHandle) -> dict[str, Any] | None:
    ref = ArtifactStore(run).latest(agent_dir="writing", stem="report_bundle")
    if ref is None:
        return None
    parsed = parse(ref.path.read_text(encoding="utf-8"))
    return {
        "exists": True,
        "manifest": _relative(run, ref.path),
        "metadata": parsed.metadata,
        "body": parsed.body,
    }


def _run_writer(
    *,
    run: RunHandle,
    kind: str,
    path: Path,
    data_pack: dict[str, Any],
    writer: Any,
    deliverables: list[dict[str, Any]],
    errors: list[str],
) -> None:
    _event(run, "reporting.deliverable_started", {"kind": kind, "path": _relative(run, path)})
    try:
        writer(path, data_pack)
        item = _completed_deliverable(run, kind, path)
        deliverables.append(item)
        _event(run, "reporting.deliverable_completed", item)
    except Exception as exc:  # pragma: no cover - generation failures are recorded in manifest
        message = f"{kind}: {exc}"
        logger.warning("report deliverable generation failed: run={} {}", run.run_id, message)
        errors.append(message)
        item = {"kind": kind, "path": _relative(run, path), "status": "failed", "error": str(exc)}
        deliverables.append(item)
        _event(run, "reporting.deliverable_failed", item)


def _qa_status(
    *,
    run: RunHandle,
    data_pack: dict[str, Any],
    deliverables: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    for item in deliverables:
        status = str(item.get("status", "failed"))
        path = run.root / str(item.get("path", ""))
        if status != "completed":
            checks.append({"name": f"{item.get('kind')}.generated", "status": status, "detail": str(item.get("error", ""))})
            continue
        if item.get("kind") in {"excel", "word", "powerpoint"}:
            checks.append(_zip_check(name=f"{item.get('kind')}.zip_structure", path=path))
        else:
            checks.append({"name": f"{item.get('kind')}.source", "status": "passed", "detail": str(item.get("path", ""))})
    for reason in data_pack.get("degraded_reasons", []):
        checks.append({"name": "input.degraded", "status": "degraded", "detail": str(reason)})
    if errors:
        status = "failed"
    elif data_pack.get("degraded"):
        status = "degraded"
    elif any(check["status"] not in {"passed", "skipped"} for check in checks):
        status = "degraded"
    else:
        status = "passed"
    return {"status": status, "checks": checks}


def _zip_check(*, name: str, path: Path) -> dict[str, str]:
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
        if bad:
            return {"name": name, "status": "failed", "detail": f"corrupt member: {bad}"}
        return {"name": name, "status": "passed", "detail": path.name}
    except zipfile.BadZipFile as exc:
        return {"name": name, "status": "failed", "detail": str(exc)}


def _bundle_body(*, run: RunHandle, metadata: dict[str, Any], data_pack: dict[str, Any]) -> str:
    summary_raw = data_pack.get("summary")
    summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
    lines = [
        f"# Report Bundle for {run.run_id}",
        "",
        f"- QA status: {metadata['qa_status']['status']}",
        f"- Experiments: {summary.get('experiment_count', 0)}",
        f"- Primary metric: {summary.get('primary_metric', 'n/a')}",
        "",
        "## Deliverables",
    ]
    for item in metadata["deliverables"]:
        lines.append(f"- {item['kind']}: {item['status']} - {item.get('path', '')}")
    if data_pack.get("degraded_reasons"):
        lines.extend(["", "## Degraded Inputs"])
        lines.extend(f"- {reason}" for reason in data_pack["degraded_reasons"])
    return "\n".join(lines) + "\n"


def _completed_deliverable(run: RunHandle, kind: str, path: Path) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": _relative(run, path),
        "status": "completed",
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def _markdown_source(run: RunHandle) -> Path | None:
    approved = run.subdir("writing") / "research_report.approved.md"
    if approved.exists():
        return approved
    versions = sorted(run.subdir("writing").glob("research_report.v*.md"), key=lambda p: p.name)
    return versions[-1] if versions else None


def _reporting_config() -> dict[str, Any]:
    path = repo_root() / "configs" / "reporting.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    reporting = raw.get("reporting", {})
    return reporting if isinstance(reporting, dict) else {}


def _format_config(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    formats = cfg.get("formats", {})
    if not isinstance(formats, dict):
        return {}
    raw = formats.get(name, {})
    return raw if isinstance(raw, dict) else {}


def _event(run: RunHandle, event: str, payload: dict[str, Any]) -> None:
    run.write_event(
        "reporting_events",
        {
            "event": event,
            "run_id": run.run_id,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            **payload,
        },
    )


def _relative(run: RunHandle, path: Path) -> str:
    try:
        return path.relative_to(run.root).as_posix()
    except ValueError:
        return path.as_posix()


def bundle_to_json(bundle: dict[str, Any] | None) -> str:
    return json.dumps(bundle or {"exists": False}, ensure_ascii=False, indent=2, default=str)
