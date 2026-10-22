"use client";

import { useI18n } from "@/lib/i18n";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { ArtifactView, RunDetail, Stage } from "@/lib/api";
import { continueEvolution, getEvolution, type EvolutionView } from "@/lib/api";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { agentLabel, statusLabel } from "@/lib/researchActivity";
import { latestStages, pendingReviewStage, reviewFocus } from "@/lib/runReview";
import { ArtifactReviewDocument } from "./ArtifactReviewDocument";
import { ResearchReviewPrompt } from "./ResearchReviewPrompt";
import { ExecutionConfigurationReview } from "./ExecutionConfigurationReview";
import { ReportsPanel } from "./ReportsPanel";
import { RunControlBar } from "./RunControlBar";

export function ResearchRunWorkspace({ run, stale, onChanged }: { run: RunDetail | null; stale: boolean; onChanged: () => Promise<void> }): JSX.Element | null {
  const { ui } = useI18n();
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
  return <section aria-label={ui("研究工作区")} className="space-y-4">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-base font-medium text-slate-100">{ui("本次研究")}</h2>
      <div className="flex flex-wrap items-center gap-3">
        <span className={`text-xs ${pending ? "text-amber-300" : "text-slate-400"}`}>{stale ? ui("状态更新中断") : pending ? ui("等待审核") : ui(statusLabel(run.status || "unknown"))}</span>
        <Link href={`/lab?${labParams}`} className="rounded-lg border border-mars-border px-3 py-2 text-xs text-indigo-200 hover:border-indigo-400/60 hover:text-indigo-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400">{ui("打开实验台")}</Link>
      </div>
    </header>
    <RunControlBar runId={run.run_id} run={run} compact showSimpleLink={false} onChange={() => void onChanged()} />
    <EvolutionProgress key={run.run_id} run={run} onChanged={onChanged} />
    <nav aria-label={ui("对话中的研究阶段")} className="flex flex-wrap gap-2">{stages.map(item => <button type="button" key={item.stage} onClick={() => setSelected(item.stage)} aria-pressed={item.stage === stage} className={`rounded-lg border px-3 py-2 text-xs ${item.stage === stage ? "border-indigo-400/60 bg-indigo-400/10 text-indigo-100" : "border-mars-border text-slate-400 hover:text-slate-200"}`}>{ui(agentLabel(item.stage))}<span className="ml-2 opacity-70">{ui(statusLabel(item.state))}</span></button>)}</nav>
    {notice ? <p role="status" className="text-sm text-indigo-200">{notice}</p> : null}
    {stage ? <ArtifactReviewDocument key={`${run.run_id}:${stage}`} run={run} stage={stage} state={state} stale={stale} advanced={`/runs/${encodeURIComponent(run.run_id)}?view=workbench&agent=${stage}`} onChanged={onChanged} onNotice={setNotice} onArtifact={receiveArtifact} expanded reviewActions={false} initiallyCollapsed /> : <p className="text-sm text-slate-400">{ui("暂无阶段文档。")}</p>}
    <ResearchReviewPrompt run={run} artifact={pendingArtifact} stale={stale} onChanged={onChanged} />
    <ExecutionConfigurationReview key={run.run_id} run={run} runId={run.run_id} project={run.project} stale={stale} onChanged={onChanged} />
    {run.managed_review?.kind === "feedback" && run.managed_review.reason ? <p role="status" className="rounded-lg border border-mars-border p-3 text-xs text-slate-300">{ui("主控复核：")}{run.managed_review.reason}</p> : null}
    {stage === "writing" || stages.some(item => item.stage === "writing" && item.state === "done") ? <ReportsPanel key={`reports:${run.run_id}`} runId={run.run_id} refreshKey={`${run.status}:${stages.find(item => item.stage === "writing")?.state}`} /> : null}
  </section>;
}

function EvolutionProgress({ run, onChanged }: { run: RunDetail; onChanged: () => Promise<void> }): JSX.Element | null {
  const { ui, lang } = useI18n();
  const [view, setView] = useState<EvolutionView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let disposed = false;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function refresh(): Promise<void> {
      if (disposed || inFlight) return;
      inFlight = true;
      let poll = !["completed", "failed", "stopped"].includes(run.status || "");
      try {
        const result = await getEvolution(run.run_id, run.project);
        if (disposed) return;
        setView(result.evolution); setError("");
        poll = poll && result.evolution !== null;
      } catch (cause) { if (!disposed) setError(cause instanceof Error ? cause.message : String(cause)); }
      finally {
        inFlight = false;
        if (!disposed && poll) timer = setTimeout(() => void refresh(), CLIENT_POLICY.controlRefreshMs);
      }
    }
    const onFocus = (): void => { if (timer) clearTimeout(timer); void refresh(); };
    window.addEventListener("focus", onFocus);
    void refresh();
    return () => { disposed = true; if (timer) clearTimeout(timer); window.removeEventListener("focus", onFocus); };
  }, [run.run_id, run.project, run.status]);
  if (!view) return error ? <p role="status" className="text-xs text-amber-200">{ui(error)}</p> : null;
  const english = lang === "en";
  const firstSentence = view.contract.goal.split("。")[0];
  const goalPreview = firstSentence.length > 240 ? `${firstSentence.slice(0, 240)}…` : firstSentence;
  const labels: Record<string, string> = english ? { researching: "Researching", evolving: "Evolving", verifying: "Verifying", goal_met: "Goal verified", budget_exhausted: "Budget exhausted", plateau: "Stopped at plateau" } : { researching: "调研与方案筛选", evolving: "依据结果进化", verifying: "保持代码不变复验", goal_met: "目标已达成并复验", budget_exhausted: "预算结束，目标未复验达成", plateau: "连续未改善，已停止" };
  return <div className="rounded-xl border border-indigo-400/30 bg-indigo-400/5 p-4 text-sm">
    <div className="flex flex-wrap justify-between gap-2"><p>{english ? "Auto Research" : "自动研究与进化"}</p><p className="text-indigo-200">{labels[view.status] || view.status}</p></div>
    <p className="mt-2 text-slate-300">{goalPreview}</p>
    {goalPreview !== view.contract.goal ? <details className="mt-2 text-xs text-slate-300"><summary className="cursor-pointer text-indigo-200">{english ? "Full goal and constraints" : "完整目标与约束"}</summary><p className="mt-2">{view.contract.goal}</p></details> : null}
    {view.proposal ? <details className="mt-3 text-xs text-slate-300"><summary className="cursor-pointer text-indigo-200">{english ? "Selected research proposal and scope" : "优先验证方案与修改范围"}</summary><p className="mt-2">{view.proposal.mechanism}</p>{view.proposal.modules.map(item => <p key={`${item.module}:${item.path}`} className="mt-1 text-slate-400">{item.module}: {item.path}</p>)}</details> : null}
    <p className="mt-2 text-xs text-slate-400">{english ? "Iterations" : "已完成轮次"} {view.attempts.length}/{view.contract.max_iterations} · {english ? "Unchanged verifications" : "同版复验"} {view.verification_count}/{view.contract.verification_runs}{view.best ? ` · ${english ? "Best iteration" : "当前最优"} ${view.best.attempt}` : ""}</p>
    {view.attempts.length ? <details className="mt-3"><summary className="cursor-pointer text-indigo-200">{english ? "Measurements and goal gaps" : "查看逐轮指标与目标差距"}</summary><div className="mt-2 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th className="p-2">{english ? "Round" : "轮次"}</th><th className="p-2">{english ? "Candidate" : "候选"}</th><th className="p-2">{english ? "Observed / Target / Gap" : "实测 / 目标 / 差距"}</th><th className="p-2">{english ? "Outcome" : "结果"}</th></tr></thead><tbody>{view.attempts.map(item => <tr key={item.attempt} className="border-t border-mars-border"><td className="p-2 align-top">{item.iteration ?? item.attempt}</td><td className="p-2 align-top">{item.winner?.experiment_id || "—"}</td><td className="p-2">{item.winner?.checks.map(check => <div key={check.name} className="mb-2 last:mb-0">{check.baseline !== null && check.value !== null ? <p className="text-slate-400">{check.name}: {english ? "Candidate" : "候选"} {check.value.toPrecision(6)} · {english ? "Baseline" : "基线"} {check.baseline.toPrecision(6)}</p> : null}<p>{check.name}{check.comparison === "candidate_minus_baseline" ? (english ? " Δ (candidate − baseline)" : " 差值（候选 − 基线）") : check.comparison === "candidate_over_baseline" ? (english ? " ratio" : " 比值") : ""}: {check.observed?.toPrecision(5) ?? "—"} / {check.direction === "lte" ? "≤" : "≥"}{check.target} / {check.gap?.toPrecision(3) ?? "—"}</p></div>)}{item.protocol_errors.map(message => <p key={message} className="text-amber-200">{message}</p>)}</td><td className="p-2 align-top">{labels[item.decision.status] || item.decision.status}</td></tr>)}</tbody></table></div></details> : null}
    {run.status === "waiting_feedback" && view.last_decision?.continue ? <button type="button" disabled={busy} className="mt-3 rounded-lg bg-mars-accent px-3 py-2 text-xs disabled:opacity-50" onClick={() => {
      setBusy(true); void continueEvolution(run.run_id, run.project).then(async result => {
        if (!result.ok) throw new Error(result.status); await onChanged();
      }).catch(cause => setError(cause instanceof Error ? cause.message : String(cause))).finally(() => setBusy(false));
    }}>{busy ? (english ? "Continuing…" : "正在继续…") : (english ? "Continue evolution" : "批准并继续进化")}</button> : null}
    {error ? <p role="alert" className="mt-2 text-amber-200">{ui(error)}</p> : null}
  </div>;
}
