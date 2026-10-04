"""Proposal-bound literature inventory; no invented reading or decision claims."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.agents.idea.literature_quality import discovered_sources, publication_key, verified_method_sources
from app.agents.idea.focused_runtime import load_focused_snapshot
from app.harness.agent_loop.context import reading_coverage_index
from app.harness.schema.frontmatter_parser import parse


def _load(root: Path, path: Path) -> dict[str, Any]:
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > 32_000_000:
        raise ValueError("研究记录缺失、越界或过大")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("研究记录格式无效")
    return result


def literature_evidence(root: Path, run_id: str, project: str) -> dict[str, Any]:
    versions = [(int(match[1]), path) for path in (root / "idea").glob("idea_proposal.v*.md")
                if (match := re.fullmatch(r"idea_proposal\.v(\d+)\.md", path.name))]
    proposal = max(versions)[1] if versions else None
    if proposal and (not proposal.resolve().is_relative_to(root.resolve()) or proposal.stat().st_size > 2_000_000):
        raise ValueError("研究方案越界或过大")
    text = proposal.read_text(encoding="utf-8") if proposal else ""
    metadata = parse(text).metadata if text else {}
    if text and metadata.get("project") != project:
        raise ValueError("研究方案与项目不一致")
    research = metadata.get("research_context", {})
    paths = sorted((root / "agent_traces/idea").glob("*/checkpoint.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    states = [(path, _load(root, path)) for path in paths]
    selected = next(((path, state) for path, state in states if text and state.get("candidate") == text
                     and state.get("status") == "passed"), None)
    selected = selected or (states[0] if not text and states else None)
    warnings: list[str] = []
    history = selected[1].get("history", []) if selected else []
    if text and selected is None:
        warnings.append("当前方案未找到匹配的通过记录，不能确认阅读统计。")
    candidates = discovered_sources(history)
    rows: dict[str, dict[str, Any]] = {}

    def add(url: str, title: str) -> dict[str, Any]:
        key = publication_key(url)
        return rows.setdefault(key, {"id": hashlib.sha256(key.encode()).hexdigest()[:20], "title": title,
            "url": url, "source_id": "", "reading_status": "unread", "decision": "", "reason": "",
            "method_summary": "", "limitations": "", "complete_pages": [], "error": ""})

    for candidate in candidates:
        add(candidate["url"], candidate["title"])
    coverage = {doc["source_id"]: [page["page"] for page in doc["pages"] if page["complete_extracted_text"]]
                for doc in reading_coverage_index(history)}
    actual_read: set[str] = set()
    for observation in history:
        if observation.get("tool") != "search.fetch_sources" or not isinstance(observation.get("output"), dict):
            continue
        for source in observation["output"].get("sources", []):
            url = str(source.get("download_url") or source.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            row = add(url, str(source.get("title") or "未命名文献"))
            sid = str(source.get("source_id") or "")
            path = Path(str(source.get("download_path") or ""))
            archived = bool(source.get("archive_complete") and path.resolve().is_relative_to(root.resolve())
                            and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == source.get("sha256"))
            visible = bool(source.get("excerpt") or any(page.get("text") for page in source.get("visible_pages", [])))
            if source.get("ok") and archived and visible:
                row.update(source_id=sid, reading_status="read", complete_pages=coverage.get(sid, []), error="")
                actual_read.add(row["id"])
            elif row["reading_status"] != "read":
                row["reading_status"] = "unverified" if source.get("ok") else "unavailable"
                row["error"] = str(source.get("error") or source.get("error_code") or "未取得可校验正文")[:1000]
    verified = verified_method_sources(research, history, root)
    for source in research.get("sources", []):
        sid = str(source.get("source_id") or "")
        selected_row: dict[str, Any] | None = next((item for item in rows.values() if sid and item["source_id"] == sid), None)
        selected_row = selected_row or rows.get(publication_key(str(source.get("url") or "")))
        if selected_row is None:
            # A proposal alone cannot add an apparently retrieved paper.
            warnings.append("方案引用尚无检索或正文凭据：" + str(source.get("title", "")))
            continue
        for field in ("decision", "reason", "method_summary", "limitations"):
            selected_row[field] = str(source.get(field) or "")
        if sid in verified:
            selected_row["reading_status"] = "method_complete"
    inventory = sorted(rows.values(), key=lambda row: (row["decision"] != "use", row["reading_status"] not in {"read", "method_complete"}, row["title"]))
    axes: dict[str, str] = {}
    if selected:
        try:
            axes = load_focused_snapshot(root, selected[0].parent.name).get("research_quality", {}).get("coverage_axes", {})
        except (OSError, ValueError):
            warnings.append("本次调研配置暂不可读取。")
    return {"run_id": run_id, "project": project, "proposal_version": proposal.stem.split(".")[-1] if proposal else "",
        "invocation": selected[0].parent.name if selected else "", "statistics_verified": selected is not None,
        "counts": {"candidates": len(candidates), "read": len(actual_read), "method_complete": len(verified),
            "adopted": sum(row["decision"] == "use" for row in inventory)},
        "sources": inventory, "coverage": [{**item, "label": axes.get(item["axis"], "研究问题")}
                                              for item in research.get("coverage", [])],
        "method_comparison": research.get("method_comparison", []), "stop_reason": research.get("stop_reason", ""),
        "quality_evaluated": bool(axes and selected and selected[1].get("status") == "passed"
                                  and selected[1].get("reflection_accepted") and research.get("coverage")
                                  and research.get("method_comparison")), "warnings": warnings}
