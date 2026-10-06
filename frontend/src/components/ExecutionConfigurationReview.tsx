"use client";

import { useEffect, useRef, useState } from "react";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { confirmExecutionConfiguration, setExecutionBoundary, ExecutionReviewError, getExecutionConfiguration, type ExecutionConfiguration } from "@/lib/executionReview";

const labels: Record<string, string> = {
  device: "运行位置", runtime_backend: "实际执行方式", configured_backend: "配置中的执行方式",
  backend_source: "配置来源",
  repository: "代码目录", branch: "研究分支", config_path: "实际训练配置", data_path: "数据路径",
  python: "Python 环境", max_concurrency: "同时运行实验数", batch_steps: "备用默认预算",
  max_iters: "本组训练预算", approved_budget: "每组批准的训练预算", dry_run: "仅检查环境", timeout_seconds: "单作业超时（秒）",
  host: "服务器", user: "用户名", remote_root: "远端工作目录", gpu_ids: "GPU 编号",
  stop_after_execution: "仿真完成后暂停", budget_unit: "训练预算单位",
  training_epochs: "实际配置中的训练轮数", training_seed: "实际配置中的随机种子",
};
const backends: Record<string, string> = { paper_static: "论文训练适配器", local_command: "项目启动命令", remote_gpu: "远端 GPU", pim_cpu: "本地 PIM 仿真", environment: "本地启动配置", execution_config: "执行配置文件", default: "应用默认配置", steps: "参数更新次数", epochs: "完整训练轮次" };
function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未配置";
  if (typeof value === "boolean") return value ? "是" : "否";
  return typeof value === "string" ? backends[value] || value : JSON.stringify(value);
}

export function ExecutionConfigurationReview({ runId, project, stale, onChanged }: {
  runId: string; project: string; stale: boolean; onChanged: () => Promise<void>;
}): JSX.Element | null {
  const [view, setView] = useState<ExecutionConfiguration | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [acknowledged, setAcknowledged] = useState("");
  const [uncertain, setUncertain] = useState("");
  const submitting = useRef(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const presented = useRef(new Set<string>());
  const changed = useRef(onChanged);
  useEffect(() => { changed.current = onChanged; }, [onChanged]);
  useEffect(() => {
    const controller = new AbortController();
    let reading = false;
    const refresh = async (): Promise<void> => {
      if (reading) return;
      reading = true;
      try {
        const next = await getExecutionConfiguration(runId, project, controller.signal);
        if (!controller.signal.aborted) { setView(next); setError(""); }
      } catch (issue) {
        if (!controller.signal.aborted) setError(issue instanceof Error ? issue.message : "配置状态更新中断。");
      } finally { reading = false; }
    };
    void refresh();
    const interval = setInterval(() => { void refresh(); }, CLIENT_POLICY.controlRefreshMs);
    return () => { controller.abort(); clearInterval(interval); };
  }, [runId, project]);
  const token = view?.token || "";
  const launchReady = Boolean(view?.visible && view.launch_ready && !view.confirmed);
  useEffect(() => {
    if (!token || !launchReady || stale || error || presented.current.has(token)) return;
    if (!dialog.current?.open) { dialog.current?.showModal(); heading.current?.focus({ preventScroll: true }); }
    presented.current.add(token);
  }, [token, launchReady, stale, error]);
  useEffect(() => {
    if (uncertain && view?.token && (view.token !== uncertain || view.confirmed)) {
      setUncertain(""); setBusy(false); submitting.current = false; dialog.current?.close();
      setNotice(view.token === uncertain ? "已核对到配置确认记录，请查看执行状态。" : "配置已发生变化，需重新核对；请同时查看任务执行状态。");
      void changed.current().catch(() => setNotice("配置已确认，任务状态暂未更新，请刷新核对。"));
    }
  }, [uncertain, view?.token, view?.confirmed]);
  useEffect(() => { if (view && !view.visible && !busy) dialog.current?.close(); }, [view?.visible, busy]);

  async function refresh(): Promise<void> {
    try { setView(await getExecutionConfiguration(runId, project)); setError(""); }
    catch (issue) { setError(issue instanceof Error ? issue.message : "配置核对失败。"); }
  }
  async function boundary(stop: boolean): Promise<void> {
    setBusy(true); setAcknowledged("");
    try { setView(await setExecutionBoundary(runId, project, stop)); setError(""); }
    catch (issue) { setError(issue instanceof Error ? issue.message : "阶段边界保存失败。"); }
    finally { setBusy(false); }
  }
  async function confirm(): Promise<void> {
    if (!view?.can_confirm || view.confirmed || acknowledged !== token || submitting.current || stale || error) return;
    submitting.current = true; setBusy(true); setNotice("");
    try {
      const result = await confirmExecutionConfiguration(runId, project, token);
      if (result.confirmed) {
        dialog.current?.close();
        setNotice(result.ok ? "仿真配置已确认，正在推进执行。" : "配置确认已保存，任务尚未启动，请核对任务恢复状态。");
        await refresh();
        await changed.current().catch(() => setNotice("配置已确认，任务状态暂未更新，请刷新核对。"));
      }
      submitting.current = false; setBusy(false);
    } catch (issue) {
      if (issue instanceof ExecutionReviewError && issue.status >= 400 && issue.status < 500 && issue.status !== 408) {
        submitting.current = false; setBusy(false); setAcknowledged("");
        setNotice(issue.message); await refresh();
      } else {
        setUncertain(token); setNotice("提交结果待核对，正在读取确认记录；请勿重复启动。");
      }
    }
  }
  function retryConfirmation(): void {
    if (uncertain !== token || !view?.can_confirm || view.confirmed || stale || error) return;
    // Retry the same immutable confirmation identity. The server reuses its
    // receipt and owned driver, including after a lost HTTP response.
    submitting.current = false; setUncertain("");
    void confirm();
  }
  if (!view?.visible) return error ? <p role="status" className="text-xs text-amber-300">{error}</p> : notice ? <p role="status" className="text-xs text-slate-400">{notice}</p> : null;
  if (view.state === "done") return <details className="rounded-xl border border-mars-border bg-mars-panel px-4 py-3"><summary className="cursor-pointer text-sm text-slate-200">仿真已完成<span className="ml-3 text-xs text-slate-400">{view.jobs.length} 条真实作业记录 · 展开结果</span></summary><ul className="mt-3 space-y-3">{view.jobs.map(job => <li key={`${job.experiment_id}-${job.updated_at}`} className="rounded-lg border border-mars-border p-3 text-xs"><p className="text-slate-200">{job.experiment_id} · {job.status === "completed" ? "已完成" : job.status === "interrupted" ? "已中断" : "失败"}{job.duration_seconds !== undefined && job.duration_seconds !== null ? ` · ${job.duration_seconds.toFixed(1)} 秒` : ""}</p>{Object.keys(job.metrics).length ? <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-2">{Object.entries(job.metrics).map(([name, value]) => <div key={name}><dt className="text-slate-500">{name}</dt><dd className="mt-1 text-slate-300">{value}</dd></div>)}</dl> : null}{job.error ? <p className="mt-2 text-amber-200">{job.error}</p> : null}</li>)}</ul></details>;
  const id = `execution-config-${runId}`;
  return <>
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-400/25 bg-indigo-400/5 px-4 py-3">
      <div><p className="text-sm text-indigo-100">{view.confirmed ? "仿真配置已确认" : "启动仿真前核对配置"}</p><p className="mt-1 text-xs text-slate-400">{view.experiments.length} 组实验 · {display(view.defaults.device)}{view.blockers.length ? ` · ${view.blockers.length} 项待解决` : " · 沿用已有配置"}</p></div>
      <button type="button" disabled={stale || Boolean(error)} onClick={() => { if (!dialog.current?.open) dialog.current?.showModal(); heading.current?.focus({ preventScroll: true }); }} className="rounded-lg border border-indigo-400/40 px-3 py-2 text-xs text-indigo-200 disabled:opacity-40">核对仿真配置</button>
    </div>
    {notice || error ? <p role="status" className="text-xs text-amber-200">{error || notice}</p> : null}
    <dialog ref={dialog} aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`} onCancel={event => { if (busy) event.preventDefault(); }} className="max-h-[90dvh] w-[min(900px,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-mars-border bg-mars-bg p-0 text-slate-200 shadow-2xl backdrop:bg-black/70">
      <div className="flex max-h-[90dvh] flex-col">
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-mars-border px-5 py-4"><div><h2 ref={heading} tabIndex={-1} id={`${id}-title`} className="text-lg font-semibold outline-none">启动仿真前核对配置</h2><p id={`${id}-description`} className="mt-1 text-xs text-slate-400">直接使用已批准实验与编码交付，不重新设计方案。下面的配置与实际启动一致；调整后须重新核对。</p></div><button type="button" disabled={busy} onClick={() => dialog.current?.close()} className="shrink-0 rounded-lg border border-mars-border px-3 py-2 text-xs disabled:opacity-40">稍后核对</button></header>
        <div className="min-h-0 space-y-5 overflow-y-auto p-5">
          {view.blockers.length ? <div role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4"><p className="text-sm font-medium text-amber-200">尚不能启动仿真</p><ul className="mt-2 list-disc space-y-2 pl-4 text-xs leading-6 text-amber-100/80">{view.blockers.map(item => <li key={item}>{item}</li>)}</ul></div> : null}
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">{Object.entries(view.defaults).map(([key, value]) => <div key={key} className="min-w-0"><dt className="text-xs text-slate-500">{labels[key] || key}</dt><dd className="mt-1 break-all text-sm text-slate-200">{display(value)}</dd></div>)}</dl>
          <section><h3 className="text-sm font-medium">实验清单</h3><div className="mt-2 divide-y divide-mars-border rounded-lg border border-mars-border">{view.experiments.map(item => <details key={item.name} className="p-3"><summary className="cursor-pointer text-xs text-slate-300">{item.name}<span className="ml-3 text-slate-500">随机种子：{display(item.seed)}</span></summary><pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-all text-xs leading-6 text-slate-400">{JSON.stringify(item.effective ? { requested: item.config, actual: item.effective } : item.config, null, 2)}</pre></details>)}</div></section>
          {view.source_configs.length ? <section><h3 className="text-sm font-medium">已编码的配置文件</h3><p className="mt-1 text-xs text-slate-500">每组实验绑定自己的配置文件；实际值在实验清单中展示。</p><ul className="mt-2 space-y-2 text-xs">{view.source_configs.map(item => <li key={item.path} className="break-all text-slate-300">{item.path}<span className="ml-3 text-slate-500">种子 {display(item.seed)} · 训练轮数 {display(item.epochs)}</span></li>)}</ul></section> : null}
          {view.jobs?.length ? <section><h3 className="text-sm font-medium">真实作业记录</h3><ul className="mt-2 space-y-2 text-xs">{view.jobs.map(job => <li key={`${job.experiment_id}-${job.attempt}-${job.updated_at}`} className="rounded-lg border border-mars-border p-3"><p>{job.experiment_id} · 第 {job.attempt} 次 · {({ running: "运行中", completed: "已完成", failed: "失败", interrupted: "已中断" } as Record<string, string>)[job.status] || job.status}</p>{job.error ? <p className="mt-1 text-amber-200">{job.error}</p> : null}</li>)}</ul></section> : null}
          <p className="text-xs text-slate-500">累计计入预算的模型请求 {view.budget.used} / {view.budget.limit ?? "不限"} 次。执行管理器无需模型生成计划；以上历史用量不阻断确定的作业执行。</p>
          {view.warnings.map(item => <p key={item} className="text-xs leading-6 text-amber-200">{item}</p>)}
        </div>
        <footer className="space-y-3 border-t border-mars-border px-5 py-4">
          {!view.confirmed ? <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={view.defaults.stop_after_execution === true} disabled={busy || stale || Boolean(error)} onChange={event => { void boundary(event.target.checked); }} />仿真完成后暂停，暂不生成报告</label> : null}
          {!view.confirmed ? <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={acknowledged === token} disabled={busy || !view.can_confirm || stale || Boolean(error)} onChange={event => setAcknowledged(event.target.checked ? token : "")} />我已核对运行环境、数据与实验参数</label> : null}
          {notice || error ? <p role="status" className="text-xs text-amber-200">{error || notice}</p> : null}
          <div className="flex flex-wrap items-center justify-between gap-3"><button type="button" onClick={() => { void refresh(); }} disabled={busy && !uncertain} className="text-xs text-indigo-300 disabled:opacity-40">刷新配置</button>{uncertain ? <button type="button" onClick={retryConfirmation} disabled={uncertain !== token || !view.can_confirm || stale || Boolean(error)} className="text-xs text-indigo-200 disabled:opacity-40">重新提交本次确认</button> : null}<button type="button" onClick={() => { void confirm(); }} disabled={busy || !view.can_confirm || view.confirmed || acknowledged !== token || stale || Boolean(error)} className="rounded-lg bg-mars-accent px-4 py-2 text-sm text-white disabled:opacity-40">{busy ? uncertain ? "正在核对提交结果" : "正在确认" : view.confirmed ? "已确认" : "确认配置，启动仿真"}</button></div>
        </footer>
      </div>
    </dialog>
  </>;
}
