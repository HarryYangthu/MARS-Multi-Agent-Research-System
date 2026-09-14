"""Read-only, run-bound views of research evidence and context snapshots."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import parse
from app.harness.tools.search.source_fetch import resource_key, source_failure_summary


class IdeaMaterial(BaseModel):
    id: str
    kind: str
    title: str
    status: str
    description: str = ""
    provenance: str = ""
    source_url: str = ""
    source_id: str = ""
    archive_available: bool = False
    source_type: str = ""
    decision: str = ""
    reason: str = ""
    transfer: str = ""
    read_windows: list[str] = Field(default_factory=list)
    preview_available: bool = False
    text: str = Field(default="", exclude=True)


class IdeaMaterialsView(BaseModel):
    run_id: str
    items: list[IdeaMaterial]
    warnings: list[str]


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _url(value: Any) -> str:
    url = str(value or "")
    try:
        return url if urlparse(url).scheme in {"http", "https"} else ""
    except ValueError:
        return ""


def _text(root: Path, path: Path, warnings: list[str]) -> str:
    if not path.exists():
        return ""
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        warnings.append(f"无法读取越界材料：{path.name}")
        return ""
    if path.stat().st_size > 2_000_000:
        warnings.append(f"材料过大，未在页面加载：{path.name}")
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _json(root: Path, path: Path, warnings: list[str]) -> Any:
    text = _text(root, path, warnings)
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        warnings.append(f"材料暂不可解析：{path.name}")
        return None


def _id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _document(root: Path, path: Path, title: str, kind: str, warnings: list[str]) -> IdeaMaterial | None:
    text = _text(root, path, warnings)
    if not text:
        return None
    relative = path.relative_to(root).as_posix()
    return IdeaMaterial(id=_id(relative), title=title, kind=kind, status="已保存",
                        provenance=relative, text=text, preview_available=True)


def collect_idea_materials(root: Path, run_id: str) -> IdeaMaterialsView:
    """Use saved observations; never reread live baseline code as historical input."""
    warnings: list[str] = []
    items: list[IdeaMaterial] = []
    folder_context = _object(_json(root, root / "input/folder_context.v1.json", warnings))
    folder_valid = folder_context.get("sha256") == digest({k: v for k, v in folder_context.items() if k != "sha256"})
    for file in _rows(folder_context.get("files")):
        content = file.get("content")
        if isinstance(content, str):
            relative = str(file.get("path") or "")
            items.append(IdeaMaterial(id="folder-" + _id(relative), kind="context", title=relative,
                status="已冻结上下文" if folder_valid and file.get("sha256") == digest(content) else "快照校验失败",
                description="随项目自动加载的背景与约定，来自本次任务冻结的版本。",
                provenance=str(folder_context.get("folder") or "") + "/" + relative,
                text=content, preview_available=True))
    knowledge = _object(_json(root, root / "input/project_knowledge.v1.json", warnings))
    content = knowledge.get("content")
    if isinstance(content, str) and content:
        valid = knowledge.get("sha256") == digest(content)
        project = str(knowledge.get("project") or "项目").upper()
        items.append(IdeaMaterial(id="project-knowledge", kind="context", title=f"{project} 背景与研究经验",
                                  status="已载入快照" if valid else "快照校验失败",
                                  description="本次任务冻结的公共背景全文；生成与评审使用同一份快照。",
                                  provenance=f"{knowledge.get('source', '')} · SHA256 {knowledge.get('sha256', '')}",
                                  text=content, preview_available=True))
    task = _document(root, root / "input/user_request.md", "本次研究任务", "context", warnings)
    if task:
        items.append(task)
    context = _document(root, root / "context/idea_context_snapshot.v2.md", "上下文装载记录", "context", warnings)
    if context:
        context.description = "上下文编译记录；背景全文和实际代码阅读内容见各自卡片。"
        items.append(context)

    # Each read has its own identity/window; multiple partial reads never imply a full file read.
    for path in sorted((root / "agent_traces/idea").glob("*/tools/*.json")):
        call = _object(_json(root, path, warnings))
        output, args = _object(call.get("output")), _object(call.get("args"))
        tool = str(call.get("tool") or "")
        if tool == "code.repo_reader":
            text = str(output.get("content") or "")
            window = f"字符 {output.get('char_start', 0)}–{output.get('char_end', len(text))} / {output.get('total_chars', '?')}"
            items.append(IdeaMaterial(id=_id(path.relative_to(root).as_posix()), kind="code",
                                      title=str(args.get("path") or output.get("path") or "代码读取"),
                                      status=("已读取片段" if output.get("truncated") else "已读取") if call.get("ok") else "读取失败",
                                      description=window if call.get("ok") else str(call.get("error") or "未返回内容"),
                                      provenance=path.relative_to(root).as_posix(), text=text, preview_available=bool(text)))
        elif tool.startswith("search.") and tool != "search.fetch_sources":
            hits = _rows(output.get("hits") or output.get("results") or output.get("papers"))
            query = str(args.get("query") or args.get("q") or "")
            body = f"# 检索记录\n\n检索词：{query}\n\n工具：{tool}\n\n"
            body += "\n\n".join(f"## {hit.get('title') or '未命名记录'}\n\n" + json.dumps(hit, ensure_ascii=False, indent=2)
                                  for hit in hits)
            if not hits:
                body += json.dumps(output, ensure_ascii=False, indent=2)
            items.append(IdeaMaterial(id=_id(path.relative_to(root).as_posix()), kind="search", title=query or tool,
                                      status="检索完成" if call.get("ok") else "检索失败",
                                      description=f"{tool} · {len(hits)} 条记录" if call.get("ok") else str(call.get("error") or "无结果"),
                                      provenance=path.relative_to(root).as_posix(), text=body, preview_available=True))

    index = root / "idea/research/downloads/source_fetch_index.v1.json"
    papers: dict[str, IdeaMaterial] = {}
    for row in _rows(_json(root, index, warnings)):
        key = resource_key(str(row.get("download_url") or row.get("url") or ""))
        paper = papers.setdefault(key, IdeaMaterial(id=_id(key), kind="paper", title=str(row.get("title") or "未命名资料"),
                                                   status="未获取正文", source_url=_url(row.get("url"))))
        if row.get("archive_complete"):
            archive = Path(str(row.get("download_path") or ""))
            if archive.resolve().is_relative_to(index.parent.resolve()) and archive.is_file():
                paper.archive_available = True
                paper.source_id = str(row.get("source_id") or "")
                paper.source_type = str(row.get("source_type") or "")
            else:
                warnings.append(f"归档缺失或越界：{paper.title}")
        if row.get("ok"):
            paper.status = "已下载 · 已有阅读记录" if paper.archive_available else "仅保留阅读记录"
            for page in _rows(row.get("visible_pages")):
                window = f"第 {page.get('page')} 页 · 字符 {page.get('char_start', 0)}–{page.get('char_end', '?')} / {page.get('full_page_text_chars', '?')}"
                if window not in paper.read_windows:
                    paper.read_windows.append(window)
                    paper.text += f"\n\n### {window}\n\n{page.get('text') or ''}"
            if row.get("excerpt"):
                window = f"正文字符 {row.get('char_start', 0)}–{row.get('char_end', '?')} / {row.get('full_text_chars', '?')}"
                if window not in paper.read_windows:
                    paper.read_windows.append(window)
                    paper.text += f"\n\n### {window}\n\n{row['excerpt']}"
        else:
            if not paper.read_windows:
                paper.status = "已下载 · 阅读失败" if paper.archive_available else "获取失败"
            paper.description = source_failure_summary([row])
            if row.get("error_code") == "http_404":
                paper.description = "下载地址返回 HTTP 404，未找到正文文件（http_404）。可核对来源链接或选择其他全文入口。"
            elif row.get("error_code") == "http_403":
                paper.description = "来源返回 HTTP 403，当前全文入口拒绝访问。可改用其他公开全文入口。"
        paper.preview_available = bool(paper.text)

    # Select the latest actual proposal version, not a delivery draft from a failed attempt.
    proposals = [(int(match[1]), path) for path in (root / "idea").glob("idea_proposal.v*.md")
                 if (match := re.fullmatch(r"idea_proposal\.v(\d+)\.md", path.name))]
    if proposals:
        path = max(proposals)[1]
        try:
            metadata = parse(_text(root, path, warnings)).metadata
            research = _object(metadata.get("research_context"))
            body = "# 调研结论\n\n" + str(research.get("question") or "") + "\n\n## 选择原则\n\n"
            body += "\n".join(f"- {rule}" for rule in research.get("selection_principles", []))
            for source in _rows(research.get("sources")):
                source_id = str(source.get("source_id") or "")
                selected = next((p for p in papers.values() if source_id and p.source_id == source_id), None)
                key = resource_key(str(source.get("url") or ""))
                selected = selected or papers.get(key)
                if selected is None:
                    selected = IdeaMaterial(id=_id(key), kind="paper", title=str(source.get("title") or "未命名资料"),
                                         status="未获取正文", source_url=_url(source.get("url")))
                    papers[key] = selected
                paper = selected
                paper.decision, paper.reason, paper.transfer = (str(source.get(name) or "") for name in ("decision", "reason", "transfer"))
                body += f"\n\n## {paper.title}\n\n取舍：{paper.decision}\n\n理由：{paper.reason}\n\n方法：{source.get('method_summary', '')}\n\n迁移思路：{paper.transfer}\n\n限制：{source.get('limitations', '')}"
            body += f"\n\n## 停止调研的依据\n\n{research.get('stop_reason', '')}\n\n## 待核验问题\n\n"
            body += "\n".join(f"- {q}" for q in research.get("open_questions", []))
            if research:
                items.append(IdeaMaterial(id="research-conclusions", kind="research", title="调研结论与选文依据", status="已生成",
                                          provenance=path.relative_to(root).as_posix(), text=body, preview_available=True))
        except (ValueError, TypeError, AttributeError):
            warnings.append(f"方案调研部分暂不可解析：{path.name}")
    items[0:0] = list(papers.values())
    return IdeaMaterialsView(run_id=run_id, items=items, warnings=list(dict.fromkeys(warnings)))


def enrich_source_errors(root: Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Explain legacy generic errors from exact saved call arguments, without rewriting history."""
    generic = "source fetch failed; see individual errors"
    if not any(row.get("error") == generic for row in entries):
        return entries
    matches: dict[str, set[str]] = {}
    for path in (root / "agent_traces/idea").glob("*/tools/*.json"):
        call = _object(_json(root, path, []))
        if call.get("tool") == "search.fetch_sources" and call.get("error") == generic:
            key = json.dumps(call.get("args"), sort_keys=True)
            rows = _rows(_object(call.get("output")).get("sources"))
            if rows:
                matches.setdefault(key, set()).add(source_failure_summary(rows))
    result: list[dict[str, Any]] = []
    for entry in entries:
        errors = matches.get(json.dumps(entry.get("args"), sort_keys=True), set())
        if entry.get("error") == generic and len(errors) == 1:
            entry = {**entry, "error": next(iter(errors)), "original_error": generic}
        result.append(entry)
    return result
