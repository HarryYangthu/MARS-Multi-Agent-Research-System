"""Render declared research choices from trusted reports without adding conclusions."""
from __future__ import annotations

import html
import re
from typing import Any, cast
from urllib.parse import quote, urlsplit

from app.agents.idea.publication_count import canonical_source, count_report_publications
from app.agents.idea.research_assessment import assessment_errors


def _text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}: nonempty text required")
    return value


def _objects(value: Any, location: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{location}: array of objects required")
    return cast(list[dict[str, Any]], value)


def _strings(value: Any, location: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{location}: array of text required")
    return [_text(item, location) for item in value]


def _escape(value: str) -> str:
    """Keep supplied text literal in paragraphs and table cells, including HTML."""
    escaped = re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", html.escape(value))
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _url(value: Any, location: str) -> str:
    text = _text(value, location)
    parsed = urlsplit(text)
    if (parsed.scheme not in {"https", "http"} or not parsed.netloc
            or any(character.isspace() or ord(character) < 32 for character in text)):
        raise ValueError(f"{location}: absolute HTTP(S) source URL required")
    return quote(text, safe=":/?#@&=+;%,$!-._~")


def _report_index(reports: list[dict[str, Any]]) -> tuple[
    dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]]
]:
    sources: dict[tuple[str, str], dict[str, Any]] = {}
    insights: dict[tuple[str, str], dict[str, Any]] = {}
    for index, item in enumerate(_objects(reports, "/reports")):
        location = f"/reports/{index}"
        delegation = _text(item.get("delegation_id"), location + "/delegation_id")
        report = item.get("report")
        if not isinstance(report, dict):
            raise ValueError(location + "/report: object required")
        _strings(report.get("selection_principles"), location + "/report/selection_principles")
        for gap in _objects(report.get("gaps"), location + "/report/gaps"):
            for field in ("id", "question"):
                _text(gap.get(field), location + "/report/gaps/" + field)
        for source in _objects(report.get("sources"), location + "/report/sources"):
            for field in ("source_id", "title", "selection_reason"):
                _text(source.get(field), location + "/report/sources/" + field)
            _url(source.get("url"), location + "/report/sources/url")
            if source.get("decision") not in {"use", "reject", "defer"}:
                raise ValueError(location + "/report/sources/decision: use, reject or defer required")
            key = (delegation, source["source_id"])
            if key in sources:
                raise ValueError(location + ": duplicate source identity")
            sources[key] = source
        for insight in _objects(report.get("insights"), location + "/report/insights"):
            for field in ("id", "source_id", "quote", "paper_finding", "transfer_idea"):
                _text(insight.get(field), location + "/report/insights/" + field)
            if type(insight.get("page")) is not int or insight["page"] < 1:
                raise ValueError(location + "/report/insights/page: positive page number required")
            _strings(insight.get("limitations"), location + "/report/insights/limitations")
            insight_source = sources.get((delegation, insight["source_id"]))
            if insight_source is None or insight_source["decision"] != "use":
                raise ValueError(location + "/report/insights/source_id: used report source required")
            key = (delegation, insight["id"])
            if key in insights:
                raise ValueError(location + ": duplicate insight identity")
            insights[key] = insight
    return sources, insights


def render_research_brief(metadata: dict[str, Any], reports: list[dict[str, Any]], *, reviewed: bool) -> str:
    """Render verified-loader reports; this function performs no provenance or model calls.

    ``reviewed`` records a model review only. The caller must first validate the
    proposal and load report evidence through the trusted delegation loader.
    """
    summary = _text(metadata.get("human_summary"), "/human_summary")
    if type(reviewed) is not bool:
        raise ValueError("/reviewed: boolean required")
    sources, insights = _report_index(reports)
    links = _objects(metadata.get("research_links"), "/research_links")
    for index, link in enumerate(links):
        for field in ("delegation_id", "insight_id", "method_spec_ref", "adaptation_reason"):
            _text(link.get(field), f"/research_links/{index}/{field}")
        if not link["method_spec_ref"].startswith("/method_spec/"):
            raise ValueError(f"/research_links/{index}/method_spec_ref: method reference required")
    errors = assessment_errors(metadata, reports, required=True)
    if errors:
        raise ValueError("Cannot render research brief: " + "; ".join(errors))
    assessment = metadata["research_assessment"]
    decisions = {(item["delegation_id"], item["source_id"]): item
                 for item in assessment["source_decisions"]}
    source_insights: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (delegation, _), insight in insights.items():
        source_insights.setdefault((delegation, insight["source_id"]), []).append(insight)
    source_links: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for link in links:
        insight = insights[(link["delegation_id"], link["insight_id"])]
        source_links.setdefault((link["delegation_id"], insight["source_id"]), []).append(link)
    read_count = len({canonical_source(sources[key]["url"]) for key in source_insights})
    linked_count = len({canonical_source(sources[key]["url"]) for key in source_links})
    read_publications = count_report_publications(reports, selected=set(source_insights))
    linked_publications = count_report_publications(reports, selected=set(source_links))
    unresolved = bool(read_publications.conflicts or linked_publications.conflicts)
    count_line = (f"成功研究报告中有引用页段的已读来源（去重）：{read_count} 篇；"
                  f"真正通过研究链接用于方案的来源（去重）：{linked_count} 篇。")
    if unresolved:
        count_line = (f"成功研究报告中有引用页段的来源 URL 记录（去重）：{read_count} 条；"
                      f"真正通过研究链接用于方案的来源 URL 记录（去重）：{linked_count} 条。")

    lines = [_escape(summary), "", "## 研究问题", "", _escape(assessment["task_question"]),
             "", "## 选择原则", ""]
    for item in assessment["selection_principles"]:
        lines.append(f"- {_escape(item['id'])}：{_escape(item['criterion'])}；任务依据：{_escape(item['task_basis'])}")
    lines += ["", "## 为何停止", "", _escape(assessment["stopping_reason"]), "",
              count_line,
              "统计仅覆盖已验证的成功报告及方案链接；页码表示实际引用页段，不表示全文阅读或全部检索、下载数量。",
              "模型审查状态：" + ("已完成模型审查。" if reviewed else "未标记为已完成模型审查。")
              + "该状态不代表实验结果或科学结论已验证。"]
    if unresolved:
        lines += [f"上述是按来源 URL 去重的记录数；按独立性计数规则可计入的已读论文为 {read_publications.count} 篇，"
                  f"用于方案的论文为 {linked_publications.count} 篇。",
                  "不同渠道的规范化完整标题相同，可能重复或独立性待澄清，未给予第二篇额度；"
                  "这不表示已合并出版物实体、正文或版本，也不改变历史审查判定。"]
    lines += ["", "## 逐篇论文与方案关系", "",
              "| 论文与来源 | 选择与任务关系 | 实际引用页码与提取内容 | 迁移限制与假设 | 方案位置与迁移理由 |",
              "|---|---|---|---|---|"]
    decision_names = {"adopt": "采纳", "exclude": "排除", "defer": "暂缓"}
    researcher_names = {"use": "使用", "reject": "排除", "defer": "暂缓"}
    for key, source in sources.items():
        rows = source_insights.get(key, [])
        decision = decisions.get(key)
        choice = f"研究员：{researcher_names[source['decision']]}；原始理由：{source['selection_reason']}"
        if decision is not None:
            choice += (f"\n方案决定：{decision_names[decision['decision']]}；理由：{decision['reason']}"
                       f"\n任务关系：{decision['task_relevance']}；对应原则：{'、'.join(decision['criterion_ids'])}")
        else:
            choice += "\n主 Agent 未列出采纳判断。"
        findings = [f"{row['id']}：第 {row['page']} 页；原文短摘：{row['quote']}；论文原结论：{row['paper_finding']}；"
                    f"迁移想法：{row['transfer_idea']}" for row in rows]
        limits = [f"{row['id']}：{'；'.join(row['limitations'])}" for row in rows]
        if decision is not None and decision["transfer_assumptions"]:
            limits.append("方案迁移假设：" + "；".join(decision["transfer_assumptions"]))
        mappings = [f"{link['insight_id']} → {link['method_spec_ref']}；{link['adaptation_reason']}"
                    for link in source_links.get(key, [])]
        citation = (f"[{_escape(source['title'])}]({_url(source['url'], '/sources/url')})"
                    f"<br>{_escape(key[0])} / {_escape(key[1])}")
        cells = [citation, _escape(choice), _escape("\n".join(findings)) if findings else "无引用页段记录。",
                 _escape("\n".join(limits)) if limits else "未记录迁移限制或假设。",
                 _escape("\n".join(mappings)) if mappings else "未通过研究链接用于方案。"]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## 未解决问题", ""]
    gaps = assessment["remaining_gaps"]
    lines += ["- " + _escape(gap) for gap in gaps] if gaps else ["未列出未解决问题。"]
    return "\n".join(lines) + "\n"
