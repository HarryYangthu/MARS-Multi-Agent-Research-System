"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getRun, getArtifact, listVersions, approveArtifact, rejectArtifact, getRunWorkLog, STAGE_TO_STEM, type ArtifactView, type RunDetail, type Stage, type WorkLogView } from "@/lib/api";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { agentLabel, statusLabel } from "@/lib/researchActivity";
import { artifactBody, latestStages, reviewFocus } from "@/lib/runReview";
import { RunControlBar } from "./RunControlBar";
import { ActivityRow } from "./ResearchActivity";
import { CodeChangesCard } from "./CodeChangesCard";

const TITLES: Record<Stage, string> = { idea: "研究提案", experiment: "实验方案", coding: "代码方案", execution: "实验记录", writing: "研究报告" };
const button = "rounded-lg border border-mars-border px-4 py-2 text-sm hover:bg-mars-panel disabled:opacity-40";

export function SimpleRunDetail({ runId, initialAgent }: { runId: string; initialAgent: string }): JSX.Element {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(initialAgent);
  const [operations, setOperations] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const refresh = useCallback(async (): Promise<void> => {
    const next = await getRun(runId);
    setRun(next); setError("");
  }, [runId]);
  useEffect(() => {
    let active = true, reading = false;
    const read = async (): Promise<void> => {
      if (reading) return;
      reading = true;
      try { const next = await getRun(runId); if (active) { setRun(next); setError(""); } }
      catch { if (active) setError("暂时无法更新任务状态，正在重试。"); }
      finally { reading = false; }
    };
    void read(); const timer = setInterval(() => void read(), CLIENT_POLICY.controlRefreshMs);
    return () => { active = false; clearInterval(timer); };
  }, [runId]);
  useEffect(() => { setSelected(initialAgent); }, [initialAgent]);
  const stages = run ? latestStages(run) : [];
  const stage = run ? reviewFocus(run, selected) : null;
  const state = stages.find(item => item.stage === stage)?.state ?? "unknown";
  const advanced = `/runs/${encodeURIComponent(runId)}?view=advanced${stage ? `&agent=${stage}` : ""}`;
  return <main className="min-h-dvh bg-mars-bg text-slate-200">
    <header className="border-b border-mars-border px-5 py-4"><div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-4"><Link href="/runs/new" className="text-sm text-slate-400 hover:text-white">← 研究对话</Link><span className="text-sm font-medium">研究任务</span></div>
      <div className="flex items-center gap-4 text-xs text-slate-400"><Link href={`/results/${encodeURIComponent(runId)}`} className="hover:text-white">结果与导出</Link><Link href={advanced} className="hover:text-white">高级视图</Link><button onClick={() => setOperations(!operations)} aria-expanded={operations}>更多操作</button></div>
    </div></header>
    {operations ? <div className="mx-auto max-w-5xl"><RunControlBar runId={runId} run={run} onChange={setRun} showSimpleLink={false} /></div> : null}
    <div className="mx-auto max-w-5xl px-5 py-6 sm:py-8">
      {error ? <p role="alert" className="mb-4 text-sm text-amber-300">{error}</p> : null}
      {notice ? <p role="status" className="mb-4 text-sm text-indigo-200">{notice}</p> : null}
      {!run ? <p role="status" className="py-12 text-center text-slate-400">{error ? "等待重新连接…" : "正在读取任务…"}</p> : <>
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3"><h1 className="break-words text-lg font-medium">{run.task}</h1>{run.read_only ? <span className="text-xs text-amber-300">只读记录</span> : null}</div>
        <nav aria-label="研究阶段" className="mb-8 flex flex-wrap gap-2">{stages.map(item => <button key={item.stage} onClick={() => setSelected(item.stage)} aria-pressed={item.stage === stage} className={`rounded-lg border px-3 py-2 text-xs ${item.stage === stage ? "border-indigo-400/70 bg-indigo-400/10 text-indigo-100" : "border-mars-border text-slate-400"}`}>{agentLabel(item.stage)}<span className="ml-2 opacity-70">{statusLabel(item.state)}</span></button>)}</nav>
        {stage ? <ReviewDocument key={`${runId}:${stage}`} run={run} stage={stage} state={state} stale={!!error} advanced={advanced} onChanged={refresh} onNotice={setNotice} /> : <p className="py-8 text-sm text-slate-400">暂无可展示的阶段。<Link className="ml-2 text-indigo-300" href={advanced}>查看任务详情</Link></p>}
        {stage === "coding" ? <div className="mt-4"><CodeChangesCard key={runId} runId={runId} project={run.project} /></div> : null}
        <details className="mt-6 border-t border-mars-border pt-4" onToggle={event => setHistoryOpen(event.currentTarget.open)}><summary className="cursor-pointer text-xs text-slate-400">处理记录</summary>{historyOpen ? <RunHistory runId={runId} /> : null}</details>
      </>}
    </div>
  </main>;
}

function ReviewDocument({ run, stage, state, stale, advanced, onChanged, onNotice }: { run: RunDetail; stage: Stage; state: string; stale: boolean; advanced: string; onChanged: () => Promise<void>; onNotice: (message: string) => void }): JSX.Element {
  const [artifact, setArtifact] = useState<ArtifactView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const locked = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    setUncertain(false); setLoading(true);
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
  const canReview = state === "waiting_review" && !!artifact && !run.read_only && !stale && !error && !busy && !loading && !uncertain;
  async function review(action: "approve" | "revise"): Promise<void> {
    if (!canReview || !artifact || (action === "approve" && !artifact.valid) || locked.current || (action === "revise" && !reason.trim())) return;
    locked.current = true; setBusy(true);
    try {
      const [freshRun, versions] = await Promise.all([getRun(run.run_id), listVersions(run.run_id, stage, artifact.stem)]);
      if (freshRun.read_only || latestStages(freshRun).find(item => item.stage === stage)?.state !== "waiting_review" || versions.at(-1)?.version !== artifact.version) {
        throw new Error("任务或文档版本已更新，请刷新后重新审核。");
      }
      const current = await getArtifact(run.run_id, stage, artifact.stem, artifact.version);
      if (current.text !== artifact.text) throw new Error("文档内容已更新，请刷新后重新审核。");
      if (action === "approve") await approveArtifact(run.run_id, stage, artifact.stem, artifact.version);
      else await rejectArtifact(run.run_id, stage, artifact.stem, reason.trim());
      onNotice(action === "approve" ? "已批准，任务状态将继续更新。" : "已提交修改意见，正在等待新版文档。");
      if (alive.current) { setFeedback(false); setUncertain(true); }
      await onChanged();
    } catch (cause: unknown) {
      if (alive.current) { setUncertain(true); setError(cause instanceof Error ? cause.message : "操作结果待核对，请刷新任务。"); }
    } finally { locked.current = false; if (alive.current) setBusy(false); }
  }
  return <section aria-label={TITLES[stage]} className="overflow-clip rounded-xl border border-mars-border bg-mars-panel/20">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-mars-border px-5 py-4"><h2 className="font-medium">{TITLES[stage]}</h2><span className={`text-xs ${state === "waiting_review" ? "text-amber-300" : "text-slate-400"}`}>{statusLabel(state)}</span></div>
    {error ? <p role="alert" className="px-5 pt-4 text-sm text-amber-300">{error}</p> : null}
    {loading ? <p className="p-8 text-sm text-slate-400">正在读取文档…</p> : artifact ? <article className="break-words px-5 py-5 text-sm leading-7 text-slate-300 sm:px-8 [&_p]:my-3 [&_h1]:mb-5 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-3 [&_h2]:mt-7 [&_h2]:text-lg [&_h2]:font-medium [&_h3]:mb-2 [&_h3]:mt-5 [&_h3]:font-medium [&_ul]:list-disc [&_ol]:list-decimal [&_li]:ml-5 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-mars-panel [&_pre]:p-4 [&_table]:block [&_table]:overflow-x-auto [&_td]:border [&_td]:border-mars-border [&_td]:p-2 [&_th]:border [&_th]:border-mars-border [&_th]:p-2"><ReactMarkdown remarkPlugins={[remarkGfm]}>{artifactBody(artifact.text)}</ReactMarkdown></article> : <p className="p-8 text-sm text-slate-400">{state === "running" ? "Agent 正在处理，文档生成后会显示在这里。" : state === "pending" ? "该阶段尚未开始。" : "暂无文档。"}</p>}
    {state === "waiting_review" ? <div className="sticky bottom-0 border-t border-mars-border bg-mars-panel px-5 py-4">
      {feedback ? <form onSubmit={event => { event.preventDefault(); void review("revise"); }}><label className="text-sm" htmlFor="review-feedback">需要修改什么？</label><textarea id="review-feedback" value={reason} onChange={event => setReason(event.target.value)} rows={3} className="mt-2 w-full rounded-lg border border-mars-border bg-mars-bg p-3 text-sm" /><div className="mt-3 flex justify-end gap-2"><button type="button" disabled={busy} onClick={() => setFeedback(false)} className={button}>取消</button><button disabled={!canReview || !reason.trim()} className={`${button} bg-mars-accent text-white`}>提交修改意见</button></div></form> : <div className="flex flex-wrap items-center justify-between gap-3"><span className="text-xs text-slate-400">{busy ? "正在提交…" : uncertain ? "操作已提交或结果待核对，请刷新查看状态。" : artifact && !artifact.valid ? "文档校验未通过" : "审核后继续下一阶段"}</span><div className="flex gap-2"><button disabled={!canReview} onClick={() => setFeedback(true)} className={button}>提出修改</button><button disabled={!canReview || !artifact?.valid} onClick={() => void review("approve")} className={`${button} border-transparent bg-mars-accent text-white`}>批准并继续</button></div></div>}
    </div> : null}
    <div className="flex justify-between border-t border-mars-border px-5 py-3 text-xs text-slate-500"><span>{artifact?.version || ""}</span><Link href={advanced} className="hover:text-indigo-300">编辑文档与查看细节 →</Link></div>
  </section>;
}

function RunHistory({ runId }: { runId: string }): JSX.Element {
  const [history, setHistory] = useState<WorkLogView | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true, reading = false;
    const read = async (): Promise<void> => {
      if (reading) return; reading = true;
      try { const value = await getRunWorkLog(runId); if (active) { setHistory(value); setError(""); } }
      catch { if (active) setError("处理记录暂时无法读取。"); }
      finally { reading = false; }
    };
    void read(); const timer = setInterval(() => void read(), CLIENT_POLICY.controlRefreshMs);
    return () => { active = false; clearInterval(timer); };
  }, [runId]);
  return <div className="mt-3 max-h-96 overflow-y-auto rounded-xl border border-mars-border p-4">{error ? <p role="alert" className="text-xs text-amber-300">{error}</p> : null}{history?.items.map(item => <ActivityRow key={item.id} activity={{ ...item, detail: item.detail }} />)}</div>;
}
