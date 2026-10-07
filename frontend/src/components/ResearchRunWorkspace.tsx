"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { ArtifactView, RunDetail, Stage } from "@/lib/api";
import { agentLabel, statusLabel } from "@/lib/researchActivity";
import { latestStages, pendingReviewStage, reviewFocus } from "@/lib/runReview";
import { ArtifactReviewDocument } from "./ArtifactReviewDocument";
import { ResearchReviewPrompt } from "./ResearchReviewPrompt";
import { ExecutionConfigurationReview } from "./ExecutionConfigurationReview";
import { ReportsPanel } from "./ReportsPanel";

export function ResearchRunWorkspace({ run, stale, onChanged }: { run: RunDetail | null; stale: boolean; onChanged: () => Promise<void> }): JSX.Element | null {
  const pathname = usePathname();
  const search = useSearchParams();
  const [selected, setSelected] = useState("");
  const [documents, setDocuments] = useState<Partial<Record<Stage, ArtifactView | null>>>({});
  const [notice, setNotice] = useState("");
  const pending = run ? pendingReviewStage(run) : null;
  const stages = run ? latestStages(run) : [];
  const stage = run ? reviewFocus(run, selected) : null;
  const state = stages.find(item => item.stage === stage)?.state ?? "unknown";
  useEffect(() => { if (pending) setSelected(pending); }, [pending, run?.run_id]);
  const receiveArtifact = useCallback((artifact: ArtifactView | null): void => {
    if (stage) setDocuments(previous => ({ ...previous, [stage]: artifact }));
  }, [stage]);
  if (!run) return null;
  const pendingArtifact = pending ? documents[pending] ?? null : null;
  const sourceQuery = search?.toString() || "";
  const labParams = new URLSearchParams({ project: run.project, run: run.run_id, returnTo: `${pathname}${sourceQuery ? `?${sourceQuery}` : ""}` });
  return <section aria-label="研究工作区" className="space-y-4">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-base font-medium text-slate-100">本次研究</h2>
      <div className="flex flex-wrap items-center gap-3">
        <span className={`text-xs ${pending ? "text-amber-300" : "text-slate-400"}`}>{stale ? "状态更新中断" : pending ? "等待审核" : statusLabel(run.status || "unknown")}</span>
        <Link href={`/lab?${labParams}`} target="_blank" rel="noopener noreferrer" className="rounded-lg border border-mars-border px-3 py-2 text-xs text-indigo-200 hover:border-indigo-400/60 hover:text-indigo-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400">打开实验台 <span aria-hidden="true">↗</span></Link>
      </div>
    </header>
    <nav aria-label="对话中的研究阶段" className="flex flex-wrap gap-2">{stages.map(item => <button type="button" key={item.stage} onClick={() => setSelected(item.stage)} aria-pressed={item.stage === stage} className={`rounded-lg border px-3 py-2 text-xs ${item.stage === stage ? "border-indigo-400/60 bg-indigo-400/10 text-indigo-100" : "border-mars-border text-slate-400 hover:text-slate-200"}`}>{agentLabel(item.stage)}<span className="ml-2 opacity-70">{statusLabel(item.state)}</span></button>)}</nav>
    {notice ? <p role="status" className="text-sm text-indigo-200">{notice}</p> : null}
    {stage ? <ArtifactReviewDocument key={`${run.run_id}:${stage}`} run={run} stage={stage} state={state} stale={stale} advanced={`/runs/${encodeURIComponent(run.run_id)}?view=advanced&agent=${stage}`} onChanged={onChanged} onNotice={setNotice} onArtifact={receiveArtifact} expanded reviewActions={false} initiallyCollapsed /> : <p className="text-sm text-slate-400">暂无阶段文档。</p>}
    <ResearchReviewPrompt run={run} artifact={pendingArtifact} stale={stale} onChanged={onChanged} />
    <ExecutionConfigurationReview key={run.run_id} runId={run.run_id} project={run.project} stale={stale} onChanged={onChanged} />
    {stage === "writing" || stages.some(item => item.stage === "writing" && item.state === "done") ? <ReportsPanel key={`reports:${run.run_id}`} runId={run.run_id} refreshKey={`${run.status}:${stages.find(item => item.stage === "writing")?.state}`} /> : null}
  </section>;
}
