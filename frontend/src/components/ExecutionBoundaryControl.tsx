"use client";

import { useI18n } from "@/lib/i18n";

import { useCallback, useEffect, useRef, useState } from "react";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { BoundaryRequestOrder, type ExecutionBoundary } from "@/lib/executionBoundaryState";
import { ExecutionReviewError, getExecutionBoundary, setExecutionBoundary } from "@/lib/executionReview";

function selectionMessage(stop: boolean): string {
  return stop ? "仿真完成后暂停，暂不生成报告。" : "仿真完成后按原流程继续。";
}

export function ExecutionBoundaryControl({ runId, project }: { runId: string; project: string }): JSX.Element {
  const { ui } = useI18n();
  const [boundary, setBoundary] = useState<ExecutionBoundary | null>(null);
  const [saving, setSaving] = useState(false);
  const [readError, setReadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [notice, setNotice] = useState("");
  const [retryValue, setRetryValue] = useState<boolean | null>(null);
  const [order] = useState(() => new BoundaryRequestOrder());
  const reading = useRef<AbortController | null>(null);
  const uncertainValue = useRef<boolean | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    if (reading.current) return;
    const ticket = order.beginRead();
    if (ticket === null) return;
    const controller = new AbortController();
    reading.current = controller;
    try {
      const next = await getExecutionBoundary(runId, project, controller.signal);
      if (!controller.signal.aborted && order.acceptsRead(ticket)) {
        setBoundary(next); setReadError("");
        if (uncertainValue.current !== null) {
          if (next.stop_after_execution === uncertainValue.current) {
            setSaveError(""); setRetryValue(null);
            setNotice("已核对当前设置：" + selectionMessage(next.stop_after_execution));
          } else {
            setSaveError("未核对到本次设置，请重试保存。");
          }
          uncertainValue.current = null;
        }
      }
    } catch (issue) {
      if (!controller.signal.aborted && order.acceptsRead(ticket)) {
        setReadError(issue instanceof Error ? issue.message : "暂停设置暂时无法读取，请刷新重试。");
      }
    } finally {
      if (reading.current === controller) reading.current = null;
    }
  }, [order, runId, project]);

  useEffect(() => {
    order.invalidate(); setBoundary(null); setSaving(false);
    setReadError(""); setSaveError(""); setNotice(""); setRetryValue(null);
    uncertainValue.current = null;
    void refresh();
    const timer = setInterval(() => { void refresh(); }, CLIENT_POLICY.controlRefreshMs);
    return () => {
      order.invalidate(); reading.current?.abort(); reading.current = null;
    };
  }, [order, refresh]);

  async function save(stop: boolean): Promise<void> {
    if (!boundary?.can_update) return;
    const ticket = order.beginSave();
    if (ticket === null) return;
    reading.current?.abort(); reading.current = null;
    uncertainValue.current = null;
    setSaving(true); setSaveError(""); setReadError(""); setNotice(""); setRetryValue(stop);
    let reconcile = false;
    try {
      const next = await setExecutionBoundary(runId, project, stop);
      if (order.acceptsSave(ticket)) {
        if (next.stop_after_execution !== stop) throw new Error("返回的设置与本次选择不一致，请重新核对。");
        setBoundary(next); setRetryValue(null);
        setNotice("已保存：" + selectionMessage(next.stop_after_execution));
      }
    } catch (issue) {
      if (order.acceptsSave(ticket)) {
        const knownRefusal = issue instanceof ExecutionReviewError && issue.status >= 400 && issue.status < 500 && issue.status !== 408;
        if (knownRefusal) {
          setSaveError("未能保存：" + issue.message);
        } else {
          uncertainValue.current = stop;
          setSaveError("连接中断，正在核对是否保存成功。");
        }
        reconcile = true;
      }
    } finally {
      if (order.finishSave(ticket)) {
        setSaving(false);
        if (reconcile) void refresh();
      }
    }
  }

  const description = `execution-boundary-${runId}`;
  return <section id={`execution-boundary-control-${runId}`} tabIndex={-1} aria-busy={saving} className="space-y-2 rounded-xl border border-mars-border bg-mars-panel px-4 py-3 outline-none">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <label className="flex items-center gap-2 text-sm text-slate-200">
        <input type="checkbox" aria-describedby={description} checked={boundary?.stop_after_execution === true} disabled={saving || !boundary?.can_update} onChange={event => { void save(event.target.checked); }} />

        {ui("仿真完成后暂停，暂不生成报告")}
      </label>
      <button type="button" disabled={saving} onClick={() => { void refresh(); }} className="text-xs text-indigo-300 disabled:opacity-40">{ui("刷新暂停设置")}</button>
    </div>
    <p id={description} className="text-xs leading-5 text-slate-500">{ui("只设置本轮仿真完成后的推进方式，不中断当前仿真，也不改变训练配置。")}</p>
    {saving ? <p role="status" className="text-xs text-indigo-200">正在保存暂停设置…</p> : null}
    {!boundary && !readError ? <p role="status" className="text-xs text-slate-400">正在读取当前暂停设置…</p> : null}
    {boundary && !boundary.can_update ? <p role="status" className="text-xs text-amber-200">{boundary.reason || "当前任务已进入后续阶段，暂停设置不可再修改。"}</p> : null}
    {notice ? <p role="status" className="text-xs text-emerald-300">{notice}</p> : null}
    {readError ? <p role="alert" className="text-xs text-amber-200">{readError}</p> : null}
    {saveError ? <div className="flex flex-wrap items-center gap-3"><p role="alert" className="text-xs text-amber-200">{saveError}</p>{retryValue !== null ? <button type="button" disabled={saving || !boundary?.can_update} onClick={() => { if (retryValue !== null) void save(retryValue); }} className="text-xs text-indigo-300 disabled:opacity-40">{ui("重试保存")}</button> : null}</div> : null}
  </section>;
}
