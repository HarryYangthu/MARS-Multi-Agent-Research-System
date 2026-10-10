"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { getArtifactEvaluationSummary, getRun, type ArtifactEvaluationSummary, type ArtifactView, type RunDetail, type Stage } from "@/lib/api";
import { latestStages } from "@/lib/runReview";
import { agentLabel, statusLabel } from "@/lib/researchActivity";
import { readInspection, objectValue, promptMessages, type InspectionCatalog, type InspectionEvent } from "@/lib/agentInspection";
import { ArtifactReviewDocument } from "./ArtifactReviewDocument";

const AGENTS = ["commander", "idea", "experiment", "coding", "execution", "writing"];
const KIND: Record<string, string> = { model_request: "模型请求", model_response: "模型输出", tool_dispatch: "执行工具", observation: "工具结果", provider_request: "实际发送的请求", context_packed: "拼接上下文", validation: "产物校验", model_error: "模型错误", sdk_attempt_failed: "调用失败", sdk_attempt_started: "开始调用", sdk_attempt_succeeded: "调用完成", completed: "阶段完成", interrupted: "中断", context_compressed: "压缩上下文" };
const button = "rounded-lg border border-mars-border px-3 py-2 text-xs hover:bg-mars-panel2 focus-visible:outline-indigo-400";

export function AgentInspection({ runId }: { runId: string }): JSX.Element {
  const search = useSearchParams();
  const requested = search?.get("agent") || "commander";
  const agent = AGENTS.includes(requested) ? requested : "commander";
  const [run, setRun] = useState<RunDetail | null>(null);
  const [catalog, setCatalog] = useState<InspectionCatalog | null>(null);
  const [tab, setTab] = useState<"trace" | "prompts" | "quality">("trace");
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<InspectionEvent | null>(null);
  const [quality, setQuality] = useState<ArtifactEvaluationSummary | null>(null);
  const [qualityError, setQualityError] = useState("");
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [qualityArtifact, setQualityArtifact] = useState<ArtifactView | null>(null);
  const receiveArtifact = useCallback((artifact: ArtifactView | null): void => setQualityArtifact(artifact), []);
  useEffect(() => {
    let active = true;
    if (qualityArtifact && qualityArtifact.agent_dir === agent) void getArtifactEvaluationSummary(runId, qualityArtifact.agent_dir, qualityArtifact.stem, qualityArtifact.version).then(next => { if (active) setQuality(next); }).catch(() => { if (active) setQualityError("评分暂时无法读取，请刷新；未将缺失评分判为通过。"); });
    return () => { active = false; };
  }, [runId, agent, qualityArtifact]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setCatalog(null); setSelected(""); setDetail(null); setQuality(null); setQualityError(""); setError("");
    void Promise.all([getRun(runId), readInspection<InspectionCatalog>(runId, agent, undefined, controller.signal)]).then(([nextRun, next]) => { if (!controller.signal.aborted) { setRun(nextRun); setCatalog(next); } }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "记录读取失败"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runId, agent, revision]);
  useEffect(() => {
    const controller = new AbortController(); setDetail(null); setDetailError("");
    if (selected) void readInspection<InspectionEvent>(runId, agent, selected, controller.signal).then(next => { if (!controller.signal.aborted) setDetail(next); }).catch(cause => { if (!controller.signal.aborted) setDetailError(cause instanceof Error ? cause.message : "记录读取失败"); });
    return () => controller.abort();
  }, [runId, agent, selected]);
  const state = run ? latestStages(run).find(item => item.stage === agent)?.state || "idle" : "unknown";
  const items = tab === "prompts" ? catalog?.requests || [] : catalog?.events || [];
  const messages = detail ? promptMessages(detail) : [];
  const segments = Array.isArray(detail?.assembly?.segments) ? detail.assembly.segments.map(objectValue) : [];
  const workbench = `/runs/${encodeURIComponent(runId)}?view=workbench&agent=${agent}`;
  return <div className="min-h-screen bg-mars-bg"><main className="mx-auto max-w-6xl space-y-6 p-4 md:p-8">
    <header className="flex flex-wrap items-center justify-between gap-3"><Link href={`/runs/${encodeURIComponent(runId)}`} className="text-sm text-slate-400 hover:text-white">← 返回研究对话</Link><div className="flex gap-2"><Link href={workbench} className={button}>文档编辑与高级操作</Link><button type="button" onClick={() => setRevision(value => value + 1)} disabled={loading} className={button}>刷新记录</button></div></header>
    <div><h1 className="text-2xl font-semibold">{agentLabel(agent)} Agent 详情</h1><p className="mt-2 break-words text-sm text-slate-400">{run?.task || runId} · {statusLabel(state)}</p></div>
    <nav aria-label="选择 Agent" className="flex flex-wrap gap-2">{AGENTS.map(key => <Link key={key} href={`/runs/${encodeURIComponent(runId)}?view=advanced&agent=${key}`} aria-current={key === agent ? "page" : undefined} className={`${button} ${key === agent ? "border-indigo-400/60 bg-indigo-400/10 text-indigo-200" : "text-slate-400"}`}>{agentLabel(key)}</Link>)}</nav>
    <nav aria-label="Agent 详情栏目" className="flex gap-2 border-b border-mars-border pb-3">{([['trace', 'Agent Trace'], ['prompts', 'LLM 提示词与拼接'], ['quality', '最终输出质量']] as const).map(([key, label]) => <button type="button" key={key} aria-pressed={tab === key} onClick={() => { setTab(key); setSelected(""); }} className={`${button} ${tab === key ? "bg-mars-accent text-white" : "text-slate-400"}`}>{label}</button>)}</nav>
    {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
    {loading ? <p role="status" className="py-10 text-slate-400">正在读取实际执行记录…</p> : tab === "quality" ? <div className="space-y-4">
      <p className="text-sm text-slate-400">结构校验、产物评分与实验有效性分别呈现。流程完成不代表研究目标达成。</p>
      {qualityError ? <p role="alert" className="text-sm text-amber-200">{qualityError}</p> : null}
      {quality ? <section className="space-y-4 rounded-xl border border-mars-border bg-mars-panel p-5"><h2 className="font-medium">产物评价 · {quality.decision}{typeof quality.overall_score === "number" ? ` · ${(quality.overall_score * 100).toFixed(0)}%` : ""}</h2><p className="text-xs text-slate-400">{quality.artifact_ref}</p><div className="grid gap-3 md:grid-cols-3">{quality.reports.map((report, index) => <div key={index} className="rounded-lg border border-mars-border p-3 text-sm"><p>{report.evaluator}</p><p className="mt-2 text-slate-400">{report.decision} · {typeof report.overall_score === "number" ? `${(report.overall_score * 100).toFixed(0)}%` : "无分数"}</p>{report.findings?.map((finding, i) => <p key={i} className="mt-2 text-xs leading-6 text-amber-200">{finding.message}{finding.evidence_refs?.length ? ` · ${finding.evidence_refs.join(", ")}` : ""}</p>)}</div>)}</div>{quality.policy?.reasons.map((reason, index) => <p key={index} className="text-xs text-slate-400">{reason}</p>)}</section> : <p className="text-sm text-slate-500">{agent === "commander" ? "总控负责调度；阶段产物的质量请查看对应 Agent。" : "尚无产物评分记录。"}</p>}
      {run && agent !== "commander" ? <ArtifactReviewDocument key={`${runId}:${agent}`} run={run} stage={agent as Stage} state={state} stale={false} advanced={workbench} onChanged={async () => setRevision(value => value + 1)} onNotice={() => {}} reviewActions={false} onArtifact={receiveArtifact} initiallyCollapsed /> : null}
    </div> : <div className="space-y-5">
      <p className="text-sm text-slate-400">{tab === "prompts" ? "逐轮读取调用时保存的完整消息。旧任务未保存的最终请求会明确标注；压缩和来源按实际记录展示。" : "模型、工具、校验、重试和错误按真实执行顺序展示。点击一条记录查看原始输入或输出。"}</p>
      {!items.length ? <p className="rounded-xl border border-dashed border-mars-border p-8 text-sm text-slate-500">此 Agent 尚无已保存的{tab === "prompts" ? "模型请求" : "Trace"}。执行 Agent 的实际作业见实验台。</p> : <ol className="space-y-2">{items.map((item, index) => <li key={item.id}><button type="button" onClick={() => setSelected(item.id)} aria-pressed={selected === item.id} className={`flex w-full flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-4 py-3 text-left text-xs ${selected === item.id ? "border-indigo-400/60 bg-indigo-400/10" : "border-mars-border bg-mars-panel hover:border-indigo-400/40"}`}><span className="min-w-0 flex-1">{tab === "prompts" ? `第 ${index + 1} 次请求` : KIND[item.kind] || item.kind} {item.model || item.tool || ""}{item.backend ? ` · ${item.backend}` : ""}</span><time className="text-slate-500">{new Date(item.time).toLocaleTimeString("zh-CN")}</time></button>{selected === item.id ? <div className="mt-2 space-y-4 rounded-xl border border-mars-border p-4">{detailError ? <p role="alert" className="text-amber-200">{detailError}</p> : !detail ? <p role="status" className="text-slate-400">正在读取记录…</p> : tab === "prompts" ? <><p className="text-xs text-amber-200">{detail.prompt_source === "provider_request" ? "已保存模型适配器实际发送的请求正文。" : "历史记录：模型适配器前的完整快照。该轮最终请求未保存，无法证明适配器后正文完全相同。"}</p>{segments.length ? <details><summary className="cursor-pointer text-sm">拼接来源、顺序与压缩（{segments.length} 段）</summary><ol className="mt-3 space-y-2">{segments.map((segment, i) => <li key={i} className="break-all text-xs text-slate-400">{i + 1}. {String(segment.source || segment.source_ref || segment.title || segment.kind || "未记录来源")} · {segment.protected || segment.priority === "critical" ? "保留完整内容" : "可压缩"} · 压缩级别 {String(segment.compression ?? "未记录")}</li>)}</ol><Raw value={detail.assembly} label="完整拼接与裁剪记录" /></details> : <p className="text-xs text-slate-500">该轮未记录可逐段核对的拼接来源；不推测来源。</p>}{messages.map((message, i) => <details key={i}><summary className="cursor-pointer text-sm">{i + 1}. {String(message.role || "消息")} · {typeof message.content === "string" ? `${message.content.length} 字符` : "结构化内容"}</summary><pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-mars-panel p-4 text-xs leading-6">{typeof message.content === "string" ? message.content : JSON.stringify(message, null, 2)}</pre></details>)}<Raw value={detail.wire_payload ?? detail.event.visible} label="完整消息、工具定义与请求参数" /></> : <Raw value={detail.event} label="实际事件记录" open />}</div> : null}</li>)}</ol>}
    </div>}
  </main></div>;
}
function Raw({ value, label, open = false }: { value: unknown; label: string; open?: boolean }): JSX.Element {
  return <details open={open}><summary className="cursor-pointer text-xs text-indigo-200">{label}</summary><pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-mars-panel p-4 text-xs leading-6 text-slate-300">{JSON.stringify(value, null, 2)}</pre></details>;
}
