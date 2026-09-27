"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getRun, type RunDetail } from "@/lib/api";
import { boundedFetch, CLIENT_POLICY, isUncertainRequestError } from "@/lib/clientPolicy";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
const STATUS: Record<string, string> = { created: "尚未启动", running: "研究中", waiting_review: "等待审核", waiting_feedback: "等待处理", cancelling: "正在停止", stopped: "已停止", failed: "任务失败", completed: "流程已结束" };
const READ_ONLY: Record<string, string> = {
  research_contract_execution_pending: "研究配置已保存。部分预算和修改边界尚未接入执行器，当前不能启动。",
  research_contract_integrity_error: "保存的研究配置缺失或完整性校验失败，当前不能启动或恢复。",
  legacy_state_migration_required: "这是历史任务。需先校验并迁移执行状态，才能判断能否恢复。迁移会保留备份，不会自动开展研究。",
};
const ERRORS: Record<string, string> = {
  not_owned: "当前服务没有正在执行此任务的工作进程。没有修改历史执行状态。",
  stop_incomplete: "停止尚未完成，请刷新核对。现有作业可能仍在清理。",
  stop_state_error: "工作进程停止与状态保存未能同时完成，请检查任务记录后再恢复。",
  driver_busy: "任务已有执行进程，请刷新核对，避免重复启动。",
  contract_execution_blocked: "研究配置尚未具备完整的执行约束，启动已被阻止。",
  resume_unavailable: "没有可安全恢复的检查点，请查看任务记录。",
};

type OwnerControl = {
  owned_task_active: boolean;
  stopping: boolean;
  owned_task_done: boolean | null;
  cleanup_complete: boolean | null;
  state_persisted: boolean | null;
  available_actions: string[];
};

export function RunControlBar({ runId, run, onChange }: { runId: string; run: RunDetail | null; onChange: (run: RunDetail) => void }): JSX.Element {
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const [control, setControl] = useState<OwnerControl | null>(null);
  const [controlError, setControlError] = useState(false);
  const mutation = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh(): Promise<void> {
      try {
        const response = await boundedFetch(`${BASE}/api/runs/${encodeURIComponent(runId)}/control`, { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("control unavailable");
        const value = await response.json() as OwnerControl;
        if (!controller.signal.aborted) { setControl(value); setControlError(false); }
      } catch {
        if (!controller.signal.aborted) setControlError(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void refresh(), CLIENT_POLICY.controlRefreshMs);
      }
    }
    void refresh();
    return () => { controller.abort(); mutation.current?.abort(); clearTimeout(timer); };
  }, [runId]);
  const cancelled = run?.termination?.type === "cancelled";
  const statusLabel = cancelled
    ? (run?.termination?.cleanup_complete ? "用户已停止" : "停止清理待确认")
    : STATUS[run?.status || ""] || "状态待核对";
  async function act(action: "start" | "stop" | "resume" | "migrate-state"): Promise<void> {
    if (busy) return;
    const controller = new AbortController();
    mutation.current = controller;
    setBusy(action); setError(false); setMessage("");
    try {
      const response = await boundedFetch(`${BASE}/api/runs/${encodeURIComponent(runId)}/${action}`, { method: "POST", signal: controller.signal });
      if (!response.ok) {
        const payload: unknown = await response.json();
        const detail = typeof payload === "object" && payload !== null && "detail" in payload ? payload.detail : null;
        const status = typeof detail === "object" && detail !== null && "status" in detail && typeof detail.status === "string" ? detail.status : "";
        throw new Error(ERRORS[status] || "请求未通过。请检查配置、检查点或任务完整性后重试。");
      }
      if (controller.signal.aborted) return;
      setMessage(action === "stop" ? "当前服务确认本任务的工作进程已停止；仍需核对独立实验作业状态。" : action === "migrate-state" ? "历史状态校验与迁移完成，尚未启动研究。" : "服务已接受请求，任务状态会继续更新。");
      try { const current = await getRun(runId); if (!controller.signal.aborted) onChange(current); } catch { if (!controller.signal.aborted) setMessage((current) => `${current} 最新状态暂时无法读取，请稍后刷新。`); }
    } catch (cause: unknown) {
      if (controller.signal.aborted) return;
      setError(true);
      setMessage(isUncertainRequestError(cause) ? "连接中断或等待超时，请刷新核对请求是否生效；系统不会自动重试启动或恢复。" : cause instanceof Error ? cause.message : "操作失败，请刷新核对。");
    } finally { if (!controller.signal.aborted) setBusy(""); }
  }
  const button = "rounded border border-mars-border px-3 py-1.5 text-xs hover:bg-mars-panel2 disabled:opacity-50";
  return <section aria-label="任务控制" className="border-b border-mars-border bg-mars-panel px-4 py-3">
    <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap items-center gap-3 text-sm"><Link href="/runs" className="text-slate-400 hover:text-white">← 研究任务</Link><span role="status">{run ? (run.read_only ? "只读记录" : statusLabel) : "正在读取任务…"}</span></div><div className="flex flex-wrap gap-2">
      {run ? <Link href={`/results/${encodeURIComponent(run.run_id)}`} className={button}>结果与导出</Link> : null}
      {run && !run.read_only && run.status === "created" ? <button type="button" className={button} disabled={!!busy} onClick={() => void act("start")}>启动任务</button> : null}
      {control?.available_actions.includes("stop") ? <button type="button" className={`${button} border-rose-400/50 text-rose-200`} disabled={!!busy} onClick={() => void act("stop")}>{busy === "stop" ? "正在请求停止…" : control.stopping ? "核对停止状态" : "停止任务"}</button> : null}
      {run && !run.read_only && ["failed", "stopped"].includes(run.status || "") ? <button type="button" className={button} disabled={!!busy} onClick={() => void act("resume")}>检查并恢复</button> : null}
      {run?.available_actions?.includes("migrate_state") ? <button type="button" className={button} disabled={!!busy} onClick={() => void act("migrate-state")}>{busy ? "正在校验…" : "校验并迁移历史记录"}</button> : null}
    </div></div>
    {run?.read_only ? <p className="mt-2 text-xs leading-5 text-amber-200">{READ_ONLY[run.read_only_reason || ""] || "此记录没有可供当前服务使用的可信执行状态。已保存产物仍可查看。"}</p> : null}
    {controlError ? <p role="status" className="mt-2 text-xs text-amber-200">暂时无法核对工作进程状态，正在重新连接。</p> : null}
    {control?.state_persisted === false ? <p role="alert" className="mt-2 text-xs text-amber-200">停止状态保存失败。{control.owned_task_done ? "本服务的工作进程已经结束。" : "仍需核对工作进程是否完成清理。"}恢复前需修复执行记录。</p> : null}
    {run?.execution_admission?.blockers.length ? <details className="mt-2 text-xs text-slate-400"><summary className="cursor-pointer">查看启动受阻原因</summary><ul className="mt-2 list-inside list-disc space-y-1">{run.execution_admission.blockers.map((blocker) => <li key={blocker.code}>{blocker.message}</li>)}</ul></details> : null}
    {message ? <p role={error ? "alert" : "status"} className={`mt-2 text-xs leading-5 ${error ? "text-amber-200" : "text-emerald-200"}`}>{message}</p> : null}
  </section>;
}
