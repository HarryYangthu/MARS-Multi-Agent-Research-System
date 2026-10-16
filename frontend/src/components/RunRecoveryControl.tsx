"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getRunRecovery, recoverRun, listDataSources, type DataSourceProfile, type RecoveryAction, type RunRecovery } from "@/lib/api";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { reviewResultUncertain } from "@/lib/apiError";
import { agentLabel } from "@/lib/researchActivity";
import { runDetailsUrl } from "@/lib/runConversation";

export function RunRecoveryControl({ runId, project, disabled = false, onChanged }: { runId: string; project: string; disabled?: boolean; onChanged?: () => void | Promise<void> }): JSX.Element | null {
  const [view, setView] = useState<RunRecovery | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [revision, setRevision] = useState(0);
  const [dataAction, setDataAction] = useState<RecoveryAction | null>(null);
  const [sources, setSources] = useState<DataSourceProfile[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [dataError, setDataError] = useState("");
  const [loadingSources, setLoadingSources] = useState(false);
  const [reason, setReason] = useState("请读取所选数据的实际字段、形状、采样率和载波信息，核对原方案及代码是否适用；修订实验设计和代码后重新审核。不确定的物理含义请询问我。");
  const submitting = useRef(false);
  const successNotice = useRef(false);
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
        if (!controller.signal.aborted) {
          setView(value); setError("");
          if (successNotice.current) { setMessage(""); successNotice.current = false; }
        }
      } catch (cause: unknown) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "恢复状态暂时无法读取");
      } finally { reading = false; }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), CLIENT_POLICY.controlRefreshMs);
    return () => { controller.abort(); clearInterval(timer); };
  }, [runId, project, revision]);

  useEffect(() => {
    if (!dataAction) return;
    let cancelled = false;
    setLoadingSources(true); setDataError("");
    void listDataSources(project).then(items => {
      if (!cancelled) setSources(items);
    }).catch((cause: unknown) => {
      if (!cancelled) setDataError(cause instanceof Error ? cause.message : "数据列表读取失败，请重试。");
    }).finally(() => { if (!cancelled) setLoadingSources(false); });
    return () => { cancelled = true; };
  }, [project, dataAction]);

  async function act(action: RecoveryAction): Promise<void> {
    if (!view || submitting.current || disabled || error || uncertain) return;
    if (action.action === "revise_data" && (!sourceId || !reason.trim())) return;
    submitting.current = true; successNotice.current = false; setBusy(true); setMessage("");
    try {
      const result = await recoverRun(runId, project, action, view.token,
        action.action === "revise_data" ? { source_id: sourceId, reason } : undefined);
      if (mounted.current) {
        successNotice.current = true; setMessage(result.message); setView(null); setDataAction(null); setRevision(value => value + 1);
        await onChanged?.();
      }
    } catch (cause: unknown) {
      if (mounted.current) {
        const unknown = reviewResultUncertain(cause, true);
        setMessage(unknown ? "连接中断，恢复请求是否生效尚未确认。请核对状态，勿重复提交。" : cause instanceof Error ? cause.message : "恢复未启动");
        setUncertain(unknown);
        if (!unknown) { setView(null); setRevision(value => value + 1); }
      }
    } finally { submitting.current = false; if (mounted.current) setBusy(false); }
  }
  if (!error && !message && (!view || ["idle", "running"].includes(view.status))) return null;
  return <section aria-label="任务恢复" className="rounded-xl border border-amber-400/25 bg-amber-400/5 px-4 py-3 text-sm">
    <p role="status" className="leading-6 text-amber-100">{message || error || view?.message}</p>
    <div className="mt-3 flex flex-wrap items-center gap-3">
      {view?.actions.map(action => <button key={`${action.action}:${action.node}`} type="button" disabled={disabled || busy || !!error || uncertain}
        onClick={() => action.action === "revise_data" ? setDataAction({ ...action }) : void act(action)} className="rounded-lg bg-mars-accent px-3 py-2 text-xs text-white disabled:opacity-40">
        {busy ? "正在提交…" : action.action === "revise_data" ? action.label : `${action.label} · ${agentLabel(action.node.replace(/_attempt_\d+$/, ""))}`}
      </button>)}
      {(error || uncertain) ? <button type="button" disabled={busy} onClick={() => { setUncertain(false); setView(null); setMessage(""); setRevision(value => value + 1); }} className="text-xs text-indigo-300">核对状态</button> : null}
      <Link href={runDetailsUrl(runId, view?.actions[0]?.node)} className="text-xs text-slate-400 hover:underline">查看任务详情</Link>
    </div>
    {dataAction && view?.actions.some(action => action.action === "revise_data") ? <form className="mt-4 space-y-3 border-t border-amber-400/20 pt-4" onSubmit={event => { event.preventDefault(); void act(dataAction); }}>
      <label className="block text-xs text-slate-300">本次研究使用的数据
        <select value={sourceId} onChange={event => setSourceId(event.target.value)} disabled={busy || loadingSources}
          className="mt-2 block w-full rounded-lg border border-mars-border bg-mars-bg px-3 py-2 text-sm">
          <option value="">{loadingSources ? "正在读取数据…" : "请选择文件"}</option>
          {sources.map(source => <option key={source.id} value={source.id}>{source.original_name}</option>)}
        </select>
      </label>
      {dataError ? <p role="alert" className="text-xs text-amber-200">{dataError}</p> : null}
      {!loadingSources && !dataError && !sources.length ? <p className="text-xs text-slate-300">本项目尚无数据，请先到项目设置上传，再重新打开此表单。</p> : null}
      <label className="block text-xs text-slate-300">修订说明（可补充带宽、评价口径等要求）
        <textarea value={reason} onChange={event => setReason(event.target.value)} disabled={busy} rows={3}
          className="mt-2 block w-full rounded-lg border border-mars-border bg-mars-bg px-3 py-2 text-sm" />
      </label>
      <p className="text-xs leading-5 text-slate-400">保留原方案与代码记录，新增实验设计和编码修订。主控托管开启时由主控审核；本操作不批准仿真。</p>
      <button type="submit" disabled={disabled || busy || !!error || uncertain || loadingSources || !!dataError || !sourceId || !reason.trim()}
        className="rounded-lg bg-mars-accent px-3 py-2 text-xs text-white disabled:opacity-40">{busy ? "正在提交…" : "按所选数据修订并继续"}</button>
      <button type="button" disabled={busy} onClick={() => setDataAction(null)} className="ml-3 text-xs text-slate-400">取消</button>
    </form> : null}
  </section>;
}
