"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getRun, getRunWorkLog, type RunDetail, type WorkLogView } from "@/lib/api";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { agentLabel, statusLabel } from "@/lib/researchActivity";
import { latestStages, reviewFocus } from "@/lib/runReview";
import { RunControlBar } from "./RunControlBar";
import { ActivityRow } from "./ResearchActivity";
import { CodeChangesCard } from "./CodeChangesCard";
import { ArtifactReviewDocument } from "./ArtifactReviewDocument";
import { researchRunConversationUrl } from "@/lib/runConversation";

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
      <div className="flex items-center gap-4">{run ? <Link href={researchRunConversationUrl(run)} className="text-sm text-indigo-300 hover:text-white">← 在主对话中查看</Link> : <span className="text-sm text-slate-500">研究对话</span>}<span className="text-sm font-medium">研究任务</span></div>
      <div className="flex items-center gap-4 text-xs text-slate-400"><Link href={`/results/${encodeURIComponent(runId)}`} className="hover:text-white">结果与导出</Link><Link href={advanced} className="hover:text-white">高级视图</Link><button onClick={() => setOperations(!operations)} aria-expanded={operations}>更多操作</button></div>
    </div></header>
    {operations ? <div className="mx-auto max-w-5xl"><RunControlBar runId={runId} run={run} onChange={setRun} showSimpleLink={false} /></div> : null}
    <div className="mx-auto max-w-5xl px-5 py-6 sm:py-8">
      {error ? <p role="alert" className="mb-4 text-sm text-amber-300">{error}</p> : null}
      {notice ? <p role="status" className="mb-4 text-sm text-indigo-200">{notice}</p> : null}
      {!run ? <p role="status" className="py-12 text-center text-slate-400">{error ? "等待重新连接…" : "正在读取任务…"}</p> : <>
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3"><h1 className="break-words text-lg font-medium">{run.task}</h1>{run.read_only ? <span className="text-xs text-amber-300">只读记录</span> : null}</div>
        <nav aria-label="研究阶段" className="mb-8 flex flex-wrap gap-2">{stages.map(item => <button key={item.stage} onClick={() => setSelected(item.stage)} aria-pressed={item.stage === stage} className={`rounded-lg border px-3 py-2 text-xs ${item.stage === stage ? "border-indigo-400/70 bg-indigo-400/10 text-indigo-100" : "border-mars-border text-slate-400"}`}>{agentLabel(item.stage)}<span className="ml-2 opacity-70">{statusLabel(item.state)}</span></button>)}</nav>
        {stage ? <ArtifactReviewDocument key={`${runId}:${stage}`} run={run} stage={stage} state={state} stale={!!error} advanced={advanced} onChanged={refresh} onNotice={setNotice} /> : <p className="py-8 text-sm text-slate-400">暂无可展示的阶段。<Link className="ml-2 text-indigo-300" href={advanced}>查看任务详情</Link></p>}
        {stage === "coding" ? <div className="mt-4"><CodeChangesCard key={runId} runId={runId} project={run.project} /></div> : null}
        <details className="mt-6 border-t border-mars-border pt-4" onToggle={event => setHistoryOpen(event.currentTarget.open)}><summary className="cursor-pointer text-xs text-slate-400">处理记录</summary>{historyOpen ? <RunHistory runId={runId} /> : null}</details>
      </>}
    </div>
  </main>;
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
