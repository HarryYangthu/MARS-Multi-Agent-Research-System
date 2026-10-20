"use client";

import { useI18n } from "@/lib/i18n";

import { useCallback, useEffect, useRef, useState } from "react";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { confirmExecutionConfiguration, ExecutionReviewError, getExecutionPreparation, prepareExecutionConfiguration, type ExecutionConfiguration, type ExecutionPreparation } from "@/lib/executionReview";
import { ExecutionBoundaryControl } from "./ExecutionBoundaryControl";
import { executionExperimentFields } from "@/lib/executionReviewPresentation";
import type { RunDetail } from "@/lib/api";
import { managedReviewPending, managedReviewReason } from "@/lib/managedReview";
import { latestStages } from "@/lib/runReview";
import { formatMetric } from "@/lib/metricFormat";
import { PreparationRequestOrder, preparationCanConfirm, preparationCanRetryConfirmation, preparationSummary, type PreparationMutation } from "@/lib/executionPreparationState";

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
  const { ui } = useI18n();
  const [view, setView] = useState<ExecutionConfiguration | null>(null);
  const [preparation, setPreparation] = useState<ExecutionPreparation | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<PreparationMutation | null>(null);
  const [acknowledged, setAcknowledged] = useState("");
  const [uncertain, setUncertain] = useState<{ kind: PreparationMutation; token: string; operationId?: string } | null>(null);
  const [order] = useState(() => new PreparationRequestOrder());
  const currentPreparation = useRef<ExecutionPreparation | null>(null);
  const readController = useRef<AbortController | null>(null);
  const reading = useRef(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const presented = useRef(new Set<string>());
  const autoPrepared = useRef(new Set<string>());
  const changed = useRef(onChanged);
  const executionKeys = Object.keys(run?.states || {}).filter(key => key === "execution" || key.startsWith("execution_attempt_"));
  executionKeys.sort((left, right) => Number(left.split("_attempt_")[1] || 1) - Number(right.split("_attempt_")[1] || 1));
  const executionNode = executionKeys.at(-1) || "";
  const runExecutionState = run ? latestStages(run).find(item => item.stage === "execution")?.state : undefined;
  const confirmed = preparation?.confirmed ?? view?.confirmed ?? false;
  const awaitingDecision = Boolean(view?.state === "approved" && view.can_confirm && !confirmed);
  const executionState = awaitingDecision ? "approved" : runExecutionState ?? preparation?.launch_state ?? view?.state ?? "";
  const runStatus = run?.status || "";
  const executionDone = ["done", "completed"].includes(executionState);
  const executionFailed = ["failed", "error"].includes(executionState);
  const interrupted = !executionDone && !awaitingDecision && ["failed", "interrupted", "paused", "stopped", "cancelled", "cancelling"].includes(runStatus);
  const finished = ["completed", "done"].includes(runStatus);
  const launchBlocked = executionDone || executionFailed || interrupted || finished || run?.read_only === true;
  const busy = pending !== null;
  useEffect(() => { changed.current = onChanged; }, [onChanged]);
  const acceptPreparation = useCallback((next: ExecutionPreparation): void => {
    currentPreparation.current = next;
    setPreparation(next);
    if (next.configuration) setView(next.configuration);
    else setView(current => current && next.token && current.node === next.node && current.token === next.token ? current : null);
    setError("");
  }, []);
  const refresh = useCallback(async (): Promise<void> => {
    if (reading.current) return;
    reading.current = true;
    const controller = new AbortController();
    readController.current = controller;
    const ticket = order.beginRead();
    try {
      const next = await getExecutionPreparation(runId, project, controller.signal);
      if (!controller.signal.aborted && order.acceptsRead(ticket)) acceptPreparation(next);
    } catch (issue) {
      if (!controller.signal.aborted && order.acceptsRead(ticket)) setError(issue instanceof Error ? issue.message : "检查状态读取中断，可重试读取。");
    } finally {
      if (readController.current === controller) { readController.current = null; reading.current = false; }
    }
  }, [runId, project, order, acceptPreparation]);
  useEffect(() => {
    order.invalidate();
    readController.current?.abort(); reading.current = false;
    currentPreparation.current = null;
    setView(null); setPreparation(null); setPending(null); setUncertain(null); setAcknowledged(""); setError(""); setNotice("");
    void refresh();
    const interval = setInterval(() => { void refresh(); }, CLIENT_POLICY.controlRefreshMs);
    return () => { readController.current?.abort(); order.invalidate(); clearInterval(interval); };
  }, [runId, project, executionNode, order, refresh]);
  useEffect(() => { void refresh(); }, [runExecutionState, runStatus, refresh]);
  const token = preparation?.token || "";
  const visible = view?.visible;
  const launchReady = Boolean(view?.visible && view.launch_ready && !confirmed);
  const managedPending = managedReviewPending(run, "execution", "execution_configuration");
  const managedReason = managedReviewReason(run, "execution", "execution_configuration");
  const validation = preparation?.validation || view?.validation || [];
  const validationBlocked = preparation?.status === "failed" || preparation?.status === "stale" || preparation?.status === "interrupted";
  const validationPassed = preparation?.status === "ready";
  const executionNotice = executionFailed
    ? "执行已失败。请按任务详情中的原因修正后，在原任务中重试执行；已保存的配置和结果会保留。"
    : interrupted ? "任务已中止，当前未在推进仿真。运行前检查不会恢复任务或启动训练。"
    : finished ? "研究已结束，当前不会启动新的仿真。已保存的结果可在实验台查看。"
    : executionState === "running" ? "执行阶段正在处理；真实仿真作业的进度以实验台记录为准。" : "";
  const visibleNotice = notice || executionNotice;
  useEffect(() => { setAcknowledged(""); }, [runId, project, token]);
  useEffect(() => {
    if (managedPending || launchBlocked) { dialog.current?.close(); return; }
    if (!token || !launchReady || !validationPassed || stale || presented.current.has(token)) return;
    if (!dialog.current?.open) { dialog.current?.showModal(); heading.current?.focus({ preventScroll: true }); }
    presented.current.add(token);
  }, [token, launchReady, validationPassed, stale, managedPending, launchBlocked]);
  useEffect(() => {
    if (!uncertain || !preparation) return;
    if (uncertain.kind === "prepare" && preparation.operation_id && (preparation.operation_id !== uncertain.operationId || preparation.status === "preparing")) {
      setUncertain(null); setNotice(preparation.status === "preparing" ? "已核对到后台检查进度，可以离开弹窗，检查会继续。" : "已核对到运行前检查状态。");
    } else if (uncertain.kind === "confirm" && token && token !== uncertain.token) {
      setUncertain(null); setNotice("配置已变化，请核对新配置后重新确认；旧确认不会用于新配置。");
    } else if (uncertain.kind === "confirm" && preparation.confirmed) {
      setUncertain(null); setNotice(preparation.launch_state === "approved" ? "确认已保存，任务尚未推进，可重试推进同一任务。" : "已核对到配置确认记录，请查看真实作业状态。");
      dialog.current?.close();
      void changed.current().catch(() => undefined);
    } else if (uncertain.kind === "confirm" && preparation.confirmation_status === "failed") {
      setUncertain(null); setNotice(preparation.confirmation_error || "本次确认未完成，请核对检查状态后重试。");
    }
  }, [uncertain, preparation, token]);
  useEffect(() => { if (visible === false) dialog.current?.close(); }, [visible]);

  const prepare = useCallback(async (): Promise<void> => {
    if (run?.read_only || executionDone || finished || currentPreparation.current?.status === "preparing" || currentPreparation.current?.launch_state !== "approved") return;
    const ticket = order.beginMutation();
    if (ticket === null) return;
    setPending("prepare"); setUncertain(null); setNotice("");
    const previous = currentPreparation.current;
    const requestedToken = previous?.status === "stale" ? undefined : previous?.token || undefined;
    try {
      const next = await prepareExecutionConfiguration(runId, project, requestedToken);
      if (order.acceptsMutation(ticket)) { acceptPreparation(next); setNotice("后台运行前检查已提交；检查通过后仍需核对并确认启动。"); }
    } catch (issue) {
      if (!order.acceptsMutation(ticket)) return;
      if (issue instanceof ExecutionReviewError && issue.status >= 400 && issue.status < 500 && issue.status !== 408) setNotice(issue.message);
      else { setUncertain({ kind: "prepare", token: requestedToken || "", operationId: previous?.operation_id }); setNotice("连接中断，正在核对后台检查是否已开始。"); }
    } finally {
      if (order.finishMutation(ticket)) { setPending(null); void refresh(); }
    }
  }, [run?.read_only, executionDone, finished, order, runId, project, acceptPreparation, refresh]);
  useEffect(() => {
    if (preparation?.status !== "unprepared" || preparation.launch_state !== "approved" || preparation.confirmed || run?.read_only) return;
    const key = `${project}:${runId}:${preparation.node}`;
    if (autoPrepared.current.has(key)) return;
    autoPrepared.current.add(key);
    void prepare();
  }, [preparation, project, runId, run?.read_only, prepare]);
  async function confirm(retryToken = ""): Promise<void> {
    const current = currentPreparation.current;
    const eligible = retryToken
      ? preparationCanRetryConfirmation(current, retryToken) && run?.read_only !== true
      : preparationCanConfirm(current, acknowledged, stale, launchBlocked);
    if (!eligible || !current) return;
    const ticket = order.beginMutation();
    if (ticket === null) return;
    const submittedToken = current.token;
    setPending("confirm"); setUncertain(null); setNotice("");
    try {
      const result = await confirmExecutionConfiguration(runId, project, submittedToken);
      if (!order.acceptsMutation(ticket)) return;
      const decisionStillCurrent = currentPreparation.current?.token === submittedToken;
      if (result.confirmed) {
        if (decisionStillCurrent) {
          dialog.current?.close();
          setNotice(result.ok ? "配置确认已保存，请以当前执行状态和真实作业记录为准。" : "确认已保存，任务尚未推进，可重试推进同一任务。");
          acceptPreparation({ ...current, confirmed: true, confirmation_status: "confirmed" });
        } else setNotice("先前配置的确认记录已保存；当前配置已变化，仍需重新核对。");
        void changed.current().catch(() => setNotice("配置已确认，任务状态暂未更新，可重新读取状态。"));
      } else if (result.status === "confirming") {
        setNotice("正在后台核对本次确认，可以关闭弹窗。确认记录保存后才会推进仿真。");
        if (decisionStillCurrent) acceptPreparation({ ...current, confirmation_status: "confirming" });
      } else setNotice("尚未取得确认记录，请核对当前检查状态后重试。");
    } catch (issue) {
      if (!order.acceptsMutation(ticket)) return;
      if (issue instanceof ExecutionReviewError && issue.status >= 400 && issue.status < 500 && issue.status !== 408) { setAcknowledged(""); setNotice(issue.message); }
      else { setUncertain({ kind: "confirm", token: submittedToken }); setNotice("连接中断，正在核对本次确认是否已保存。可关闭弹窗查看状态。"); }
    } finally {
      if (order.finishMutation(ticket)) { setPending(null); void refresh(); }
    }
  }
  const retryToken = uncertain?.kind === "confirm" ? uncertain.token : confirmed ? token : "";
  const canRetryConfirmation = preparationCanRetryConfirmation(preparation, retryToken) && run?.read_only !== true;
  const canPrepare = !busy && preparation?.status !== "preparing" && preparation?.launch_state === "approved" && !executionDone && !finished && run?.read_only !== true;
  const progress = <section aria-label="运行前检查进度" className="space-y-2 text-xs">
    <p role="status" className="text-slate-300">{preparationSummary(preparation)}</p>
    {preparation?.error ? <p className="whitespace-pre-wrap text-amber-200">{preparation.error}</p> : null}
    {preparation?.confirmation_status === "confirming" ? <p role="status" className="text-indigo-200">正在后台核对确认记录，可以离开弹窗。</p> : null}
    {preparation?.confirmation_error ? <p role="status" className="whitespace-pre-wrap text-amber-200">{preparation.confirmation_error}</p> : null}
    {validation.length ? <ul className="space-y-2">{validation.map(item => <li key={item.name} className="rounded-lg border border-mars-border p-3"><p>{item.name} · {({ passed: "实际入口检查已通过", running: "正在检查实际入口", failed: "检查未通过", stale: "输入已变化，需重新检查", required: "等待检查实际入口", unverified: "尚未配置入口检查" } as Record<string, string>)[item.status]}</p>{item.error ? <p className="mt-1 whitespace-pre-wrap text-amber-200">{item.error}</p> : null}</li>)}</ul> : null}
    <p className="text-slate-500">只核对环境、配置和数据，不启动训练。可以关闭弹窗，后台检查会继续。</p>
    {error ? <p role="status" className="text-amber-200">状态暂未更新：{error}</p> : null}
    <div className="flex flex-wrap gap-4"><button type="button" onClick={() => { void refresh(); }} className="text-indigo-300">重新读取状态</button>{preparation?.status !== "ready" && !confirmed ? <button type="button" disabled={!canPrepare} onClick={() => { void prepare(); }} className="text-indigo-200 disabled:opacity-40">{pending === "prepare" ? "正在提交检查" : preparation?.status === "preparing" ? "后台检查进行中" : validationBlocked ? "重新检查运行入口" : "检查运行入口"}</button> : null}{canRetryConfirmation ? <button type="button" disabled={busy} onClick={() => { void confirm(retryToken); }} className="text-indigo-200 disabled:opacity-40">{confirmed ? "重试推进同一任务" : "重试本次确认"}</button> : null}</div>
  </section>;
  const boundaryControl = <ExecutionBoundaryControl runId={runId} project={project} />;
  if (!view?.visible && !preparation?.operation_id && preparation?.launch_state !== "approved" && runExecutionState !== "approved" && !error) return boundaryControl;
  if (!view?.visible) return <>{boundaryControl}<div className="rounded-xl border border-mars-border bg-mars-panel px-4 py-3">{progress}</div>{visibleNotice ? <p role="status" className="text-xs text-amber-300">{visibleNotice}</p> : null}</>;
  if (view.state === "done") return <>{boundaryControl}<details className="rounded-xl border border-mars-border bg-mars-panel px-4 py-3"><summary className="cursor-pointer text-sm text-slate-200">{ui("仿真已完成")}<span className="ml-3 text-xs text-slate-400">{view.jobs.length}  {ui("条真实作业记录 · 展开结果")}</span></summary><ul className="mt-3 space-y-3">{view.jobs.map(job => <li key={`${job.experiment_id}-${job.updated_at}`} className="rounded-lg border border-mars-border p-3 text-xs"><p className="text-slate-200">{job.experiment_id} · {job.status === "completed" ? ui("已完成") : job.status === "interrupted" ? ui("已中断") : ui("失败")}{job.duration_seconds !== undefined && job.duration_seconds !== null ? ` · ${job.duration_seconds.toFixed(2)} 秒` : ""}</p>{Object.keys(job.metrics).length ? <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-2">{Object.entries(job.metrics).map(([name, value]) => <div key={name}><dt className="text-slate-500">{name}</dt><dd className="mt-1 text-slate-300" title={String(value)}>{formatMetric(value)}</dd></div>)}</dl> : null}{job.error ? <p className="mt-2 text-amber-200">{job.error}</p> : null}</li>)}</ul></details></>;
  const id = `execution-config-${runId}`;
  return <>
    {boundaryControl}
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-400/25 bg-indigo-400/5 px-4 py-3">
      <div><p className="text-sm text-indigo-100">{executionFailed ? "仿真执行失败" : interrupted ? "仿真推进已中止" : finished ? "研究已结束" : validationBlocked ? "运行前验证需重新核对" : confirmed ? "仿真配置已确认" : managedPending ? "主控正在核对仿真配置" : "启动仿真前核对配置"}</p><p className="mt-1 text-xs text-slate-400">{view.experiments.length} 组实验 · {display(view.defaults.device)} · {confirmed ? "配置确认已保存" : "配置待确认"}{view.blockers.length ? ` · ${view.blockers.length} 项待解决` : validationPassed ? " · 实际入口验证已通过" : ""}</p>{run?.review_mode === "commander" && !confirmed && !launchBlocked && managedReason ? <p role="status" className="mt-1 text-xs text-amber-200">{managedReason}</p> : null}</div>
      <button type="button" disabled={stale} onClick={() => { if (!dialog.current?.open) dialog.current?.showModal(); heading.current?.focus({ preventScroll: true }); }} className="rounded-lg border border-indigo-400/40 px-3 py-2 text-xs text-indigo-200 disabled:opacity-40">核对仿真配置</button>
    </div>
    <details className="rounded-xl border border-mars-border px-4 py-3" open={preparation?.status === "preparing" || validationBlocked || preparation?.confirmation_status === "confirming"}><summary className="cursor-pointer text-xs text-slate-300">{preparationSummary(preparation)}</summary><div className="mt-3">{progress}</div></details>
    {visibleNotice ? <p role="status" className="text-xs text-amber-200">{visibleNotice}</p> : null}
    <dialog ref={dialog} aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`} className="max-h-[90dvh] w-[min(900px,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-mars-border bg-mars-bg p-0 text-slate-200 shadow-2xl backdrop:bg-black/70">
      <div className="flex max-h-[90dvh] flex-col">
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-mars-border px-5 py-4"><div><h2 ref={heading} tabIndex={-1} id={`${id}-title`} className="text-lg font-semibold outline-none">启动仿真前核对配置</h2><p id={`${id}-description`} className="mt-1 text-xs text-slate-400">直接使用已批准实验与编码交付，不重新设计方案。下面的配置与实际启动一致；调整后须重新核对。</p></div><button type="button" onClick={() => dialog.current?.close()} className="shrink-0 rounded-lg border border-mars-border px-3 py-2 text-xs disabled:opacity-40">稍后核对</button></header>
        <div className="min-h-0 space-y-5 overflow-y-auto p-5">
          {view.blockers.length ? <div role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4"><p className="text-sm font-medium text-amber-200">尚不能启动仿真</p><ul className="mt-2 list-disc space-y-2 pl-4 text-xs leading-6 text-amber-100/80">{view.blockers.map(item => <li key={item}>{item}</li>)}</ul></div> : null}
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">{Object.entries(view.defaults).filter(([key]) => key !== "stop_after_execution").map(([key, value]) => <div key={key} className="min-w-0"><dt className="text-xs text-slate-500">{labels[key] || key}</dt><dd className="mt-1 break-all text-sm text-slate-200">{display(value)}</dd></div>)}</dl>
          <section><h3 className="text-sm font-medium">实验清单</h3><div className="mt-2 divide-y divide-mars-border rounded-lg border border-mars-border">{view.experiments.map(item => <article key={item.name} className="p-3">
            <h4 className="text-xs font-medium text-slate-200">{item.name}</h4>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2">{executionExperimentFields(item).map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 break-all text-xs text-slate-300">{display(value)}</dd></div>)}</dl>
            {item.effective?.output_parent ? <p className="mt-2 text-xs text-slate-500">每次作业在此位置创建独立目录，保留配置快照、曲线和结果。</p> : null}
            <details className="mt-3"><summary className="cursor-pointer text-xs text-indigo-300">查看配置明细</summary><pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-all text-xs leading-6 text-slate-400">{JSON.stringify(item.effective ? { requested: item.config, actual: item.effective } : item.config, null, 2)}</pre></details>
          </article>)}</div></section>
          {view.source_configs.length ? <section><h3 className="text-sm font-medium">已编码的配置文件</h3><p className="mt-1 text-xs text-slate-500">每组实验绑定自己的配置文件；实际值在实验清单中展示。</p><ul className="mt-2 space-y-2 text-xs">{view.source_configs.map(item => <li key={item.path} className="break-all text-slate-300">{item.path}<span className="ml-3 text-slate-500">种子 {display(item.seed)}{view.defaults.budget_unit !== "steps" ? ` · 训练轮数 ${display(item.epochs)}` : ""}</span></li>)}</ul></section> : null}
          {progress}
          {view.jobs?.length ? <section><h3 className="text-sm font-medium">真实作业记录</h3><ul className="mt-2 space-y-2 text-xs">{view.jobs.map(job => <li key={`${job.experiment_id}-${job.attempt}-${job.updated_at}`} className="rounded-lg border border-mars-border p-3"><p>{job.experiment_id} · 第 {job.attempt} 次 · {({ running: "运行中", completed: ui("已完成"), failed: ui("失败"), interrupted: ui("已中断") } as Record<string, string>)[job.status] || job.status}</p>{job.error ? <p className="mt-1 text-amber-200">{job.error}</p> : null}</li>)}</ul></section> : null}
          <p className="text-xs text-slate-500">累计计入预算的模型请求 {view.budget.used} / {view.budget.limit ?? "不限"} 次。执行管理器无需模型生成计划；以上历史用量不阻断确定的作业执行。</p>
          {view.warnings.map(item => <p key={item} className="text-xs leading-6 text-amber-200">{item}</p>)}
        </div>
        <footer className="space-y-3 border-t border-mars-border px-5 py-4">
          <button type="button" onClick={() => { dialog.current?.close(); document.getElementById(`execution-boundary-control-${runId}`)?.focus(); }} className="text-xs text-indigo-300 disabled:opacity-40">设置仿真完成后的暂停与继续</button>
          {!confirmed ? <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={Boolean(token) && acknowledged === token} disabled={busy || preparation?.confirmation_status === "confirming" || !validationPassed || !view.can_confirm || stale || launchBlocked} onChange={event => setAcknowledged(event.target.checked ? token : "")} />我已核对运行环境、数据与实验参数</label> : null}
          {visibleNotice ? <p role="status" className="text-xs text-amber-200">{visibleNotice}</p> : null}
          <div className="flex flex-wrap items-center justify-between gap-3"><button type="button" onClick={() => { void refresh(); }} className="text-xs text-indigo-300">读取检查状态</button><button type="button" onClick={() => { void confirm(); }} disabled={busy || !preparationCanConfirm(preparation, acknowledged, stale, launchBlocked)} className="rounded-lg bg-mars-accent px-4 py-2 text-sm text-white disabled:opacity-40">{pending === "confirm" ? "正在保存确认" : preparation?.confirmation_status === "confirming" ? "后台确认中，可关闭弹窗" : launchBlocked ? "当前不可启动" : confirmed ? "已确认" : validationPassed ? "确认配置，启动仿真" : "请先完成运行前检查"}</button></div>
        </footer>
      </div>
    </dialog>
  </>;
}
