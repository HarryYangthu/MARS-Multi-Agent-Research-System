"""Turn a report data pack into a presentation narrative model.

Everything shown is extracted from the approved report text or recorded
metrics; nothing is invented. Missing pieces degrade to neutral placeholders.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SECTION_RULES: list[tuple[str, str]] = [
    ("verdict", r"达标判定"),
    ("purpose", r"摘要|研究目的|问题定义|可证伪假设"),
    ("principle", r"方法|原理|建模|自洽|改动"),
    ("design", r"实验设置|执行矩阵|变量控制|对照|预算说明|复用判定"),
    ("results", r"实际结果|实测|净差|判读"),
    ("evidence", r"证据链|复现"),
    ("limitations", r"局限|归因边界|下一步"),
]


@dataclass
class ExperimentCard:
    experiment_id: str
    role: str
    note: str = ""


@dataclass
class PathStep:
    title: str
    note: str


@dataclass
class Narrative:
    title: str
    report_title: str
    task: str
    run_id: str
    created: str
    purpose: str
    hypothesis: str
    principle_text: str
    design_text: str
    results_text: str
    verdict_text: str
    limitations_text: str
    experiments: list[ExperimentCard] = field(default_factory=list)
    paths: list[PathStep] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    curves: list[dict[str, Any]] = field(default_factory=list)
    family_terms: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _plain(block: dict[str, Any]) -> str:
    """Extract visible text from a mistune AST token (raw leaves + children)."""
    if block.get("type") in {"softbreak", "linebreak"}:
        return "\n"
    parts: list[str] = [str(block.get("raw", ""))]
    if block.get("type") == "table":
        rows: list[list[str]] = []
        for section in block.get("children", []):
            if section.get("type") == "table_head":
                rows.append([" ".join(_plain(c) for c in cell.get("children", [])) for cell in section.get("children", [])])
            else:
                rows.extend([[" ".join(_plain(c) for c in cell.get("children", [])) for cell in row.get("children", [])] for row in section.get("children", [])])
        return "\n".join(" | ".join(r) for r in rows)
    for child in block.get("children", []) or []:
        parts.append(_plain(child))
    return "".join(parts)


def _sections(blocks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Split report blocks into buckets keyed by heading keywords."""
    buckets: dict[str, list[str]] = {key: [] for key, _ in _SECTION_RULES}
    buckets.setdefault("other", [])
    current: list[list[str]] = [buckets["other"]]
    for block in blocks:
        if block.get("type") == "heading":
            heading = _plain(block)
            level = int(block.get("attrs", {}).get("level", 2))
            target = buckets["other"]
            if level <= 3:
                for key, pattern in _SECTION_RULES:
                    if re.search(pattern, heading):
                        target = buckets[key]
                        break
            current = [target]
            continue
        text = _plain(block)
        if text.strip():
            current[0].append(text)
    return buckets


def _role_of(experiment_id: str) -> tuple[str, str]:
    name = str(experiment_id).lower()
    if "baseline" in name or name.startswith("a_"):
        return "基线（对照锚点）", "同条件重跑，提供参数量与性能分母"
    if "k1" in name or "degenerate" in name:
        return "退化对照", "隔离结构改动本身的效应"
    if "3term" in name:
        return "本轮候选", "在 2 项基础上追加带外镜像族"
    if "2term" in name or "capp" in name.replace("ca_pp", "capp"):
        return "上轮候选（对照）", "载波分辨 2 项配置"
    return "实验臂", ""


def _family_terms(text: str) -> list[str]:
    """Best-effort extraction of PP family terms from the report method text."""
    terms: list[str] = []
    for match in re.finditer(r"2c[01]\s*[−–-]\s*c[01]", text):
        token = match.group(0).replace(" ", "").replace("−", "−")
        if token not in terms:
            terms.append(token)
    if re.search(r"full|锚点", text) and "full（0 阶锚点）" not in terms:
        terms.insert(0, "full（0 阶锚点）")
    return terms[:4]


def build_narrative(pack: dict[str, Any], blocks: list[dict[str, Any]]) -> Narrative:
    sections = _sections(blocks)
    metrics = [row for row in pack.get("metrics", []) if isinstance(row, dict)]
    summary = pack.get("summary", {}) if isinstance(pack.get("summary"), dict) else {}
    question = str(summary.get("question") or pack.get("task") or "")
    purpose = "\n\n".join(sections.get("purpose", [])[:6]).strip() or question
    hypothesis = ""
    purpose_lines = sections.get("purpose", [])
    for index, line in enumerate(purpose_lines):
        if re.search(r"假设", line) and len(line.strip()) < 30:
            # heading line like "1.2 本轮唯一可证伪假设": the statement follows it
            for follower in purpose_lines[index + 1:]:
                if len(follower.strip()) >= 20:
                    hypothesis = follower.strip()
                    break
            if hypothesis:
                break
    if not hypothesis:
        for line in purpose_lines:
            if re.search(r"假设", line) and len(line.strip()) >= 20:
                hypothesis = line.strip()
                break
    experiments = []
    for row in metrics:
        role, note = _role_of(row.get("experiment_id", ""))
        experiments.append(ExperimentCard(str(row.get("experiment_id", "")), role, note))
    paths = [PathStep(card.experiment_id, card.note) for card in experiments]
    principle_text = "\n\n".join(sections.get("principle", [])[:10]).strip()
    report_title = ""
    for block in blocks:
        if block.get("type") == "heading" and int(block.get("attrs", {}).get("level", 9)) == 1:
            report_title = _plain(block).strip()
            break
    return Narrative(
        title=str(pack.get("task") or "研究报告"),
        report_title=report_title or str(pack.get("task") or "研究报告"),
        task=question,
        run_id=str(pack.get("run_id", "")),
        created=str(pack.get("created_at", "")),
        purpose=purpose,
        hypothesis=hypothesis,
        principle_text=principle_text or "报告未包含独立方法章节，详见结果表与证据附录。",
        design_text="\n\n".join(sections.get("design", [])[:10]).strip(),
        results_text="\n\n".join(sections.get("results", [])[:12]).strip(),
        verdict_text="\n\n".join(sections.get("verdict", [])[:8]).strip(),
        limitations_text="\n\n".join(sections.get("limitations", [])[:10]).strip(),
        experiments=experiments,
        paths=paths,
        metrics=metrics,
        curves=[c for c in pack.get("curves", []) if isinstance(c, dict)],
        family_terms=_family_terms(principle_text + "\n" + "\n".join(sections.get("design", []))),
        summary=summary,
    )
