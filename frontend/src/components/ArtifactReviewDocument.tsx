"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { getRun, getArtifact, listVersions, approveArtifact, rejectArtifact, STAGE_TO_STEM, type ArtifactView, type RunDetail, type Stage } from "@/lib/api";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { reviewResultUncertain } from "@/lib/apiError";
import { statusLabel } from "@/lib/researchActivity";
import { artifactBody, latestStages } from "@/lib/runReview";
import { ResearchEvidencePanel } from "./ResearchEvidencePanel";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
const TITLES: Record<Stage, string> = { idea: "研究提案", experiment: "实验方案", coding: "代码方案", execution: "实验记录", writing: "研究报告" };
const button = "rounded-lg border border-mars-border px-4 py-2 text-sm hover:bg-mars-panel disabled:opacity-40";

export function ArtifactReviewDocument({ run, stage, state, stale, advanced, onChanged, onNotice, onArtifact, onBusy, onReviewed, expanded = false, reviewActions = true, initiallyCollapsed = false }: { run: RunDetail; stage: Stage; state: string; stale: boolean; advanced: string; onChanged: () => Promise<void>; onNotice: (message: string) => void; onArtifact?: (artifact: ArtifactView | null) => void; onBusy?: (busy: boolean) => void; onReviewed?: () => void; expanded?: boolean; reviewActions?: boolean; initiallyCollapsed?: boolean }): JSX.Element {
  const [collapsed, setCollapsed] = useState(initiallyCollapsed);
  const contentId = useId();
  const [artifact, setArtifact] = useState<ArtifactView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reviewError, setReviewError] = useState("");
  const [feedback, setFeedback] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [receipt, setReceipt] = useState<"approve" | "revise" | null>(null);
  const locked = useRef(false);
  const alive = useRef(true);
  useEffect(() => { onArtifact?.(artifact); }, [artifact, onArtifact]);
  useEffect(() => { onBusy?.(busy); }, [busy, onBusy]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    setUncertain(false); setReceipt(null); setReviewError(""); setLoading(true); setArtifact(null);
    let active = true, reading = false;
    const read = async (): Promise<void> => {
      if (reading || locked.current) return;
      reading = true;
      try {
        const versions = await listVersions(run.run_id, stage, STAGE_TO_STEM[stage]);
        const latest = versions.at(-1);
        const doc = latest ? await getArtifact(run.run_id, stage, STAGE_TO_STEM[stage], latest.version) : null;
        if (active) { setArtifact(doc); setError(""); }
      } catch { if (active) setError("文档暂时无法读取，请稍后重试。"); }
      finally { reading = false; if (active) setLoading(false); }
    };
    void read(); const timer = setInterval(() => void read(), CLIENT_POLICY.readinessRefreshMs);
    return () => { active = false; clearInterval(timer); };
  }, [run.run_id, stage, state]);
  const canReview = reviewActions && state === "waiting_review" && !!artifact && !run.read_only && !stale && !error && !busy && !loading && !uncertain && receipt === null;
  async function review(action: "approve" | "revise"): Promise<void> {
    if (!canReview || !artifact || (action === "approve" && !artifact.valid) || locked.current || (action === "revise" && !reason.trim())) return;
    locked.current = true; setBusy(true); setReviewError("");
    let submitted = false;
    try {
      const [freshRun, versions] = await Promise.all([getRun(run.run_id), listVersions(run.run_id, stage, artifact.stem)]);
      if (freshRun.read_only || latestStages(freshRun).find(item => item.stage === stage)?.state !== "waiting_review" || versions.at(-1)?.version !== artifact.version) {
        throw new Error("任务或文档版本已更新，请刷新后重新审核。");
      }
      const current = await getArtifact(run.run_id, stage, artifact.stem, artifact.version);
      if (current.text !== artifact.text) throw new Error("文档内容已更新，请刷新后重新审核。");
      submitted = true;
      if (action === "approve") await approveArtifact(run.run_id, stage, artifact.stem, artifact.version);
      else await rejectArtifact(run.run_id, stage, artifact.stem, reason.trim());
      onNotice(action === "approve" ? "已批准，任务状态将继续更新。" : "已提交修改意见，正在等待新版文档。");
      if (alive.current) { setFeedback(false); setReceipt(action); setUncertain(false); }
      try { await onChanged(); } catch { onNotice(action === "approve" ? "已批准，页面状态暂时无法更新，正在等待同步。" : "修改意见已提交，页面状态暂时无法更新，正在等待同步。"); }
      if (alive.current) onReviewed?.();
    } catch (cause: unknown) {
      if (alive.current) { setUncertain(reviewResultUncertain(cause, submitted)); setReviewError(cause instanceof Error ? cause.message : "操作结果待核对，请核对任务状态。"); }
    } finally { locked.current = false; if (alive.current) setBusy(false); }
  }
  return <section aria-label={TITLES[stage]} className="overflow-clip rounded-xl border border-mars-border bg-mars-panel/20">
    <div className={`flex flex-wrap items-center justify-between gap-3 px-5 py-4 ${collapsed ? "" : "border-b border-mars-border"}`}>
      <h2 className="font-medium">{TITLES[stage]}</h2>
      <div className="flex items-center gap-3">
        <span className={`text-xs ${state === "waiting_review" ? "text-amber-300" : "text-slate-400"}`}>{statusLabel(state)}</span>
        <button type="button" aria-label={`${collapsed ? "展开" : "收起"}${TITLES[stage]}`} aria-expanded={!collapsed} aria-controls={contentId} onClick={() => setCollapsed(value => !value)} className="flex items-center gap-1.5 rounded-lg border border-mars-border px-3 py-1.5 text-xs text-slate-300 hover:bg-mars-panel focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400">
          {collapsed ? "展开" : "收起"}
          <svg aria-hidden="true" viewBox="0 0 16 16" className={`h-3.5 w-3.5 ${collapsed ? "" : "rotate-180"}`} fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m4 6 4 4 4-4" /></svg>
        </button>
      </div>
    </div>
    {error ? <p role="alert" className="px-5 pt-4 text-sm text-amber-300">{error}</p> : null}
    <div id={contentId} hidden={collapsed}>
    {loading ? <p className="p-8 text-sm text-slate-400">正在读取文档…</p> : artifact ? <article className="break-words px-5 py-5 text-sm leading-7 text-slate-300 sm:px-8 [&_p]:my-3 [&_h1]:mb-5 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-3 [&_h2]:mt-7 [&_h2]:text-lg [&_h2]:font-medium [&_h3]:mb-2 [&_h3]:mt-5 [&_h3]:font-medium [&_ul]:list-disc [&_ol]:list-decimal [&_li]:ml-5 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-mars-panel [&_pre]:p-4 [&_table]:block [&_table]:overflow-x-auto [&_td]:border [&_td]:border-mars-border [&_td]:p-2 [&_th]:border [&_th]:border-mars-border [&_th]:p-2"><ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={(url, key) => key === "src" && !/^(?:https?:|data:)/.test(url) ? `${API_BASE}/api/reports/${encodeURIComponent(run.run_id)}/images?${new URLSearchParams({ path: url })}` : defaultUrlTransform(url)}>{artifactBody(artifact.text)}</ReactMarkdown></article> : <p className="p-8 text-sm text-slate-400">{state === "running" ? "Agent 正在处理，文档生成后会显示在这里。" : state === "pending" ? "该阶段尚未开始。" : "暂无文档。"}</p>}
    {expanded && artifact ? <ProposalDetails metadata={artifact.metadata} /> : null}
    {stage === "idea" ? <ResearchEvidencePanel runId={run.run_id} project={run.project} version={artifact?.version ?? ""} active={state === "running"} /> : null}
    </div>
    {reviewActions && state === "waiting_review" ? <div className="sticky bottom-0 border-t border-mars-border bg-mars-panel px-5 py-4">
      {reviewError ? <p role="alert" className="mb-3 text-sm text-amber-300">{reviewError}</p> : null}
      {uncertain && !busy ? <button type="button" onClick={() => void onChanged().catch(() => onNotice("任务状态暂时无法读取，请稍后核对。"))} className={`${button} mb-3`}>核对任务状态</button> : null}
      {feedback ? <form onSubmit={event => { event.preventDefault(); void review("revise"); }}><label className="text-sm" htmlFor="review-feedback">需要修改什么？</label><textarea id="review-feedback" value={reason} onChange={event => setReason(event.target.value)} rows={3} className="mt-2 w-full rounded-lg border border-mars-border bg-mars-bg p-3 text-sm" /><div className="mt-3 flex justify-end gap-2"><button type="button" disabled={busy} onClick={() => setFeedback(false)} className={button}>取消</button><button disabled={!canReview || !reason.trim()} className={`${button} bg-mars-accent text-white`}>提交修改意见</button></div></form> : <div className="flex flex-wrap items-center justify-between gap-3"><span className="text-xs text-slate-400">{busy ? "正在提交…" : receipt === "approve" ? "已批准，正在同步下一阶段。" : receipt === "revise" ? "修改意见已保存，等待新版文档。" : uncertain ? "提交结果暂未确认，正在核对；不会重复提交。" : reviewError ? "审核未完成，请根据提示处理后重试。" : artifact && !artifact.valid ? "文档校验未通过" : "审核后继续下一阶段"}</span><div className="flex gap-2"><button disabled={!canReview} onClick={() => setFeedback(true)} className={button}>提出修改</button><button disabled={!canReview || !artifact?.valid} onClick={() => void review("approve")} className={`${button} border-transparent bg-mars-accent text-white`}>批准并继续</button></div></div>}
    </div> : null}
    {!collapsed ? <div className="flex justify-end border-t border-mars-border px-5 py-3 text-xs text-slate-500"><Link href={advanced} className="hover:text-indigo-300">编辑文档与查看细节 →</Link></div> : null}
  </section>;
}

const asRecord = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const asText = (value: unknown): string => typeof value === "string" ? value : "";
function ProposalDetails({ metadata }: { metadata: Record<string, unknown> }): JSX.Element {
  const method = asRecord(metadata.method_spec), verification = asRecord(method.verification), research = asRecord(metadata.research_context);
  const changes = Array.isArray(method.training_changes) ? method.training_changes.map(asRecord) : [];
  const steps = Array.isArray(verification.steps) ? verification.steps.filter((value): value is string => typeof value === "string") : [];
  const questions = Array.isArray(research.open_questions) ? research.open_questions.filter((value): value is string => typeof value === "string") : [];
  return <div className="space-y-5 px-5 pb-6 text-sm leading-6 text-slate-300 sm:px-8">
    {changes.length ? <section><h3 className="mb-2 font-medium text-slate-100">拟采用的方法</h3><ol className="space-y-2">{changes.map((change, index) => <li key={index} className="rounded-lg border border-mars-border bg-mars-bg/50 p-3"><p>{asText(change.change)}</p>{asText(change.edge_cases) ? <details className="mt-2 text-xs text-slate-400"><summary className="cursor-pointer">实现约束</summary><p className="mt-2">{asText(change.edge_cases)}</p></details> : null}</li>)}</ol></section> : null}
    {asText(method.no_param_change_argument) ? <section><h3 className="mb-2 font-medium text-slate-100">参数与修改边界</h3><p>{asText(method.no_param_change_argument)}</p></section> : null}
    {steps.length ? <section><h3 className="mb-2 font-medium text-slate-100">实验与对照</h3><ol className="list-decimal space-y-1 pl-5">{steps.map((step, index) => <li key={index}>{step}</li>)}</ol></section> : null}
    {questions.length ? <section className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3"><h3 className="mb-2 font-medium text-amber-200">尚待确认</h3><ul className="list-disc space-y-1 pl-5 text-xs text-slate-400">{questions.map((question, index) => <li key={index}>{question}</li>)}</ul></section> : null}
  </div>;
}
