"use client";

import { useEffect, useState } from "react";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { getLiteratureEvidence, literatureStatus, type LiteratureEvidence } from "@/lib/literatureEvidence";

export function ResearchEvidencePanel({ runId, project, version, active }: { runId: string; project: string; version: string; active: boolean }): JSX.Element {
  const [view, setView] = useState<LiteratureEvidence | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let reading = false;
    setView(null); setError("");
    const read = async (): Promise<void> => {
      if (reading || controller.signal.aborted) return;
      reading = true;
      try {
        const next = await getLiteratureEvidence(runId, project, controller.signal);
        if (!controller.signal.aborted) { setView(next); setError(""); }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "调研记录暂时无法读取。");
      } finally { reading = false; }
    };
    void read();
    const timer = active ? setInterval(() => void read(), CLIENT_POLICY.readinessRefreshMs) : undefined;
    return () => { controller.abort(); if (timer) clearInterval(timer); };
  }, [runId, project, version, active, reload]);
  return <section aria-label="论文调研" className="border-t border-mars-border px-5 py-5 text-sm sm:px-8">
    <div className="flex items-center justify-between gap-3"><h3 className="font-medium text-slate-100">论文调研</h3><button type="button" onClick={() => setReload(value => value + 1)} className="text-xs text-indigo-300 hover:underline">刷新记录</button></div>
    {error ? <p role="alert" className="mt-3 text-amber-300">{error}</p> : null}
    {!view && !error ? <p className="mt-3 text-xs text-slate-400">正在核对检索与阅读记录…</p> : null}
    {view ? <>
      <p className="mt-3 text-slate-200">检索 {view.statistics_verified ? view.counts.candidates : "待核对"} 篇 · 正文阅读 {view.statistics_verified ? view.counts.read : "待核对"} 篇 · 方案采用 {view.counts.adopted} 篇</p>
      <p className="mt-1 text-xs leading-5 text-slate-500">正文阅读包含实际返回的文本片段；其中 {view.counts.method_complete} 篇具有完整方法页及总结凭据。图表与公式仍需核对。</p>
      {!view.quality_evaluated && view.proposal_version ? <p className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-200">这份方案尚未按新的覆盖与方法比较规则验收。</p> : null}
      {view.coverage.length ? <details className="mt-4 text-xs"><summary className="cursor-pointer text-slate-300">研究问题的覆盖情况</summary><div className="mt-3 space-y-3">{view.coverage.map((item, index) => <div key={index}><h4 className="text-slate-200">{item.label}</h4><p className="mt-1 leading-6 text-slate-400">{item.finding}</p>{item.remaining_gap ? <p className="mt-1 leading-6 text-amber-200">尚待确认：{item.remaining_gap}</p> : null}</div>)}</div></details> : null}
      {view.method_comparison.length ? <div className="mt-4 space-y-2"><h4 className="font-medium text-slate-200">方法比较</h4>{view.method_comparison.map((method, index) => <div key={`${method.direction}:${index}`} className="rounded-lg border border-mars-border p-3 text-xs leading-6"><p className="text-slate-200">{method.direction}</p><p>{method.mechanism}</p><p>与项目的适配：{method.compatibility}</p><p>取舍：{method.tradeoff}</p><p className="text-indigo-200">{method.decision}</p></div>)}</div> : null}
      <details className="mt-4" open={view.sources.length <= 5}><summary className="cursor-pointer text-xs text-slate-300">全部候选与阅读记录（{view.sources.length}）</summary><ol className="mt-3 space-y-2">{view.sources.map(source => <li key={source.id} className="rounded-lg border border-mars-border bg-mars-bg/40 p-3">
        <div className="flex flex-wrap items-start justify-between gap-2"><p className="min-w-0 flex-1 text-slate-200">{source.title}</p><span className="text-xs text-slate-400">{literatureStatus(source)}</span></div>
        {source.reason ? <p className="mt-2 text-xs leading-5 text-slate-400">{source.reason}</p> : null}
        {source.error ? <p className="mt-2 text-xs text-amber-200">{source.error}</p> : null}
        {source.method_summary ? <details className="mt-2 text-xs text-slate-400"><summary className="cursor-pointer">方法总结与限制</summary><p className="mt-2 leading-6">{source.method_summary}</p><p className="mt-2 leading-6">{source.limitations}</p>{source.complete_pages.length ? <p className="mt-2">文本覆盖完整的页：{source.complete_pages.join("、")}</p> : null}</details> : null}
        {/^https?:\/\//.test(source.url) ? <a href={source.url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-xs text-indigo-300 hover:underline">查看论文原文 ↗</a> : null}
      </li>)}</ol></details>
      {view.stop_reason ? <p className="mt-4 text-xs leading-6 text-slate-400">结束理由：{view.stop_reason}</p> : null}
      {view.warnings.map((warning, index) => <p key={index} className="mt-2 text-xs text-amber-200">{warning}</p>)}
    </> : null}
  </section>;
}
