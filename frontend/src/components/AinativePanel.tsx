"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  detectAinative,
  getAinativeStatus,
  startAinativeGeneration,
  type AinativeDetection,
  type AinativeJob,
  type ProjectSummary,
} from "@/lib/api";

const BUTTON = "rounded border border-mars-border px-3 py-2 text-sm hover:bg-mars-panel2 disabled:opacity-40";
const PRIMARY = "rounded border border-mars-border bg-mars-accent px-3 py-2 text-sm font-medium hover:brightness-110 disabled:opacity-40";

const STEP_LABELS: Record<string, string> = {
  survey: "通读基线代码仓",
  plan: "模型制定迁移计划",
  generate: "生成 torch 仿真代码",
  agent_md: "独立模型会话撰写 AGENTS.md",
  verify: "真实执行验证（编译 + dry-run）",
  bind: "绑定为项目可写工作仓",
};

function StepRow({ step }: { step: { name: string; status: string; detail: string } }): JSX.Element {
  const icon = step.status === "completed" ? "✓" : step.status === "failed" ? "✗" : "…";
  const color = step.status === "completed" ? "text-emerald-300" : step.status === "failed" ? "text-rose-300" : "text-slate-300";
  return <li className={`flex items-start gap-2 text-sm ${color}`}>
    <span aria-hidden>{icon}</span>
    <span>{STEP_LABELS[step.name] || step.name}{step.detail ? <span className="ml-1 text-xs text-slate-400">{step.detail}</span> : null}</span>
  </li>;
}

function Checklist({ detection }: { detection: AinativeDetection }): JSX.Element {
  return <ul className="grid gap-1 sm:grid-cols-2">
    {detection.checks.map((check) => <li key={check.name} className={`text-xs ${check.passed ? "text-emerald-300" : "text-amber-300"}`}>
      {check.passed ? "✓" : "○"} {check.label}
    </li>)}
  </ul>;
}

export function AinativePanel({ project, onRepoChanged }: { project: ProjectSummary; onRepoChanged?: () => void }): JSX.Element {
  const [detection, setDetection] = useState<AinativeDetection | null>(null);
  const [job, setJob] = useState<AinativeJob | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback((): void => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } }, []);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const status = await getAinativeStatus(project.name);
      setDetection(status.detection);
      setJob(status.job ?? null);
      if (!status.job || status.job.status !== "generating") stopPolling();
    } catch { /* transient; next poll retries */ }
  }, [project.name, stopPolling]);

  useEffect((): (() => void) => {
    void refresh();
    return stopPolling;
  }, [refresh, stopPolling]);

  useEffect((): (() => void) => {
    if (job?.status !== "generating") return () => undefined;
    pollRef.current = setInterval(() => { void refresh(); }, 3000);
    return stopPolling;
  }, [job?.status, refresh, stopPolling]);

  async function start(): Promise<void> {
    setBusy(true); setError(""); setMessage(""); setConfirming(false);
    try {
      const result = await startAinativeGeneration(project.name);
      if (result.skipped) {
        setDetection(result.detection);
        setMessage("当前代码仓已符合 AI Native 标准，直接使用，无需生成。");
        return;
      }
      if (result.job) setJob(result.job);
      setMessage("生成任务已启动，耗时取决于基线仓规模；可离开此页，完成后状态保留。");
      onRepoChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "生成任务启动失败，请确认已绑定基线代码仓。");
    } finally { setBusy(false); }
  }

  const generating = job?.status === "generating";
  const completed = job?.status === "completed";
  const failed = job?.status === "failed" || job?.status === "interrupted";

  return <section aria-label="AI Native 代码仓" className="space-y-3 rounded-lg border border-mars-border p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold">AI Native 代码仓</h4>
        {detection?.is_native ? <span className="rounded bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-200">已达标</span>
          : completed ? <span className="rounded bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-200">已生成</span>
          : generating ? <span className="rounded bg-indigo-500/10 px-2 py-0.5 text-xs text-indigo-200">生成中</span>
          : failed ? <span className="rounded bg-rose-500/10 px-2 py-0.5 text-xs text-rose-200">生成失败</span> : null}
      </div>
      {!generating && !completed && !confirming
        ? <button type="button" className={PRIMARY} disabled={busy || !project.repo_path} onClick={() => { setMessage(""); setError(""); void detectAinative(project.name).then((value) => { setDetection(value); setConfirming(!value.is_native); if (value.is_native) setMessage("当前代码仓已符合 AI Native 标准，直接使用，无需生成。"); }).catch(() => setError("检测失败，请确认已绑定基线代码仓。")); }}>AINative 一键生成</button>
        : null}
      {generating ? <button type="button" className={BUTTON} onClick={() => void refresh()}>刷新进度</button> : null}
    </div>
    <p className="text-xs text-slate-400">
      统一标准：torch 架构（GPU/CPU 自适应）· 仓根 AGENTS.md（独立模型通读仓后生成，研究人员可维护）· TensorBoard 实验可视化 · checkpoint。参考 pimc 架构；已达标则直接使用不重复生成。
    </p>
    {detection ? <Checklist detection={detection} /> : null}
    {confirming ? <div role="alert" className="space-y-2 rounded border border-amber-400/30 bg-amber-500/5 p-3">
      <p className="text-sm text-amber-200">基线仓未完全达标。生成会通读代码仓并多轮调用模型，可能耗时较长；生成结果写入项目内 ainative/ 可写副本，原基线保持只读。确定开始？</p>
      <div className="flex gap-2"><button type="button" className={PRIMARY} disabled={busy} onClick={() => void start()}>{busy ? "启动中…" : "开始生成"}</button><button type="button" className={BUTTON} onClick={() => setConfirming(false)}>取消</button></div>
    </div> : null}
    {job ? <div className="space-y-2 rounded border border-mars-border p-3">
      <p className="text-xs text-slate-400">任务 {job.id.slice(0, 8)} · 基线 {job.baseline_repo}</p>
      <ul className="space-y-1">{job.steps.map((step, index) => <StepRow key={`${step.name}-${index}`} step={step} />)}</ul>
      {generating ? <p role="status" className="text-xs text-indigo-200">当前步骤：{STEP_LABELS[job.step] || job.step}…</p> : null}
      {completed ? <p className="text-xs text-emerald-200">生成完成：{job.repo_path}（已绑定为可写工作仓，原基线保持只读）</p> : null}
      {failed ? <p role="alert" className="break-all text-xs text-rose-300">{job.error || "生成中断"}</p> : null}
    </div> : null}
    {message ? <p role="status" className="text-sm text-emerald-200">{message}</p> : null}
    {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
  </section>;
}
