"use client";

import { useEffect, useRef, useState } from "react";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { confirmExecutionConfiguration, setExecutionBoundary, ExecutionReviewError, getExecutionConfiguration, type ExecutionConfiguration } from "@/lib/executionReview";
import { executionExperimentFields } from "@/lib/executionReviewPresentation";
import type { RunDetail } from "@/lib/api";
import { managedReviewPending, managedReviewReason } from "@/lib/managedReview";
import { latestStages } from "@/lib/runReview";
import { ProjectDataPipeline } from "./ProjectDataPipeline";

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
const backends: Record<string, string> = { cpu: "CPU", paper_static: "论文训练适配器", local_command: "项目启动命令", remote_gpu: "远端 GPU", pim_cpu: "本地 PIM 仿真", environment: "本地启动配置", execution_config: "执行配置文件", default: "应用默认配置", steps: "参数更新次数", epochs: "完整训练轮次" };
function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未配置";
  if (typeof value === "boolean") return value ? "是" : "否";
  return typeof value === "string" ? backends[value] || value : JSON.stringify(value);
}

export function ExecutionConfigurationReview({ runId, project, stale, onChanged, run = null }: {
  runId: string; project: string; stale: boolean; onChanged: () => Promise<void>; run?: RunDetail | null;
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
  const readGeneration = useRef(0);
  const changed = useRef(onChanged);
  const executionKeys = Object.keys(run?.states || {}).filter(key => key === "execution" || key.startsWith("execution_attempt_"));
  executionKeys.sort((left, right) => Number(left.split("_attempt_")[1] || 1) - Number(right.split("_attempt_")[1] || 1));
  const executionNode = executionKeys.at(-1) || "";
  const runExecutionState = run ? latestStages(run).find(item => item.stage === "execution")?.state : undefined;
  const awaitingDecision = Boolean(view?.state === "approved" && view.can_confirm && !view.confirmed);
  // A fresh confirmation GET can already expose the new decision while the
  // separate run snapshot still carries the preceding failed attempt.
  const executionState = awaitingDecision ? "approved" : runExecutionState ?? view?.state ?? "";
  const runStatus = run?.status || "";
  const executionDone = ["done", "completed"].includes(executionState);
  const executionFailed = ["failed", "error"].includes(executionState);
  const interrupted = !executionDone && !awaitingDecision && ["failed", "interrupted", "paused", "stopped", "cancelled", "cancelling"].includes(runStatus);
  const finished = ["completed", "done"].includes(runStatus);
  const launchBlocked = executionDone || executionFailed || interrupted || finished || run?.read_only === true;
  useEffect(() => { changed.current = onChanged; }, [onChanged]);
  useEffect(() => {
    const controller = new AbortController();
    let reading = false;
    // A retry can retain the run ID while renewing its execution decision.
    // Discard the old confirmation view and any older in-flight read.
    setView(null);
    const generationState = readGeneration;
    const refresh = async (): Promise<void> => {
      if (reading) return;
      reading = true;
      const generation = ++generationState.current;
      try {
        const next = await getExecutionConfiguration(runId, project, controller.signal);
        if (!controller.signal.aborted && generation === generationState.current) { setView(next); setError(""); }
      } catch (issue) {
        if (!controller.signal.aborted && generation === generationState.current) setError(issue instanceof Error ? issue.message : "配置状态更新中断。");
      } finally { reading = false; }
    };
    void refresh();
    const interval = setInterval(() => { void refresh(); }, CLIENT_POLICY.controlRefreshMs);
    return () => { controller.abort(); clearInterval(interval); generationState.current++; };
  }, [runId, project, executionNode, runExecutionState, runStatus]);
  const token = view?.token || "";
  const currentDecision = useRef(token);
  const visible = view?.visible;
  const launchReady = Boolean(view?.visible && view.launch_ready && !view.confirmed);
  const managedPending = managedReviewPending(run, "execution", "execution_configuration");
  const managedReason = managedReviewReason(run, "execution", "execution_configuration");
  const validationBlocked = Boolean(view?.validation?.some(item => item.status === "failed" || item.status === "stale"));
  const validationPassed = Boolean(view?.validation?.length && view.validation.every(item => item.status === "passed"));
  const executionNotice = executionFailed
    ? "执行已失败。请按任务详情中的原因修正后，在原任务中重试执行；已保存的配置和结果会保留。"
    : interrupted ? "任务已中止，当前未在推进仿真。请核对停止或失败原因后，在原任务中恢复或重试。"
    : finished ? "研究已结束，当前不会启动新的仿真。已保存的结果可在实验台查看。"
    : validationBlocked ? "运行前验证未通过或输入已变化。请按原因修正后，在原任务中重新核对配置。"
    : executionState === "running" ? "执行阶段正在处理；真实仿真作业的进度以实验台记录为准。" : "";
  const visibleNotice = error || executionNotice || notice;
  useEffect(() => { currentDecision.current = token; setAcknowledged(""); setNotice(""); }, [runId, project, token]);
  useEffect(() => {
    if (managedPending || launchBlocked) { dialog.current?.close(); return; }
    if (!token || !launchReady || stale || error || presented.current.has(token)) return;
    if (!dialog.current?.open) { dialog.current?.showModal(); heading.current?.focus({ preventScroll: true }); }
    presented.current.add(token);
  }, [token, launchReady, stale, error, managedPending, launchBlocked]);
  useEffect(() => {
    if (uncertain && view?.token && (view.token !== uncertain || view.confirmed)) {
      setUncertain(""); setBusy(false); submitting.current = false; dialog.current?.close();
      setNotice(view.token === uncertain ? "已核对到配置确认记录，请查看执行状态。" : "配置已发生变化，需重新核对；请同时查看任务执行状态。");
      void changed.current().catch(() => setNotice("配置已确认，任务状态暂未更新，请刷新核对。"));
    }
  }, [uncertain, view?.token, view?.confirmed]);
  useEffect(() => { if (visible === false && !busy) dialog.current?.close(); }, [visible, busy]);

  async function refresh(): Promise<void> {
    const generation = ++readGeneration.current;
    try {
      const next = await getExecutionConfiguration(runId, project);
      if (generation === readGeneration.current) { setView(next); setError(""); }
    } catch (issue) {
      if (generation === readGeneration.current) setError(issue instanceof Error ? issue.message : "配置核对失败。");
    }
  }
  async function boundary(stop: boolean): Promise<void> {
    if (launchBlocked) return;
    const generation = ++readGeneration.current;
    setBusy(true); setAcknowledged("");
    try {
      const next = await setExecutionBoundary(runId, project, stop);
      if (generation === readGeneration.current) { setView(next); setError(""); }
    } catch (issue) {
      if (generation === readGeneration.current) setError(issue instanceof Error ? issue.message : "阶段边界保存失败。");
    }
    finally { setBusy(false); }
  }
  async function confirm(ackToken = acknowledged): Promise<void> {
    if (!token || !view?.can_confirm || view.confirmed || ackToken !== token || currentDecision.current !== token || submitting.current || stale || error || launchBlocked) return;
    submitting.current = true; setBusy(true); setNotice("");
    try {
      const result = await confirmExecutionConfiguration(runId, project, token);
      if (result.confirmed) {
        dialog.current?.close();
        if (currentDecision.current === token) setNotice(result.ok ? "配置确认已保存，请以当前执行状态和真实作业记录为准。" : "配置确认已保存，任务尚未启动，请核对任务恢复状态。");
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
    if (!token || uncertain !== token || currentDecision.current !== token || !view?.can_confirm || view.confirmed || stale || error || launchBlocked) return;
    // Retry the same immutable confirmation identity. The server reuses its
    // receipt and owned driver, including after a lost HTTP response.
    submitting.current = false; setUncertain("");
    // The uncertain token records this same decision's prior acknowledgement;
    // a polling refresh may have cleared the checkbox without changing it.
    void confirm(token);
  }
  if (!view?.visible) return visibleNotice ? <p role="status" className="text-xs text-amber-300">{visibleNotice}</p> : null;
  if (view.state === "done") return <details className="rounded-xl border border-mars-border bg-mars-panel px-4 py-3"><summary className="cursor-pointer text-sm text-slate-200">仿真已完成<span className="ml-3 text-xs text-slate-400">{view.jobs.length} 条真实作业记录 · 展开结果</span></summary><ul className="mt-3 space-y-3">{view.jobs.map(job => <li key={`${job.experiment_id}-${job.updated_at}`} className="rounded-lg border border-mars-border p-3 text-xs"><p className="text-slate-200">{job.experiment_id} · {job.status === "completed" ? "已完成" : job.status === "interrupted" ? "已中断" : "失败"}{job.duration_seconds !== undefined && job.duration_seconds !== null ? ` · ${job.duration_seconds.toFixed(2)} 秒` : ""}</p>{Object.keys(job.metrics).length ? <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-2">{Object.entries(job.metrics).map(([name, value]) => <div key={name}><dt className="text-slate-500">{name}</dt><dd className="mt-1 text-slate-300" title={String(value)}>{Number.isInteger(value) ? value : value.toFixed(2)}</dd></div>)}</dl> : null}{job.error ? <p className="mt-2 text-amber-200">{job.error}</p> : null}</li>)}</ul></details>;
  const id = `execution-config-${runId}`;
  return <>
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-400/25 bg-indigo-400/5 px-4 py-3">
      <div><p className="text-sm text-indigo-100">{executionFailed ? "仿真执行失败" : interrupted ? "仿真推进已中止" : finished ? "研究已结束" : validationBlocked ? "运行前验证需重新核对" : view.confirmed ? "仿真配置已确认" : managedPending ? "主控正在核对仿真配置" : "启动仿真前核对配置"}</p><p className="mt-1 text-xs text-slate-400">{view.experiments.length} 组实验 · {display(view.defaults.device)} · {view.confirmed ? "配置确认已保存" : "配置待确认"}{view.blockers.length ? ` · ${view.blockers.length} 项待解决` : validationPassed ? " · 实际入口验证已通过" : ""}</p>{run?.review_mode === "commander" && !view.confirmed && !launchBlocked && managedReason ? <p role="status" className="mt-1 text-xs text-amber-200">{managedReason}</p> : null}</div>
      <button type="button" disabled={stale || Boolean(error)} onClick={() => { if (!dialog.current?.open) dialog.current?.showModal(); heading.current?.focus({ preventScroll: true }); }} className="rounded-lg border border-indigo-400/40 px-3 py-2 text-xs text-indigo-200 disabled:opacity-40">核对仿真配置</button>
    </div>
    {visibleNotice ? <p role="status" className="text-xs text-amber-200">{visibleNotice}</p> : null}
    <dialog ref={dialog} aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`} onCancel={event => { if (busy) event.preventDefault(); }} className="max-h-[90dvh] w-[min(900px,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-mars-border bg-mars-bg p-0 text-slate-200 shadow-2xl backdrop:bg-black/70">
      <div className="flex max-h-[90dvh] flex-col">
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-mars-border px-5 py-4"><div><h2 ref={heading} tabIndex={-1} id={`${id}-title`} className="text-lg font-semibold outline-none">启动仿真前核对配置</h2><p id={`${id}-description`} className="mt-1 text-xs text-slate-400">直接使用已批准实验与编码交付，不重新设计方案。下面的配置与实际启动一致；调整后须重新核对。</p></div><button type="button" disabled={busy} onClick={() => dialog.current?.close()} className="shrink-0 rounded-lg border border-mars-border px-3 py-2 text-xs disabled:opacity-40">稍后核对</button></header>
        <div className="min-h-0 space-y-5 overflow-y-auto p-5">
          {view.blockers.length ? <div role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4"><p className="text-sm font-medium text-amber-200">尚不能启动仿真</p><ul className="mt-2 list-disc space-y-2 pl-4 text-xs leading-6 text-amber-100/80">{view.blockers.map(item => <li key={item}>{item}</li>)}</ul></div> : null}
          {view.missing_data_binding ? <section className="space-y-3 rounded-lg border border-amber-500/30 p-4">
            <h3 className="text-sm font-medium">先核对数据与方案</h3>
            <p className="text-sm leading-6 text-slate-300">这次任务创建时没有绑定数据。请先确认要用的文件，并核对实验方案、代码配置是否适用。已有方案需要修订并重新审核；仅设置默认数据或刷新页面不会解除当前阻塞。</p>
            <details><summary className="cursor-pointer py-2 text-sm text-indigo-200">查看本项目的数据选择</summary><div className="mt-3"><ProjectDataPipeline project={project} /></div></details>
            <p className="text-sm leading-6 text-slate-400">选定后回到研究对话，说明“使用哪份数据，修订当前实验方案及编码配置后继续”；主控托管仍会按正常流程审核。</p>
          </section> : null}
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">{Object.entries(view.defaults).map(([key, value]) => <div key={key} className="min-w-0"><dt className="text-xs text-slate-500">{labels[key] || key}</dt><dd className="mt-1 break-all text-sm text-slate-200">{display(value)}</dd></div>)}</dl>
          <section><h3 className="text-sm font-medium">实验清单</h3><div className="mt-2 divide-y divide-mars-border rounded-lg border border-mars-border">{view.experiments.map(item => <article key={item.name} className="p-3">
            <h4 className="text-xs font-medium text-slate-200">{item.name}</h4>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2">{executionExperimentFields(item).map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 break-all text-xs text-slate-300">{display(value)}</dd></div>)}</dl>
            {item.effective?.output_parent ? <p className="mt-2 text-xs text-slate-500">每次作业在此位置创建独立目录，保留配置快照、曲线和结果。</p> : null}
            <details className="mt-3"><summary className="cursor-pointer text-xs text-indigo-300">查看配置明细</summary><pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-all text-xs leading-6 text-slate-400">{JSON.stringify(item.effective ? { requested: item.config, actual: item.effective } : item.config, null, 2)}</pre></details>
          </article>)}</div></section>
          {view.source_configs.length ? <section><h3 className="text-sm font-medium">已编码的配置文件</h3><p className="mt-1 text-xs text-slate-500">每组实验绑定自己的配置文件；实际值在实验清单中展示。</p><ul className="mt-2 space-y-2 text-xs">{view.source_configs.map(item => <li key={item.path} className="break-all text-slate-300">{item.path}<span className="ml-3 text-slate-500">种子 {display(item.seed)}{view.defaults.budget_unit !== "steps" ? ` · 训练轮数 ${display(item.epochs)}` : ""}</span></li>)}</ul></section> : null}
          {view.validation?.length ? <section><h3 className="text-sm font-medium">运行前验证</h3><ul className="mt-2 space-y-2 text-xs">{view.validation.map(item => <li key={item.name} className="rounded-lg border border-mars-border p-3"><p>{item.name} · {({ passed: "实际入口检查已通过", failed: "检查未通过", stale: "输入已变化，需重新检查", required: "确认时检查实际入口", unverified: "尚未配置入口检查" } as Record<string, string>)[item.status]}</p>{item.error ? <p className="mt-1 whitespace-pre-wrap text-amber-200">{item.error}</p> : null}</li>)}</ul><p className="mt-2 text-xs text-slate-500">检查执行环境、配置和数据，训练结果在仿真完成后单独显示。</p></section> : null}
          {view.jobs?.length ? <section><h3 className="text-sm font-medium">真实作业记录</h3><ul className="mt-2 space-y-2 text-xs">{view.jobs.map(job => <li key={`${job.experiment_id}-${job.attempt}-${job.updated_at}`} className="rounded-lg border border-mars-border p-3"><p>{job.experiment_id} · 第 {job.attempt} 次 · {({ running: "运行中", completed: "已完成", failed: "失败", interrupted: "已中断" } as Record<string, string>)[job.status] || job.status}</p>{job.error ? <p className="mt-1 text-amber-200">{job.error}</p> : null}</li>)}</ul></section> : null}
          <p className="text-xs text-slate-500">累计计入预算的模型请求 {view.budget.used} / {view.budget.limit ?? "不限"} 次。执行管理器无需模型生成计划；以上历史用量不阻断确定的作业执行。</p>
          {view.warnings.map(item => <p key={item} className="text-xs leading-6 text-amber-200">{item}</p>)}
        </div>
        <footer className="space-y-3 border-t border-mars-border px-5 py-4">
          {!view.confirmed ? <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={view.defaults.stop_after_execution === true} disabled={busy || stale || Boolean(error) || launchBlocked} onChange={event => { void boundary(event.target.checked); }} />仿真完成后暂停，暂不生成报告</label> : null}
          {!view.confirmed ? <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={acknowledged === token} disabled={busy || !view.can_confirm || stale || Boolean(error) || launchBlocked} onChange={event => setAcknowledged(event.target.checked ? token : "")} />我已核对运行环境、数据与实验参数</label> : null}
          {visibleNotice ? <p role="status" className="text-xs text-amber-200">{visibleNotice}</p> : null}
          <div className="flex flex-wrap items-center justify-between gap-3"><button type="button" onClick={() => { void refresh(); }} disabled={busy && !uncertain} className="text-xs text-indigo-300 disabled:opacity-40">刷新配置</button>{uncertain ? <button type="button" onClick={retryConfirmation} disabled={uncertain !== token || !view.can_confirm || stale || Boolean(error) || launchBlocked} className="text-xs text-indigo-200 disabled:opacity-40">重新提交本次确认</button> : null}<button type="button" onClick={() => { void confirm(); }} disabled={busy || !view.can_confirm || view.confirmed || acknowledged !== token || stale || Boolean(error) || launchBlocked} className="rounded-lg bg-mars-accent px-4 py-2 text-sm text-white disabled:opacity-40">{busy ? uncertain ? "正在核对提交结果" : "正在确认" : launchBlocked ? "当前不可启动" : view.confirmed ? "已确认" : "确认配置，启动仿真"}</button></div>
        </footer>
      </div>
    </dialog>
  </>;
}
