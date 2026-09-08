"""Human-authored rendering contracts, not model output or tool-execution substitutes."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

import pytest

from app.agents.idea.research_brief import render_research_brief


def documents() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Declare display inputs only; these objects do not establish real provenance."""
    metadata: dict[str, Any] = {
        "human_summary": "这是一份人工编写的渲染契约示例，不包含任何实测收益。",
        "method_spec": {"candidate": "A declared candidate field for rendering."},
        "research_links": [{"delegation_id": "reading-a", "insight_id": "finding-a",
                            "method_spec_ref": "/method_spec/candidate",
                            "adaptation_reason": "此处仅展示结构化字段中的迁移理由原文。"}],
        "research_assessment": {
            "version": "idea.research_assessment.v1",
            "task_question": "人工定义的问题：方案如何满足声明的参数约束？",
            "selection_principles": [{"id": "budget", "criterion": "提供清楚可核算的参数定义",
                                      "task_basis": "人工声明的研究任务要求明确说明参数量。"}],
            "stopping_reason": "人工声明：现有资料足以定义候选方法，实验收益仍未知。",
            "remaining_gaps": ["人工声明：迁移能否提高任务表现仍需验证。"],
            "source_decisions": [{
                "delegation_id": "reading-a", "source_id": "paper-a", "decision": "adopt",
                "reason": "人工声明：该资料的计数定义用于候选方法。",
                "task_relevance": "人工声明：计数定义对应任务指定的参数约束。",
                "criterion_ids": ["budget"], "insight_ids": ["finding-a"],
                "transfer_assumptions": ["人工声明：原文计数单位适用于目标任务。"],
            }],
        },
    }
    reports: list[dict[str, Any]] = [{"delegation_id": "reading-a", "report": {
        "selection_principles": ["人工声明的研究员选文原则。"],
        "gaps": [{"id": "gap-a", "question": "人工声明的信息缺口。"}],
        "sources": [{"source_id": "paper-a", "title": "人工资料甲", "url": "https://example.org/paper-a",
                     "decision": "use", "selection_reason": "人工声明的研究员原始选择理由。"}],
        "insights": [{"id": "finding-a", "source_id": "paper-a", "page": 7,
                      "quote": "A human-authored excerpt for a rendering contract only.",
                      "paper_finding": "人工声明的论文原结论。", "transfer_idea": "人工声明的迁移想法。",
                      "limitations": ["人工声明的原方法适用条件。"]}],
    }}]
    return metadata, reports


def test_brief_preserves_declared_content_and_does_not_mutate_inputs() -> None:
    metadata, reports = documents()
    before = deepcopy((metadata, reports))
    brief = render_research_brief(metadata, reports, reviewed=False)
    assert brief.startswith(metadata["human_summary"] + "\n")
    assert [brief.index(title) for title in ("## 研究问题", "## 选择原则", "## 为何停止",
                                            "## 逐篇论文与方案关系", "## 未解决问题")] == sorted(
        brief.index(title) for title in ("## 研究问题", "## 选择原则", "## 为何停止",
                                         "## 逐篇论文与方案关系", "## 未解决问题"))
    for text in ("第 7 页", "人工声明的论文原结论。", "人工声明的迁移想法。",
                 "人工声明的原方法适用条件。", "此处仅展示结构化字段中的迁移理由原文。",
                 "https://example.org/paper-a", "原始选择理由", "方案迁移假设",
                 "原文短摘：A human\\-authored excerpt for a rendering contract only\\."):
        assert text in brief
    assert "/method\\_spec/candidate" in brief
    assert "已读来源（去重）：1 篇" in brief
    assert "用于方案的来源（去重）：1 篇" in brief
    assert (metadata, reports) == before


def test_repeated_readings_are_deduplicated_and_unused_reads_are_not_adoptions() -> None:
    metadata, reports = documents()
    duplicate = deepcopy(reports[0])
    duplicate["delegation_id"] = "reading-b"
    duplicate["report"]["sources"][0]["url"] += "?revision=2"
    duplicate["report"]["insights"][0]["page"] = 9
    reports.append(duplicate)
    second = deepcopy(reports[0])
    second["delegation_id"] = "reading-c"
    second["report"]["sources"][0]["url"] = "https://example.org/paper-b"
    reports.append(second)
    decision = metadata["research_assessment"]["source_decisions"][0]
    for delegation in ("reading-b", "reading-c"):
        excluded = {**deepcopy(decision), "delegation_id": delegation, "decision": "exclude",
                    "insight_ids": [], "transfer_assumptions": []}
        metadata["research_assessment"]["source_decisions"].append(excluded)
    brief = render_research_brief(metadata, reports, reviewed=True)
    assert "已读来源（去重）：2 篇" in brief
    assert "用于方案的来源（去重）：1 篇" in brief
    assert "第 7 页" in brief and "第 9 页" in brief
    assert "方案决定：排除" in brief


def test_researcher_rejected_source_has_no_invented_read_pages_or_adoption() -> None:
    metadata, reports = documents()
    reports[0]["report"]["sources"].append({
        "source_id": "paper-rejected", "title": "人工资料乙", "url": "https://example.org/rejected",
        "decision": "reject", "selection_reason": "人工声明：资料不覆盖任务所需的问题。",
    })
    brief = render_research_brief(metadata, reports, reviewed=False)
    row = next(line for line in brief.splitlines() if "人工资料乙" in line)
    assert "研究员：排除" in row and "资料不覆盖任务所需的问题" in row
    assert "主 Agent 未列出采纳判断" in row
    assert "无引用页段记录" in row and "第 7 页" not in row
    assert "已读来源（去重）：1 篇" in brief


def test_all_excluded_can_render_zero_adoptions_without_success_claim() -> None:
    metadata, reports = documents()
    metadata["research_links"] = []
    decision = metadata["research_assessment"]["source_decisions"][0]
    decision.update(decision="exclude", insight_ids=[], transfer_assumptions=[])
    metadata["research_assessment"]["remaining_gaps"] = []
    brief = render_research_brief(metadata, reports, reviewed=True)
    assert "用于方案的来源（去重）：0 篇" in brief
    assert "未列出未解决问题" in brief
    assert "已完成模型审查" in brief
    assert "不代表实验结果或科学结论已验证" in brief
    assert "实测成功" not in brief


def test_untrusted_text_cannot_inject_html_or_split_markdown_rows() -> None:
    metadata, reports = documents()
    hostile = '<img src=x onerror=alert(1)> | [unsafe](javascript:alert(1))\n# injected'
    metadata["human_summary"] = hostile
    reports[0]["report"]["sources"][0]["title"] = hostile
    reports[0]["report"]["sources"][0]["url"] = "https://example.org/a)|[bad](x)"
    reports[0]["report"]["insights"][0]["paper_finding"] = hostile
    brief = render_research_brief(metadata, reports, reviewed=False)
    assert "<img" not in brief and "&lt;img" in brief
    assert "\\[unsafe\\]" in brief and "\n# injected" not in brief
    assert "%29%7C%5Bbad%5D%28x%29" in brief
    rows = [line for line in brief.splitlines() if line.startswith("| ")]
    assert len(rows) == 2
    assert all(len(re.findall(r"(?<!\\)\|", row)) == 6 for row in rows)


@pytest.mark.parametrize("field", ["human_summary", "research_assessment", "research_links"])
def test_missing_metadata_fails_explicitly(field: str) -> None:
    metadata, reports = documents()
    del metadata[field]
    with pytest.raises(ValueError, match=field):
        render_research_brief(metadata, reports, reviewed=False)


@pytest.mark.parametrize("collection,field", [
    ("sources", "title"), ("sources", "url"), ("sources", "selection_reason"),
    ("insights", "page"), ("insights", "paper_finding"), ("insights", "transfer_idea"),
    ("insights", "limitations"), ("insights", "quote"),
])
def test_missing_report_content_is_not_replaced_with_generated_text(collection: str, field: str) -> None:
    metadata, reports = documents()
    del reports[0]["report"][collection][0][field]
    with pytest.raises(ValueError, match=field):
        render_research_brief(metadata, reports, reviewed=False)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,hello", "/relative", "https://example.org/\nbad"])
def test_unsafe_source_url_is_rejected(url: str) -> None:
    metadata, reports = documents()
    reports[0]["report"]["sources"][0]["url"] = url
    with pytest.raises(ValueError, match="URL"):
        render_research_brief(metadata, reports, reviewed=False)


def test_link_cannot_claim_an_unresolved_finding() -> None:
    metadata, reports = documents()
    metadata["research_links"][0]["insight_id"] = "missing"
    with pytest.raises(ValueError, match="does not resolve"):
        render_research_brief(metadata, reports, reviewed=False)


def test_source_adoption_cannot_contradict_links() -> None:
    metadata, reports = documents()
    metadata["research_links"] = []
    with pytest.raises(ValueError, match="exactly match"):
        render_research_brief(metadata, reports, reviewed=False)
