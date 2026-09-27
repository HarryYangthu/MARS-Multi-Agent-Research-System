"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { downloadResultExport, getResults, type RunResults } from "@/lib/results";
import { isUncertainRequestError } from "@/lib/clientPolicy";

const OUTCOME: Record<RunResults["outcome"]["status"], string> = {
  unknown: "目标达成状态尚不能判定", goal_met: "完成且达标", goal_not_met: "完成但未达标",
  budget_stopped: "预算停止", user_cancelled: "用户取消", blocked: "研究受阻", failed: "研究失败",
};
const ROLE = { baseline: "基线", candidate: "候选方案", ablation: "消融", unknown: "未标注" };
const VERIFICATION = { verified_local_receipt: "本地执行回执已核验", unverified: "未核验", invalid: "核验失败" };
const button = "rounded-md border border-mars-border bg-mars-panel2 px-4 py-2 text-sm hover:bg-mars-subtle disabled:opacity-50";

function displayNumber(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "未知" : new Intl.NumberFormat("zh-CN", { maximumSignificantDigits: 7 }).format(value);
}

export function RunResultsPanel({ runId }: { runId: string }): JSX.Element {
  const [data, setData] = useState<RunResults | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [exportError, setExportError] = useState(false);
  const exportRequest = useRef<AbortController | null>(null);
  useEffect(() => () => exportRequest.current?.abort(), [runId]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setData(null); setExportMessage("");
    void getResults(runId, controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(isUncertainRequestError(cause) ? "连接中断或等待超时，结果尚未读取。请恢复连接后点击刷新结果。" : cause instanceof Error ? cause.message : "读取结果失败。");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [runId, refresh]);

  async function exportResults(): Promise<void> {
    const controller = new AbortController();
    exportRequest.current = controller;
    setExporting(true); setExportMessage(""); setExportError(false);
    try {
      await downloadResultExport(runId, controller.signal);
      if (!controller.signal.aborted) setExportMessage("下载已交给浏览器。解压后打开 report.html 阅读；复现前提见包内说明。");
    } catch (cause: unknown) {
      if (controller.signal.aborted) return;
      setExportError(true);
      setExportMessage(isUncertainRequestError(cause) ? "连接中断或等待超时，尚不能确认导出是否完成。请检查连接后再操作。" : cause instanceof Error ? cause.message : "导出失败，请检查连接后重试。");
    } finally { if (!controller.signal.aborted) setExporting(false); }
  }

  return <>
    <div className="flex flex-wrap items-center justify-between gap-3"><Link href="/results" className="text-sm text-slate-400 hover:text-white">← 全部结果</Link><div className="flex gap-2"><Link href={`/runs/${encodeURIComponent(runId)}`} className={button}>任务详情</Link><button type="button" disabled={loading} onClick={() => setRefresh((value) => value + 1)} className={button}>刷新结果</button></div></div>
    {loading ? <p role="status" className="py-12 text-center text-slate-400">正在核对任务状态与结果产物…</p> : null}
    {error ? <div role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">{error}</div> : null}
    {data ? <>
      <header className="space-y-3"><p className="break-all font-mono text-xs text-slate-500">{data.identity.run_id}</p><h1 className="break-words text-2xl font-semibold">{data.identity.task}</h1>{data.identity.question ? <p className="max-w-4xl whitespace-pre-wrap text-sm leading-7 text-slate-300">{data.identity.question}</p> : null}</header>
      <section aria-labelledby="outcome-title" className="rounded-lg border border-mars-border bg-mars-panel p-5">
        <div className="flex flex-wrap items-start justify-between gap-4"><div><h2 id="outcome-title" className="text-lg font-medium">{OUTCOME[data.outcome.status]}</h2><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-400">{data.outcome.reason}</p></div><button type="button" onClick={() => void exportResults()} disabled={exporting || data.state.authority === "invalid"} className={`${button} shrink-0`}>{exporting ? "正在生成报告包…" : "导出离线报告包"}</button></div>
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-400"><p>执行回执核验：{data.evidence.verified_jobs} / {data.evidence.total_jobs} 项作业</p><p>状态来源：{data.state.authority === "sqlite" ? "已保存的任务状态" : data.state.authority === "legacy_json" ? "历史记录" : data.state.authority === "invalid" ? "状态损坏" : "状态缺失"}</p>{data.state.read_only ? <p>结果页仅读取已保存产物</p> : null}</div>
        {exportMessage ? <p role={exportError ? "alert" : "status"} className={`mt-4 text-sm ${exportError ? "text-amber-200" : "text-emerald-200"}`}>{exportMessage}</p> : null}
      </section>
      <section aria-labelledby="facts-title" className="grid gap-4 lg:grid-cols-3">
        <Statements title="已记录的事实" id="facts-title" values={data.conclusions.facts} empty="尚无可呈现的事实结论。" />
        <Statements title="待验证的假设" values={data.conclusions.hypotheses} empty="尚未记录假设。" />
        <Statements title="分析与解释" values={data.conclusions.interpretations} empty="尚未记录分析解释。" />
      </section>
      <section aria-labelledby="metrics-title" className="space-y-3"><h2 id="metrics-title" className="text-lg font-semibold">实验指标</h2>
        {data.metrics.length === 0 ? <Empty>没有可用的实验指标。研究可能尚未执行，或指标产物缺失。</Empty> : <div className="overflow-x-auto rounded-lg border border-mars-border"><table className="w-full min-w-[780px] text-left text-sm"><caption className="sr-only">基线、候选与消融指标记录</caption><thead className="bg-mars-panel text-xs text-slate-400"><tr>{["实验", "类型", "指标", "数值", "单位 / 方向", "证据"].map((label) => <th key={label} scope="col" className="p-3">{label}</th>)}</tr></thead><tbody>{data.metrics.map((metric, index) => <tr key={`${metric.experiment_id}-${metric.name}-${index}`} className="border-t border-mars-border"><th scope="row" className="max-w-56 break-all p-3 font-normal">{metric.experiment_id}</th><td className="p-3">{ROLE[metric.role]}</td><td className="break-all p-3">{metric.name}</td><td className="p-3 font-mono tabular-nums">{displayNumber(metric.value)}</td><td className="p-3 text-slate-400">{metric.unit || "单位未声明"}<br />{metric.direction === "minimize" ? "越小越好" : metric.direction === "maximize" ? "越大越好" : "方向未声明"}</td><td className="p-3 text-xs text-slate-400">{VERIFICATION[metric.verification]}{metric.source_id ? <a href={`#source-${metric.source_id}`} className="mt-1 block text-indigo-200 hover:underline">来源详情</a> : null}</td></tr>)}</tbody></table></div>}
      </section>
      {data.curves.length > 0 ? <section className="space-y-3" aria-labelledby="curves-title"><h2 id="curves-title" className="text-lg font-semibold">已记录的曲线</h2><div className="grid gap-4 lg:grid-cols-2">{data.curves.map((curve, index) => <Curve key={`${curve.job_id}-${curve.metric}-${index}`} curve={curve} />)}</div></section> : null}
      {data.statistics.length > 0 ? <section className="space-y-3"><h2 className="text-lg font-semibold">统计信息</h2><div className="grid gap-3 md:grid-cols-2">{data.statistics.map((item, index) => <div key={`${item.experiment_id}-${item.metric}-${index}`} className="rounded-lg border border-mars-border p-4 text-sm"><p className="break-all">{item.experiment_id} · {item.metric}</p><p className="mt-2 text-slate-400">记录数 {item.n}，均值 {displayNumber(item.mean)}，标准差 {displayNumber(item.standard_deviation)}</p><p className="mt-1 text-xs text-slate-500">{item.independent_repeats ? "已标注独立重复；统计结论仍需结合实验条件。" : "未核验为独立重复，不能据此声称统计显著性。"}</p></div>)}</div></section> : null}
      <section className="space-y-3"><h2 className="text-lg font-semibold">用量记录</h2><dl className="grid grid-cols-2 gap-4 rounded-lg border border-mars-border bg-mars-panel p-5 md:grid-cols-5">{[["逻辑调用记录", displayNumber(data.resources.logical_records)], ["SDK 尝试（已记录）", displayNumber(data.resources.observed_sdk_attempts)], ["输入 token", displayNumber(data.resources.input_tokens)], ["计费输出 token", displayNumber(data.resources.billed_output_tokens)], ["模型费用（账本估算）", data.resources.cost === null || data.resources.currency === null ? "未知" : `${displayNumber(data.resources.cost)} ${data.resources.currency}`]].map(([label, value]) => <div key={label}><dt className="text-xs text-slate-400">{label}</dt><dd className="mt-2 font-mono text-lg tabular-nums">{value}</dd></div>)}</dl><p className="text-xs text-slate-500">{data.resources.usage_complete === true ? "当前记录的 token 用量完整。" : "用量记录完整性尚未确认；未知数据不会按零计算。"} {data.resources.observed_attempts_complete === true ? "SDK 尝试次数记录完整。" : "SDK 尝试次数可能不完整；已记录次数不是总调用量。"}</p></section>
      <Statements title="局限与缺失" values={data.limitations} empty="当前未记录额外局限；这不代表研究已经通过科学或复现验收。" />
      <section className="rounded-lg border border-mars-border p-5"><h2 className="text-lg font-medium">离线阅读与复现</h2><p className="mt-2 text-sm leading-6 text-slate-400">报告可供审阅。独立环境复跑尚未通过；导出包不包含原始私有数据、完整仓库或凭据。</p>{data.reproduction.external_requirements.length > 0 ? <ul className="mt-3 list-inside list-disc space-y-2 text-sm text-slate-400">{data.reproduction.external_requirements.map((value, index) => <li key={index}>{value}</li>)}</ul> : null}</section>
      <details className="rounded-lg border border-mars-border p-5"><summary className="cursor-pointer text-sm font-medium">高级：证据来源与完整性记录（{data.sources.length}）</summary><div className="mt-4 space-y-3">{data.sources.map((source) => <dl id={`source-${source.id}`} key={source.id} className="space-y-1 border-t border-mars-border pt-3 text-xs"><dt className="text-slate-300">{source.id} · {source.kind}</dt><dd className="break-all font-mono text-slate-500">SHA-256 {source.sha256}</dd><dd className="text-slate-500">{source.bytes} 字节</dd></dl>)}</div></details>
    </> : null}
  </>;
}

function Empty({ children }: { children: React.ReactNode }): JSX.Element {
  return <p className="rounded-lg border border-dashed border-mars-border p-6 text-sm leading-6 text-slate-400">{children}</p>;
}

function Statements({ title, values, empty, id }: { title: string; values: string[]; empty: string; id?: string }): JSX.Element {
  return <section className="min-w-0 rounded-lg border border-mars-border bg-mars-panel p-5"><h2 id={id} className="font-medium">{title}</h2>{values.length > 0 ? <ul className="mt-3 list-inside list-disc space-y-2 break-words text-sm leading-6 text-slate-300">{values.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p className="mt-3 text-sm leading-6 text-slate-500">{empty}</p>}</section>;
}

function Curve({ curve }: { curve: RunResults["curves"][number] }): JSX.Element {
  const values = curve.points;
  if (values.length === 0 || values.some((value) => !Number.isFinite(value))) return <Empty>曲线数据缺失或含无效数值。</Empty>;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  // Scale before subtracting: two finite measurements can have an infinite difference.
  const scale = Math.max(Math.abs(minimum), Math.abs(maximum)) || 1;
  const scaledMinimum = minimum / scale;
  const range = maximum / scale - scaledMinimum || 1;
  const points = values.map((value, index) => `${20 + index / Math.max(1, values.length - 1) * 960},${140 - (value / scale - scaledMinimum) / range * 120}`).join(" ");
  return <figure className="min-w-0 rounded-lg border border-mars-border bg-mars-panel p-4"><figcaption className="break-all text-sm">{curve.experiment_id} · {curve.metric}</figcaption><svg viewBox="0 0 1000 160" role="img" aria-label={`${curve.metric}，共 ${values.length} 个记录点，最小 ${minimum}，最大 ${maximum}`} className="my-3 w-full"><line x1="20" y1="140" x2="980" y2="140" stroke="#475569" />{values.length === 1 ? <circle cx="20" cy="140" r="4" fill="#a5b4fc" /> : <polyline points={points} fill="none" stroke="#a5b4fc" strokeWidth="2" vectorEffect="non-scaling-stroke" />}</svg><p className="text-xs text-slate-500">按产物记录顺序 · {values.length} 点 · 范围 {displayNumber(minimum)} – {displayNumber(maximum)}</p><a className="mt-2 inline-block text-xs text-indigo-200 hover:underline" href={`#source-${curve.source_id}`}>核对来源</a></figure>;
}
