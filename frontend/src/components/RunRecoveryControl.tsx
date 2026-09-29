"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getRunRecovery, recoverRun, type RecoveryAction, type RunRecovery } from "@/lib/api";
import { CLIENT_POLICY, isUncertainRequestError } from "@/lib/clientPolicy";
import { agentLabel } from "@/lib/researchActivity";

export function RunRecoveryControl({ runId, project, disabled = false }: { runId: string; project: string; disabled?: boolean }): JSX.Element | null {
  const [view, setView] = useState<RunRecovery | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [revision, setRevision] = useState(0);
  const submitting = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let reading = false;
    const refresh = async (): Promise<void> => {
      if (reading) return;
      reading = true;
      try {
        const value = await getRunRecovery(runId, project, controller.signal);
        if (value.project !== project || value.run_id !== runId) throw new Error("恢复记录所属任务不匹配");
        if (!controller.signal.aborted) { setView(value); setError(""); }
      } catch (cause: unknown) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "恢复状态暂时无法读取");
      } finally { reading = false; }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), CLIENT_POLICY.controlRefreshMs);
    return () => { controller.abort(); clearInterval(timer); };
  }, [runId, project, revision]);

  async function act(action: RecoveryAction): Promise<void> {
    if (!view || submitting.current || disabled || error || uncertain) return;
    submitting.current = true; setBusy(true); setMessage("");
    try {
      const result = await recoverRun(runId, project, action, view.token);
      if (mounted.current) { setMessage(result.message); setView(null); setRevision(value => value + 1); }
    } catch (cause: unknown) {
      if (mounted.current) {
        setMessage(isUncertainRequestError(cause) ? "连接中断，恢复请求是否生效尚未确认。请核对状态，勿重复提交。" : cause instanceof Error ? cause.message : "恢复未启动");
        setUncertain(true);
      }
    } finally { submitting.current = false; if (mounted.current) setBusy(false); }
  }
  if (!error && !message && (!view || ["idle", "running"].includes(view.status))) return null;
  return <section aria-label="任务恢复" className="rounded-xl border border-amber-400/25 bg-amber-400/5 px-4 py-3 text-sm">
    <p role="status" className="leading-6 text-amber-100">{message || error || view?.message}</p>
    <div className="mt-3 flex flex-wrap items-center gap-3">
      {view?.actions.map(action => <button key={`${action.action}:${action.node}`} type="button" disabled={disabled || busy || !!error || uncertain}
        onClick={() => void act(action)} className="rounded-lg bg-mars-accent px-3 py-2 text-xs text-white disabled:opacity-40">
        {busy ? "正在提交…" : `${action.label} · ${agentLabel(action.node.replace(/_attempt_\d+$/, ""))}`}
      </button>)}
      {(error || uncertain) ? <button type="button" disabled={busy} onClick={() => { setUncertain(false); setView(null); setMessage(""); setRevision(value => value + 1); }} className="text-xs text-indigo-300">核对状态</button> : null}
      <Link href={`/runs/${encodeURIComponent(runId)}`} className="text-xs text-slate-400 hover:underline">查看任务详情</Link>
    </div>
  </section>;
}
