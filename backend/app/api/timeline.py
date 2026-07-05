"""Unified timeline endpoint for V2 workbench views."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import get_run_store
from app.storage.run_store import RunHandle

router = APIRouter(prefix="/api/timeline", tags=["timeline"])


class TimelineItem(BaseModel):
    id: str
    timestamp: str
    source: str
    kind: str
    title: str
    summary: str = ""
    status: str = ""
    agent: str = ""
    node: str = ""
    payload: dict[str, Any]


class WorkLogItem(BaseModel):
    id: str
    timestamp: str
    elapsed_seconds: float | None = None
    agent: str = ""
    kind: str
    status: str = ""
    title: str
    detail: str = ""
    next_action: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class WorkLogView(BaseModel):
    run_id: str
    project: str
    agent: str = ""
    status: str = ""
    started_at: str = ""
    latest_at: str = ""
    elapsed_seconds: float | None = None
    items: list[WorkLogItem]


@router.get("/runs/{run_id}", response_model=list[TimelineItem])
async def get_run_timeline(
    run_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[TimelineItem]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    items: list[TimelineItem] = []
    items.extend(_event_items(run.root / "events"))
    items.extend(_trace_items(run.root / "context" / "trace_manifest.v2.json"))
    items.extend(_context_manifest_items(run.root / "context"))
    items.sort(key=lambda item: (item.timestamp or "", item.id))
    return items[-limit:]


@router.get("/runs/{run_id}/worklog", response_model=WorkLogView)
async def get_run_worklog(
    run_id: str,
    agent: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
) -> WorkLogView:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    items = _worklog_items(run=run, agent_filter=agent.strip())
    items.sort(key=lambda item: (_parse_dt(item.timestamp) or datetime.min.replace(tzinfo=timezone.utc), item.id))
    started_at = _first_timestamp(items) or run.created_at
    latest_at = _latest_timestamp(items) or started_at
    elapsed = _elapsed_seconds(started_at, latest_at)
    return WorkLogView(
        run_id=run.run_id,
        project=run.project,
        agent=agent.strip(),
        status=_latest_agent_status(items),
        started_at=started_at,
        latest_at=latest_at,
        elapsed_seconds=elapsed,
        items=items[-limit:],
    )


def _event_items(events_dir: Path) -> list[TimelineItem]:
    if not events_dir.exists():
        return []
    items: list[TimelineItem] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for index, payload in enumerate(_read_jsonl(path), start=1):
            event = str(payload.get("event") or payload.get("kind") or path.stem)
            items.append(
                TimelineItem(
                    id=f"{path.stem}:{index}",
                    timestamp=str(payload.get("timestamp") or payload.get("created_at") or ""),
                    source=path.stem,
                    kind=_classify_event(event, path.stem),
                    title=event,
                    summary=_event_summary(event, payload),
                    status=str(payload.get("status") or payload.get("to_state") or ""),
                    agent=str(payload.get("agent") or ""),
                    node=str(payload.get("node") or payload.get("from_node") or ""),
                    payload=payload,
                )
            )
    return items


def _worklog_items(*, run: RunHandle, agent_filter: str = "") -> list[WorkLogItem]:
    items: list[WorkLogItem] = []
    start = _run_started_at(run)
    evaluation_rows = _read_jsonl(run.subdir("events") / "evaluation_events.jsonl")
    review_rows = _read_jsonl(run.subdir("hitl") / "review_log.jsonl")
    timestamp_hints = _worklog_timestamp_hints(evaluation_rows=evaluation_rows, review_rows=review_rows)

    items.append(
        WorkLogItem(
            id="0:run:start",
            timestamp=start,
            elapsed_seconds=0.0,
            agent="",
            kind="run",
            status="started",
            title="收到任务，启动真实工作流",
            detail=(
                f"entrypoint={run.entrypoint}，项目={run.project}。接下来按 UI/API/Bridge/Orchestrator "
                "路径进入对应 Agent。"
            ),
            next_action="装载项目规则、任务 prompt、上下文配置和代码仓入口。",
        )
    )

    agent_rows = _read_jsonl(run.subdir("events") / "agent_events.jsonl")
    last_agent_timestamp = start
    for index, payload in enumerate(agent_rows, start=1):
        if not _belongs_to_agent(payload, agent_filter):
            continue
        payload_with_time = dict(payload)
        if payload_with_time.get("timestamp"):
            last_agent_timestamp = str(payload_with_time["timestamp"])
        else:
            event_name = str(payload_with_time.get("event") or "")
            inferred_timestamp = (
                _next_agent_timestamp(agent_rows, index - 1)
                if event_name.startswith("idea.acceptance_report")
                else ""
            )
            payload_with_time["__worklog_inferred_timestamp"] = inferred_timestamp or last_agent_timestamp
        item = _agent_event_worklog(
            index=index,
            payload=payload_with_time,
            timestamp_hints=timestamp_hints,
        )
        if item is not None:
            items.append(item)

    for index, payload in enumerate(review_rows, start=1):
        if not _belongs_to_agent(payload, agent_filter):
            continue
        items.append(_review_worklog(index=index, payload=payload))

    for index, payload in enumerate(_read_jsonl(run.subdir("events") / "tool_calls.jsonl"), start=1):
        if not _belongs_to_agent(payload, agent_filter):
            continue
        items.append(_tool_call_worklog(index=index, payload=payload))

    for index, payload in enumerate(evaluation_rows, start=1):
        if not _belongs_to_agent(payload, agent_filter):
            continue
        items.append(_evaluation_worklog(index=index, payload=payload))

    for index, payload in enumerate(_read_jsonl(run.subdir("events") / "websocket_events.jsonl"), start=1):
        if not _belongs_to_agent(payload, agent_filter):
            continue
        item = _websocket_worklog(
            index=index,
            payload=payload,
            fallback_timestamp=start,
            timestamp_hints=timestamp_hints,
        )
        if item is not None:
            items.append(item)

    for index, item in enumerate(_context_manifest_worklog(run=run, agent_filter=agent_filter), start=1):
        items.append(item.model_copy(update={"id": f"context:{index}:{item.id}"}))

    items = _deduplicate_worklog(items)
    items.sort(key=lambda item: (_parse_dt(item.timestamp) or datetime.min.replace(tzinfo=timezone.utc), item.id))
    started_at = _first_timestamp(items) or start
    return [
        item.model_copy(update={"elapsed_seconds": _elapsed_seconds(started_at, item.timestamp)})
        for item in items
    ]


def _agent_event_worklog(
    *,
    index: int,
    payload: dict[str, Any],
    timestamp_hints: dict[str, dict[str, str]],
) -> WorkLogItem | None:
    reason = str(payload.get("reason") or "")
    timestamp = str(
        payload.get("timestamp")
        or payload.get("__worklog_inferred_timestamp")
        or (timestamp_hints.get("revision_reason") or {}).get(reason)
        or ""
    )
    agent = str(payload.get("agent") or payload.get("node") or "")
    event = str(payload.get("event") or "")
    if payload.get("to_state"):
        to_state = str(payload.get("to_state") or "")
        from_state = str(payload.get("from_state") or "")
        if to_state == "running":
            return WorkLogItem(
                id=f"agent:{index}:running:{agent}",
                timestamp=timestamp,
                agent=agent,
                kind="state",
                status=to_state,
                title=f"{_agent_label(agent)} 开始处理",
                detail=f"状态从 {from_state or 'unknown'} 进入 running，开始读取上下文、代码仓和可用工具。",
                next_action="先收集证据，再生成或修订可审核产物。",
            )
        if to_state == "waiting_review":
            return WorkLogItem(
                id=f"agent:{index}:waiting:{agent}",
                timestamp=timestamp,
                agent=agent,
                kind="state",
                status=to_state,
                title=f"{_agent_label(agent)} 完成本轮产物",
                detail="已进入人工审核状态，页面会展示最新版 Markdown 产物和评价结果。",
                next_action="等待用户批准，或带着反馈返工生成新版。",
            )
        return WorkLogItem(
            id=f"agent:{index}:state:{agent}",
            timestamp=timestamp,
            agent=agent,
            kind="state",
            status=to_state,
            title=f"{_agent_label(agent)} 状态变化",
            detail=f"{from_state or 'unknown'} -> {to_state}",
        )
    if event == "agent.revision_started":
        return WorkLogItem(
            id=f"agent:{index}:revision:{agent}",
            timestamp=timestamp,
            agent=agent,
            kind="revision",
            status="running",
            title="根据人工意见开始返工",
            detail=str(payload.get("reason") or "用户要求生成新版方案。"),
            next_action="把人工意见加入上下文，重新讨论并写出下一版产物。",
        )
    if event.startswith("idea.acceptance_report"):
        return WorkLogItem(
            id=f"agent:{index}:acceptance:{agent}",
            timestamp=timestamp,
            agent=agent,
            kind="artifact",
            status="written",
            title="写入 Idea Agent 验收报告",
            detail=str(payload.get("path") or "idea/idea_agent_acceptance_report.md"),
            evidence_refs=[str(payload.get("path") or "")] if payload.get("path") else [],
        )
    return None


def _review_worklog(*, index: int, payload: dict[str, Any]) -> WorkLogItem:
    detail = payload.get("detail")
    detail_dict = detail if isinstance(detail, dict) else {}
    reason = str(detail_dict.get("reason") or payload.get("reason") or "")
    agent = str(payload.get("agent") or "")
    return WorkLogItem(
        id=f"review:{index}:{agent}",
        timestamp=str(payload.get("timestamp") or ""),
        agent=agent,
        kind="human_feedback",
        status=str(payload.get("action") or "review"),
        title="收到人工驳回意见",
        detail=reason or "用户要求重新生成方案。",
        next_action="将该意见作为高优先级上下文，生成新版产物。",
    )


def _tool_call_worklog(*, index: int, payload: dict[str, Any]) -> WorkLogItem:
    tool = str(payload.get("tool") or payload.get("tool_name") or "tool")
    status = str(payload.get("status") or ("success" if payload.get("ok") else "error"))
    ok = bool(payload.get("ok"))
    args = _mapping(payload.get("args"))
    title, detail, next_action = _tool_worklog_text(tool=tool, args=args, payload=payload, ok=ok)
    error = str(payload.get("error") or "")
    if error:
        detail = f"{detail}；错误：{error}" if detail else f"错误：{error}"
    return WorkLogItem(
        id=f"tool:{index}:{tool}",
        timestamp=str(payload.get("timestamp") or ""),
        agent=str(payload.get("agent") or ""),
        kind="tool",
        status=status,
        title=title,
        detail=detail,
        next_action=next_action,
        evidence_refs=_tool_evidence_refs(payload),
    )


def _tool_worklog_text(
    *, tool: str, args: dict[str, Any], payload: dict[str, Any], ok: bool
) -> tuple[str, str, str]:
    query = _short(str(args.get("query") or args.get("q") or ""), 180)
    if tool == "search.local_docs":
        return ("检索本地背景文档", f"query={query}", "把命中的项目文档放入 research pack。")
    if tool == "knowledge.kb_query":
        zone = str(args.get("zone") or "all")
        return ("查询知识库", f"zone={zone}；query={query}", "用 KB 结果补充领域背景和历史经验。")
    if tool == "knowledge.experiment_memory":
        return ("检索历史实验记忆", f"query={query}", "复用相近实验配置，避免重复试错。")
    if tool == "knowledge.code_assets":
        return ("检索代码资产", f"query={query}", "把可复用实现和接口约束带入代码规格。")
    if tool == "knowledge.methodology":
        return ("查询方法论资料", f"query={query}", "用方法论约束实验设计和报告写法。")
    if tool == "knowledge.run_archive":
        return ("查询历史 run", f"query={query}", "用历史指标和失败模式校准当前判断。")
    if tool == "knowledge.ingest_document":
        zone = str(args.get("zone") or "unknown")
        return ("沉淀资料到知识库", f"zone={zone}", "让后续 Agent 可以复用这条上下文。")
    if tool == "code.repo_reader":
        path = str(args.get("path") or "")
        if ok:
            return ("读取代码仓文件", f"读取 {path}，确认实现细节、接口和可改边界。", "用代码证据约束方案，避免凭空设计。")
        return ("读取代码仓文件失败", f"尝试读取 {path}", "改用 repo_link/allowed_paths 中可用路径继续核对。")
    if tool == "code.patch_generator":
        path = str(args.get("path") or "")
        return ("生成/规范化补丁", f"目标={path or 'diff'}", "把补丁作为可审计产物交给人工审核。")
    if tool == "code.apply_patch":
        version = str(args.get("version") or "")
        return ("应用已批准补丁", f"version={version or 'unknown'}", "记录 patch 结果和 rollback 引用。")
    if tool == "code.write_file":
        path = str(args.get("path") or "")
        return ("写入代码文件", f"path={path}", "写入后运行 lint/test 并保留回滚信息。")
    if tool == "code.delete_file":
        path = str(args.get("path") or "")
        return ("删除代码文件", f"path={path}", "删除属于高风险动作，需要审计和回滚。")
    if tool == "code.rollback_patch":
        return ("回滚代码补丁", "恢复到工具调用前的快照。", "确认工作区重新回到可审计状态。")
    if tool == "code.test_runner":
        return ("运行测试", _tool_result_brief(payload, default="执行配置的测试命令。"), "把测试结果写入 Coding/Execution 交接证据。")
    if tool == "code.lint":
        return ("运行 lint", _tool_result_brief(payload, default="执行配置的 lint 命令。"), "修正 lint 问题或把阻塞项展示给用户。")
    if tool == "knowledge.baseline_match":
        return ("检查 baseline 约束", "确认方案变量和指标是否触碰项目保护规则。", "只在允许范围内提出可验证假设。")
    if tool == "search.arxiv_search":
        quality = _mapping(_mapping(payload.get("quality")).get("literature_relevance"))
        relevant = quality.get("relevant_hits")
        total = quality.get("total_hits")
        suffix = f"；相关命中 {relevant}/{total}" if relevant is not None and total is not None else ""
        return ("检索 arXiv 论文", f"query={query}{suffix}", "命中相关论文后下载 PDF；低相关时改 query 继续查。")
    if tool == "search.fetch_sources":
        sources = args.get("sources")
        source_count = len(sources) if isinstance(sources, list) else 0
        return ("下载/抓取关键来源", f"抓取 {source_count} 个来源，生成 PDF/正文摘要。", "把 source_summaries 注入当前 Agent 上下文。")
    if tool == "search.web_search":
        return ("检索 web/blog 来源", f"query={query}", "若 provider/allowlist 缺失，就把它作为配置阻塞展示。")
    if tool == "execution.metrics_collector":
        run_id = str(args.get("run_id") or payload.get("run_id") or "")
        return ("读取执行指标", f"run_id={run_id or 'current'}；{_tool_result_brief(payload, default='读取 metrics.json')}", "用真实指标判断是否达标。")
    if tool == "execution.log_streamer":
        run_id = str(args.get("run_id") or payload.get("run_id") or "")
        return ("读取执行日志", f"run_id={run_id or 'current'}；{_tool_result_brief(payload, default='读取日志尾部')}", "定位失败原因或确认运行完成。")
    if tool == "execution.simulation_runner":
        experiment_id = str(args.get("experiment_id") or args.get("id") or "")
        return ("运行单组仿真", f"experiment={experiment_id or 'exp1'}；{_tool_result_brief(payload, default='执行 simulation_runner')}", "沉淀 run_log、metrics 和曲线文件。")
    if tool == "execution.batch_runner":
        experiments = args.get("experiments")
        count = len(experiments) if isinstance(experiments, list) else 1
        return ("运行实验批次", f"experiments={count}；{_tool_result_brief(payload, default='执行 batch_runner')}", "汇总多组指标，交给 Writing Agent 使用。")
    if tool == "reporting.generate_bundle":
        return ("生成报告附件包", _tool_result_brief(payload, default="生成报告 manifest 和 Office 附件。"), "把可交付文件展示在报告区并沉淀到 writing 工作区。")
    return (f"调用工具 {tool}", query or "执行工具调用。", "把工具结果写入审计记录。")


def _tool_result_brief(payload: dict[str, Any], *, default: str) -> str:
    output = _mapping(payload.get("output_summary"))
    metrics = _mapping(payload.get("metrics"))
    artifacts = payload.get("artifacts")
    parts: list[str] = []
    backend = output.get("backend")
    status = output.get("status")
    if backend:
        parts.append(f"backend={backend}")
    if status:
        parts.append(f"status={status}")
    results_count = output.get("results_count")
    if results_count is not None:
        parts.append(f"results={results_count}")
    if metrics:
        parts.append("metrics=" + ",".join(list(metrics.keys())[:4]))
    if isinstance(artifacts, list) and artifacts:
        parts.append(f"artifacts={len(artifacts)}")
    return "；".join(parts) if parts else default


def _tool_evidence_refs(payload: dict[str, Any]) -> list[str]:
    refs = [str(ref) for ref in payload.get("evidence_refs") or [] if str(ref)]
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            path = str(artifact.get("path") or "")
            if path:
                refs.append(path)
    return list(dict.fromkeys(refs))


def _evaluation_worklog(*, index: int, payload: dict[str, Any]) -> WorkLogItem:
    artifact = str(payload.get("artifact_ref") or payload.get("artifact_id") or "")
    decision = str(payload.get("decision") or "")
    score = payload.get("overall_score")
    score_text = f"；分数={float(score):.3f}" if isinstance(score, int | float) else ""
    return WorkLogItem(
        id=f"evaluation:{index}:{artifact}",
        timestamp=str(payload.get("timestamp") or ""),
        agent=str(payload.get("agent") or ""),
        kind="evaluation",
        status=decision,
        title="评价产物质量",
        detail=f"{artifact} -> {decision}{score_text}",
        next_action="通过则进入人工审核；发现硬证据问题则阻断或要求修订。",
        evidence_refs=[artifact] if artifact else [],
    )


def _websocket_worklog(
    *,
    index: int,
    payload: dict[str, Any],
    fallback_timestamp: str,
    timestamp_hints: dict[str, dict[str, str]],
) -> WorkLogItem | None:
    event = str(payload.get("event") or "")
    agent = str(payload.get("agent") or payload.get("node") or "")
    timestamp = str(payload.get("timestamp") or fallback_timestamp)
    if event == "hitl.review_required":
        artifact = str(payload.get("artifact_id") or "")
        timestamp = str(
            payload.get("timestamp")
            or (timestamp_hints.get("artifact") or {}).get(artifact)
            or fallback_timestamp
        )
        revision = "新版" if payload.get("revision") or _artifact_version(artifact) != "v1" else "初版"
        return WorkLogItem(
            id=f"hitl:{index}:review:{artifact}",
            timestamp=timestamp,
            agent=agent,
            kind="hitl",
            status="waiting_review",
            title=f"{revision}产物等待审核",
            detail=artifact or "产物已生成，等待人工检查。",
            next_action="用户可以批准，或输入意见驳回返工。",
            evidence_refs=[f"{agent}/{artifact}"] if agent and artifact else [],
        )
    if event == "hitl.revision_started":
        reason = str(payload.get("reason") or "")
        timestamp = str(
            payload.get("timestamp")
            or (timestamp_hints.get("revision_reason") or {}).get(reason)
            or fallback_timestamp
        )
        return WorkLogItem(
            id=f"hitl:{index}:revision:{agent}",
            timestamp=timestamp,
            agent=agent,
            kind="human_feedback",
            status="running",
            title="开始按人工意见返工",
            detail=reason or "用户要求修订。",
            next_action="重新装载上下文，产出下一版 proposal。",
        )
    return None


def _context_manifest_worklog(*, run: RunHandle, agent_filter: str) -> list[WorkLogItem]:
    items: list[WorkLogItem] = []
    for path in sorted((run.root / "context").glob("*manifest*.json")):
        if path.name == "trace_manifest.v2.json":
            continue
        raw = _read_json(path)
        if not isinstance(raw, dict):
            continue
        agent = str(raw.get("agent") or "")
        if agent_filter and agent and agent != agent_filter:
            continue
        if agent_filter and not agent:
            continue
        budget = _mapping(raw.get("budget"))
        used = budget.get("used")
        target = budget.get("target")
        items.append(
            WorkLogItem(
                id=path.name,
                timestamp=str(raw.get("created_at") or raw.get("updated_at") or ""),
                agent=agent,
                kind="context",
                status="loaded",
                title="装载上下文包",
                detail=(
                    f"{raw.get('node_key') or agent or 'agent'} · "
                    f"segments={len(raw.get('segments') or [])} · tokens={used}/{target}"
                ),
                next_action="把项目规则、代码证据、研究摘要和人工反馈交给模型生成。",
                evidence_refs=[f"context/{path.name}"],
            )
        )
    return items


def _deduplicate_worklog(items: list[WorkLogItem]) -> list[WorkLogItem]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[WorkLogItem] = []
    for item in items:
        key = (item.timestamp, item.agent, item.kind, item.title + item.detail)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _worklog_timestamp_hints(
    *,
    evaluation_rows: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    artifacts: dict[str, str] = {}
    revision_reasons: dict[str, str] = {}
    for row in evaluation_rows:
        timestamp = str(row.get("timestamp") or "")
        if not timestamp:
            continue
        for key in ("artifact_id", "artifact_ref"):
            value = str(row.get(key) or "")
            if value:
                artifacts[value] = timestamp
                artifacts[value.split("/")[-1]] = timestamp
    for row in review_rows:
        timestamp = str(row.get("timestamp") or "")
        detail = _mapping(row.get("detail"))
        reason = str(detail.get("reason") or row.get("reason") or "")
        if timestamp and reason:
            revision_reasons[reason] = timestamp
    return {"artifact": artifacts, "revision_reason": revision_reasons}


def _trace_items(path: Path) -> list[TimelineItem]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return []
    spans = raw.get("spans", [])
    if not isinstance(spans, list):
        return []
    items: list[TimelineItem] = []
    for span in spans:
        if not isinstance(span, dict):
            continue
        attrs = span.get("attributes", {})
        attrs_dict = attrs if isinstance(attrs, dict) else {}
        span_id = str(span.get("span_id") or len(items))
        items.append(
            TimelineItem(
                id=f"trace:{span_id}",
                timestamp=str(span.get("started_at") or ""),
                source="trace_manifest",
                kind="trace_span",
                title=str(span.get("name") or "trace span"),
                summary=f"{span.get('kind', 'span')} {span.get('status', '')}".strip(),
                status=str(span.get("status") or ""),
                agent=str(attrs_dict.get("agent") or attrs_dict.get("stage") or ""),
                node=str(attrs_dict.get("node") or ""),
                payload=span,
            )
        )
    return items


def _context_manifest_items(context_dir: Path) -> list[TimelineItem]:
    if not context_dir.exists():
        return []
    items: list[TimelineItem] = []
    for path in sorted(context_dir.glob("*manifest*.json")):
        if path.name == "trace_manifest.v2.json":
            continue
        raw = _read_json(path)
        if not isinstance(raw, dict):
            continue
        items.append(
            TimelineItem(
                id=f"context:{path.name}",
                timestamp=str(raw.get("created_at") or raw.get("updated_at") or ""),
                source="context",
                kind="context_manifest",
                title=str(raw.get("schema") or path.name),
                summary=str(raw.get("summary") or "context manifest updated"),
                payload={"path": path.name, "manifest": raw},
            )
        )
    return items


def _classify_event(event: str, source: str) -> str:
    if event.startswith("langgraph."):
        return "langgraph"
    if event.startswith("reporting."):
        return "reporting"
    if event.startswith("hitl."):
        return "hitl"
    if event.startswith("tool.") or source == "tool_calls":
        return "tool"
    if event.startswith("timeline.reasoning_summary"):
        return "reasoning_summary"
    if event.startswith("evaluation."):
        return "evaluation"
    if "feedback_loop" in event:
        return "feedback"
    if event.startswith("run."):
        return "state"
    return "event"


def _event_summary(event: str, payload: dict[str, Any]) -> str:
    if event.startswith("reporting."):
        return " ".join(
            str(payload.get(key, ""))
            for key in ("kind", "path", "status")
            if payload.get(key)
        )
    if event.startswith("tool.") or "tool" in payload:
        return " ".join(
            str(payload.get(key, ""))
            for key in ("tool", "status", "error")
            if payload.get(key)
        )
    if payload.get("reason"):
        return str(payload["reason"])
    if payload.get("summary"):
        return str(payload["summary"])
    if payload.get("artifact_id"):
        return str(payload["artifact_id"])
    return ""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _run_started_at(run: RunHandle) -> str:
    websocket_events = _read_jsonl(run.subdir("events") / "websocket_events.jsonl")
    for item in websocket_events:
        if item.get("event") == "run.started" and item.get("timestamp"):
            return str(item["timestamp"])
    agent_events = _read_jsonl(run.subdir("events") / "agent_events.jsonl")
    for item in agent_events:
        if item.get("timestamp"):
            return str(item["timestamp"])
    return run.created_at


def _belongs_to_agent(payload: dict[str, Any], agent_filter: str) -> bool:
    if not agent_filter:
        return True
    agent = str(payload.get("agent") or payload.get("node") or "")
    return agent == agent_filter


def _first_timestamp(items: list[WorkLogItem]) -> str:
    timestamps = [item.timestamp for item in items if item.timestamp]
    return min(timestamps) if timestamps else ""


def _latest_timestamp(items: list[WorkLogItem]) -> str:
    timestamps = [item.timestamp for item in items if item.timestamp]
    return max(timestamps) if timestamps else ""


def _latest_agent_status(items: list[WorkLogItem]) -> str:
    for item in reversed(items):
        if item.kind in {"state", "hitl"} and item.status:
            return item.status
    for item in reversed(items):
        if item.status:
            return item.status
    return ""


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _elapsed_seconds(start: str, end: str) -> float | None:
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, round((end_dt - start_dt).total_seconds(), 3))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _short(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _artifact_version(artifact_id: str) -> str:
    parts = artifact_id.split(".")
    for part in parts:
        if part.startswith("v") and part[1:].isdigit():
            return part
    return ""


def _next_agent_timestamp(rows: list[dict[str, Any]], start_index: int) -> str:
    for row in rows[start_index + 1 :]:
        timestamp = str(row.get("timestamp") or "")
        if timestamp:
            return timestamp
    return ""


def _agent_label(agent: str) -> str:
    labels = {
        "idea": "Idea Agent",
        "experiment": "Experiment Agent",
        "coding": "Coding Agent",
        "execution": "Execution Agent",
        "writing": "Writing Agent",
        "commander": "Commander Agent",
    }
    return labels.get(agent, agent or "Agent")
