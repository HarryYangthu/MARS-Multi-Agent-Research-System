"""Save report Markdown/images; convert requested Office formats on demand."""
from __future__ import annotations

import json
import hashlib
import zipfile
from uuid import uuid4
from xml.etree import ElementTree
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
from app.harness.persistence import path_lock
from app.harness.runtime.project_scope import safe_scope_path
from app.harness.schema.validator import validate_document


def generate_report_bundle(run: RunHandle, *, actor: str = "system", formats: tuple[str, ...] = ()) -> dict[str, Any]:
    with path_lock(run.root / "writing/.report_export.lock"):
        return _generate_report_bundle(run, actor=actor, formats=formats)


def _generate_report_bundle(run: RunHandle, *, actor: str, formats: tuple[str, ...]) -> dict[str, Any]:
    cfg = _reporting_config()
    if not cfg.get("enabled", True):
        raise ValueError("报告导出已在配置中关闭")
    if any(kind not in {"excel", "word", "powerpoint"} for kind in formats):
        raise ValueError("仅支持 Excel、Word、PPT 格式")
    if any(not _format_config(cfg, kind).get("enabled", True) for kind in formats):
        raise ValueError("请求的报告格式已在配置中关闭")
    approved = safe_scope_path(run.root, "writing/research_report.approved.md", must_exist=True)
    if not approved.is_file():
        raise ValueError("请先审核并批准研究报告，再生成导出文件")
    validation = validate_document(approved.read_text(encoding="utf-8"), expected_schema="report.v1")
    if not validation.valid or validation.metadata.get("project") != run.project:
        raise ValueError("已审核报告格式或项目身份不正确，未生成导出文件")
    deliverables_dir = safe_scope_path(run.root, str(Path(str(cfg.get("deliverables_dir", "writing/deliverables"))) / ".export_anchor")).parent / uuid4().hex
    deliverables_dir.mkdir(parents=True, exist_ok=True)

    previous = read_latest_report_bundle(run)
    data_pack = collect_report_data_pack(run)
    images = _save_images(run, deliverables_dir, data_pack)
    data_pack_path = deliverables_dir / Path(str(cfg.get("data_pack_filename", "report_data_pack.v1.json"))).name
    data_pack_path.write_text(pretty_json(data_pack), encoding="utf-8")
    _event(run, "reporting.data_pack_written", {"path": _relative(run, data_pack_path), "actor": actor})

    deliverables: list[dict[str, Any]] = []
    errors: list[str] = []
    markdown_ref = _markdown_source(run)
    if markdown_ref is not None:
        snapshot = deliverables_dir / "research_report.md"
        text = markdown_ref.read_text(encoding="utf-8")
        if images:
            text += "\n\n## 已保存的关键图片\n\n" + '\n\n'.join(
                f"{'!' if Path(item['path']).suffix.lower() != '.pdf' else ''}[已记录的实验图]({quote('images/' + Path(item['path']).name)})\n\n来源：`{item['source_path']}` · SHA-256：`{item['sha256']}`"
                for item in images)
            text += "\n"
        snapshot.write_text(text, encoding="utf-8")
        deliverables.append(_completed_deliverable(run, "markdown", snapshot))
    else:
        deliverables.append({"kind": "markdown", "path": "writing/research_report.approved.md", "status": "skipped", "error": "approved markdown report not found"})

    archive_path = deliverables_dir / "report_materials.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in [*deliverables, *images]:
            path = safe_scope_path(run.root, item["path"], must_exist=True)
            archive.write(path, path.relative_to(deliverables_dir).as_posix())
    materials_archive = _completed_deliverable(run, "markdown_archive", archive_path)

    reusable = _reusable_formats(run, previous, data_pack)
    writers = [("excel", "results_workbook.xlsx", write_results_workbook),
               ("word", "research_report.docx", write_research_docx),
               ("powerpoint", "research_deck.pptx", write_research_deck)]
    for kind, filename, writer in writers:
        if kind in formats:
            _run_writer(run=run, kind=kind, path=deliverables_dir / _filename(cfg, kind, filename),
                        data_pack=data_pack, writer=writer, deliverables=deliverables, errors=errors)
        elif kind in reusable:
            deliverables.append(reusable[kind])

    qa_status = _qa_status(run=run, data_pack=data_pack, deliverables=deliverables, errors=errors)
    if hashlib.sha256(approved.read_bytes()).hexdigest() != data_pack["report_source_sha256"]:
        raise ValueError("报告在导出期间发生修改，请重新生成")
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
        "generator": "office_editable",
        "report_source_sha256": data_pack["report_source_sha256"],
        "materials_saved": True,
        "images": images,
        "materials_archive": materials_archive,
        "source_hashes": data_pack["source_hashes"],
        "requested_formats": list(dict.fromkeys(formats)),
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
        "current": True,
    }


def _save_images(run: RunHandle, folder: Path, pack: dict[str, Any]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for plot in pack.get("plots", []):
        source_ref = str(plot["path"])
        source = safe_scope_path(run.root, source_ref, must_exist=True)
        data = source.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha != pack["source_hashes"].get(source_ref):
            raise ValueError("关键图片在保存时发生修改，请重试")
        target = folder / "images" / f"{sha[:12]}-{source.name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        item = _completed_deliverable(run, "image", target)
        item["source_path"] = source_ref
        images.append(item)
        plot["source_path"], plot["path"] = source_ref, item["path"]
    return images


def _reusable_formats(run: RunHandle, previous: dict[str, Any] | None, pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not previous or not previous.get("current"):
        return {}
    meta = previous["metadata"]
    if meta.get("source_hashes") != pack["source_hashes"]:
        return {}
    reusable: dict[str, dict[str, Any]] = {}
    for item in meta.get("deliverables", []):
        if item.get("kind") not in {"excel", "word", "powerpoint"} or item.get("status") != "completed":
            continue
        try:
            path = safe_scope_path(run.root, item["path"], must_exist=True)
            if hashlib.sha256(path.read_bytes()).hexdigest() == item.get("sha256"):
                reusable[item["kind"]] = dict(item)
        except (ValueError, OSError):
            continue
    return reusable


def read_latest_report_bundle(run: RunHandle) -> dict[str, Any] | None:
    ref = ArtifactStore(run).latest(agent_dir="writing", stem="report_bundle")
    if ref is None:
        return None
    parsed = parse(ref.path.read_text(encoding="utf-8"))
    approved = run.subdir("writing") / "research_report.approved.md"
    current = hashlib.sha256(approved.read_bytes()).hexdigest() if approved.is_file() else None
    return {
        "exists": True,
        "manifest": _relative(run, ref.path),
        "metadata": parsed.metadata,
        "body": parsed.body,
        "current": parsed.metadata.get("generator") == "office_editable" and current == parsed.metadata.get("report_source_sha256"),
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
    enabled: bool = True,
) -> None:
    if not enabled:
        deliverables.append({"kind": kind, "path": _relative(run, path), "status": "skipped", "error": "已在配置中关闭"})
        return
    _event(run, "reporting.deliverable_started", {"kind": kind, "path": _relative(run, path)})
    try:
        writer(path, data_pack)
        check = _zip_check(name=kind, path=path)
        if check["status"] != "passed":
            raise ValueError(check["detail"])
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
    if errors or any(check["status"] == "failed" for check in checks):
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
            for member in zf.namelist():
                if member.endswith((".xml", ".rels")):
                    ElementTree.fromstring(zf.read(member))
            required = {".xlsx": "xl/workbook.xml", ".docx": "word/document.xml", ".pptx": "ppt/slideMasters/slideMaster1.xml"}
            if path.suffix not in required or required[path.suffix] not in zf.namelist():
                raise ValueError("Office document part missing")
        if bad:
            return {"name": name, "status": "failed", "detail": f"corrupt member: {bad}"}
        return {"name": name, "status": "passed", "detail": path.name}
    except (zipfile.BadZipFile, ElementTree.ParseError, ValueError) as exc:
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
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
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


def _filename(cfg: dict[str, Any], kind: str, default: str) -> str:
    name = str(_format_config(cfg, kind).get("filename", default))
    if not name or Path(name).name != name or "\\" in name or Path(name).suffix != Path(default).suffix:
        raise ValueError("报告文件名必须为对应 Office 格式的单个文件名")
    return name


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
        return path.resolve().relative_to(run.root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def bundle_to_json(bundle: dict[str, Any] | None) -> str:
    return json.dumps(bundle or {"exists": False}, ensure_ascii=False, indent=2, default=str)
