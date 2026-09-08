"""Human-facing progress and a versioned downstream handoff for real Idea runs."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.agents.base import Artifact, RunRequest
from app.harness.agent_loop.executor import ProgressSink
from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.schema.frontmatter_parser import FrontmatterError, parse
from app.harness.schema.validator import validate_document


def resolve_pointer(document: dict[str, Any], pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ValueError("reference must be an absolute JSON pointer")
    value: Any = document
    for token in pointer[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise ValueError(f"invalid JSON pointer escape: {pointer}")
        key = token.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(value, list) and not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError("array indices must be canonical nonnegative integers")
            value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"reference does not resolve: {pointer}") from exc
    if value is None or value == "" or value == {} or value == []:
        raise ValueError(f"reference is empty: {pointer}")
    return value


def delivery_errors(metadata: dict[str, Any], scope: str, *, body: str | None = None) -> list[str]:
    errors: list[str] = []
    summary = metadata.get("human_summary")
    if not isinstance(summary, str) or not 8 <= len(summary.strip()) <= 240 or "\n" in summary:
        errors.append("/human_summary: write one plain-language paragraph of 1-2 sentences, 8-240 characters, explaining the change and intended benefit")
    elif len([s for s in re.split(r"[。！？!?]+|(?<!\d)\.(?!\d)", summary) if s.strip()]) > 2:
        errors.append("/human_summary: at most two sentences; move details into method_spec")
    if body is not None and isinstance(summary, str) and body.strip() != summary.strip():
        errors.append("/body: copy human_summary exactly as the body; keep all method definitions in structured metadata to prevent contradictory duplicate specifications")
    handoff = metadata.get("handoff")
    if not isinstance(handoff, dict):
        return errors + ["/handoff: required versioned downstream contract; follow the handoff schema"]
    if handoff.get("scope") != scope:
        errors.append("/handoff/scope: must match the requested scope")
    for i, change in enumerate(handoff.get("changes", [])):
        if not isinstance(change, dict):
            continue  # JSON Schema reports malformed objects first.
        try:
            pointer = str(change.get("spec_ref", ""))
            if not pointer.startswith("/method_spec/"):
                raise ValueError("spec_ref must reference the canonical method_spec")
            resolve_pointer(metadata, pointer)
        except ValueError as exc:
            errors.append(f"/handoff/changes/{i}: {exc}")
    for i, check in enumerate(handoff.get("verification_requirements", [])):
        if isinstance(check, dict):
            try:
                if check.get("decision_rule_ref") != "/decision_rule":
                    raise ValueError("use the single canonical /decision_rule")
                resolve_pointer(metadata, "/decision_rule")
            except ValueError as exc:
                errors.append(f"/handoff/verification_requirements/{i}: {exc}")
    if scope == "method_proposal":
        missing = handoff.get("required_context", [])
        kinds = {item.get("kind") for item in missing if isinstance(item, dict) and item.get("blocks_execution") is True}
        if "baseline_code" not in kinds or "data_description" not in kinds:
            errors.append("/handoff/required_context: method-only scope must name baseline_code and data_description as execution prerequisites; a symbolic model is not a real project input")
    return errors


def progress_message(event: dict[str, Any]) -> str:
    kind = event["kind"]
    if kind == "started":
        return "我会先核对研究目标和已有材料，再比较可行方向，形成一份可交给实验环节的方案。"
    if kind == "action":
        reason = str(event.get("reason") or "").strip()
        if reason and len(reason) <= 240 and "\n" not in reason and re.search(r"[\u4e00-\u9fff]", reason):
            return reason
        tool = str(event.get("tool", ""))
        descriptions = {"knowledge.kb_query": "查询相关历史记录", "knowledge.baseline_match": "查找可复用的基线记录",
                        "search.arxiv_search": "检索相关论文", "search.web_search": "搜索相关资料",
                        "search.local_docs": "查阅本地材料", "search.fetch_sources": "获取资料并读取指定页段",
                        "code.repo_reader": "阅读基线代码"}
        return "正在" + descriptions.get(tool, "执行研究工具 " + tool) + "。"
    if kind == "observation":
        return "已收到工具结果，将据此继续判断。" if event.get("ok") else "本次工具调用未成功，将根据错误信息决定下一步。"
    if kind == "candidate":
        try:
            summary = parse(str(event.get("text", ""))).metadata.get("human_summary")
        except FrontmatterError:
            summary = None
        if isinstance(summary, str) and summary.strip():
            return "候选方案，尚待验收：" + summary.strip()[:240]
        return "已形成候选方案，接下来检查格式、证据和下游所需信息。"
    if kind == "validation":
        return "结构与材料检查通过，继续完成本次验收。" if event.get("valid") else f"结构或材料检查发现 {len(event.get('issues', []))} 项问题，候选方案尚未通过。"
    if kind == "review":
        if event.get("accepted"):
            return "本轮模型审查未发现阻断问题；研究效果仍需实验验证。"
        issues = event.get("issues", [])
        detail = str(issues[0]) if issues and re.search(r"[\u4e00-\u9fff]", str(issues[0])) else "需要修订方案。"
        return f"审查发现 {len(issues)} 项待解决问题：" + detail[:220]
    return "本次方案生成已完成，正在整理交付材料。" if event.get("status") == "passed" else "本次运行已停止，未完成验收；进展和问题已保留。"


def progress_sink(request: RunRequest, invocation: str) -> ProgressSink:
    target = Path(str(request.extra["run_root"])) / "idea" / "progress.jsonl"

    async def emit(event: dict[str, Any]) -> None:
        message = progress_message(event)
        if event["kind"] == "started":
            labels = {"background": "研究背景", "baseline_code": "基线代码", "data_description": "数据说明",
                      "analysis_results": "已有分析", "metric_definition": "指标定义", "literature_notes": "参考资料"}
            supplied = [label for key, label in labels.items() if request.upstream_artifacts.get(key, "").strip()]
            if supplied:
                message = "已收到" + "、".join(supplied) + "。我会先核对目标与基线，再形成可验证的研究方案。"
        payload = {"id": uuid.uuid4().hex, "timestamp": datetime.now(timezone.utc).isoformat(),
                   "kind": event["kind"], "phase": event.get("phase", "act"),
                   "message": message, "agent": "idea", "project": request.project,
                   "run_id": str(request.extra.get("run_id", Path(str(request.extra["run_root"])).name)),
                   "invocation": invocation}
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        logger.info("IDEA_PROGRESS {}", payload["message"])
        if request.progress_sink is not None:
            try:
                await request.progress_sink(payload)
            except Exception as exc:
                logger.warning("Idea progress delivery failed after local persistence: {}", type(exc).__name__)
    return emit


def write_delivery(artifact: Artifact, request: RunRequest, *, invocation: str, reviewed: bool) -> Path:
    validation = validate_document(artifact.text, expected_schema="proposal.v1")
    errors = [error.message for error in validation.errors] + delivery_errors(
        validation.metadata, str(request.extra.get("scope", "method_proposal")), body=parse(artifact.text).body)
    if errors:
        raise ValueError("cannot publish an invalid Idea delivery: " + "; ".join(errors))
    reports: list[dict[str, Any]] = []
    research_brief: str | None = None
    if ("research_assessment" in validation.metadata
            or request.extra.get("idea_requirements", {}).get("require_research_dossier")
            or "idea_research_session" in request.runtime):
        from app.agents.idea.research_assessment import assessment_errors
        from app.agents.idea.research_brief import render_research_brief
        from app.agents.idea.research_delegate import verified_delegated_reports
        from app.agents.idea.research_links import research_link_errors
        reports = verified_delegated_reports(request)
        errors = assessment_errors(validation.metadata, reports, required=True)
        errors.extend(research_link_errors(validation.metadata, reports,
            min_sources=int(request.extra.get("idea_requirements", {}).get("min_sources", 1)),
            require_linked_sources=True))
        if not reviewed:
            errors.append("research assessment requires an accepted independent-context model review")
        if errors:
            raise ValueError("cannot publish unreviewed or inconsistent research decisions: " + "; ".join(errors))
        research_brief = render_research_brief(validation.metadata, reports, reviewed=reviewed)
    # A resumed invocation may produce another accepted revision. Keep each
    # export immutable so publishing it neither fails nor replaces earlier evidence.
    root = Path(str(request.extra["run_root"])) / "idea" / "deliveries" / invocation / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    (root / "proposal.md").write_text(artifact.text, encoding="utf-8")
    atomic_json(root / "proposal.json", validation.metadata)
    (root / "summary.txt").write_text(str(validation.metadata["human_summary"]) + "\n", encoding="utf-8")
    if research_brief is not None:
        (root / "research_brief.md").write_text(research_brief, encoding="utf-8")
        atomic_json(root / "research_evidence.json", {"schema": "idea.research_evidence.v1",
            "proposal_sha256": digest(artifact.text), "reports": reports,
            "scientific_validated": False})
    atomic_json(root / "acceptance.json", {"schema_valid": True, "delivery_contract_valid": True,
        "model_review_passed": reviewed, "scientific_validated": False, "simulation_executed": False,
        "research_decisions_checked": research_brief is not None,
        "proposal_sha256": digest(artifact.text), "scope": request.extra.get("scope", "method_proposal"),
        "execution_requires_context": any(c["blocks_execution"] for c in validation.metadata["handoff"]["required_context"])})
    return root
