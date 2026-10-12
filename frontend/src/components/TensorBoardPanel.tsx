"use client";

import { useEffect, useState } from "react";
import { openTensorBoard, type TensorBoardSession } from "@/lib/tensorboard";
import { ExperimentComparison } from "./ExperimentComparison";
import { canOverlayImages, comparisonUrl, type TensorBoardDashboard } from "@/lib/tensorboardComparison";

export function TensorBoardPanel({ project, runId, fullHeight = false }: {
  project: string; runId?: string; fullHeight?: boolean;
}): JSX.Element {
  const [session, setSession] = useState<TensorBoardSession | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"tensorboard" | "compare">("tensorboard");
  const [dashboard, setDashboard] = useState<TensorBoardDashboard>("scalars");
  const [compareBaseline, setCompareBaseline] = useState(false);
  const [overlay, setOverlay] = useState(true);
  const [reference, setReference] = useState("");
  const [candidate, setCandidate] = useState("");
  const preferenceKey = `mars-tensorboard-comparison:${project}:${runId || "project"}`;
  const [loadedPreference, setLoadedPreference] = useState("");
  const sources = session?.coverage?.models.filter((source) => source.id && source.scalar_run) || [];
  const recentSources = [...sources].reverse();
  const baseline = sources.find((source) => source.id === reference) || recentSources.find((source) => source.is_baseline);
  const selectedCandidate = sources.find((source) => source.id === candidate && source.id !== baseline?.id)
    || recentSources.find((source) => source.id !== baseline?.id && source.attempt === baseline?.attempt)
    || recentSources.find((source) => source.id !== baseline?.id && !source.is_baseline)
    || recentSources.find((source) => source.id !== baseline?.id);
  const pair = baseline && selectedCandidate ? [baseline, selectedCandidate] : [];
  const comparing = compareBaseline && pair.length === 2;
  const split = comparing && (!overlay || (dashboard === "images" && !canOverlayImages(pair, sources)));
  const frameUrl = session ? comparing ? comparisonUrl(session.url, dashboard, pair, true) : session.url + "#" + dashboard : "";
  useEffect(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(preferenceKey) || "{}");
      const data = saved && typeof saved === "object" ? saved as Record<string, unknown> : {};
      setCompareBaseline(data.enabled === true);
      setOverlay(data.overlay !== false);
      setReference(typeof data.reference === "string" ? data.reference : "");
      setCandidate(typeof data.candidate === "string" ? data.candidate : "");
    } catch { setCompareBaseline(false); setOverlay(true); setReference(""); setCandidate(""); }
    setLoadedPreference(preferenceKey);
  }, [preferenceKey]);
  useEffect(() => {
    if (loadedPreference !== preferenceKey) return;
    try { localStorage.setItem(preferenceKey, JSON.stringify({ enabled: compareBaseline, overlay, reference, candidate })); }
    catch { /* Comparison controls remain usable when storage is unavailable. */ }
  }, [loadedPreference, preferenceKey, compareBaseline, overlay, reference, candidate]);
  useEffect(() => {
    let alive = true;
    setSession(null); setError(""); setLoading(true);
    // TensorBoard's persistent-settings contract; keep the user's other chart settings.
    try {
      const stored: unknown = JSON.parse(localStorage.getItem("_tb_global_settings") || "{}");
      const settings = stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
      localStorage.setItem("_tb_global_settings", JSON.stringify({ ...settings, autoReload: true, autoReloadPeriodInMs: 30000 }));
    } catch { /* TensorBoard's own Reload control remains available. */ }
    void openTensorBoard(project, runId).then((result) => {
      if (alive) setSession(result);
    }).catch((caught: unknown) => {
      if (alive) setError(caught instanceof Error ? caught.message : String(caught));
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [project, runId, retry]);
  return (
    <section className={`flex min-h-0 flex-col overflow-hidden rounded-xl border border-mars-border bg-mars-panel ${fullHeight ? "h-full" : "h-[720px]"}`}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-mars-border px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">TensorBoard · 实验过程</h2>
          <p className="mt-1 text-xs text-slate-400">{runId ? "当前运行的训练指标与实验结果；首条指标写入后即可查看。" : "项目已保存的实验记录，可在 TensorBoard 中选择实验进行对比。"}</p>
        </div>
        <div className="flex items-center gap-3 text-xs">
          {view === "tensorboard" ? <span className="text-slate-400">每 30 秒更新</span> : null}
          {session && !split ? <a href={frameUrl} target="_blank" rel="noreferrer" className="text-mars-accent hover:underline">独立打开 ↗</a> : null}
          <button className="rounded border border-mars-border px-3 py-1.5 hover:bg-mars-subtle" onClick={() => setRetry((value) => value + 1)}>{error ? "重试" : "重新加载"}</button>
        </div>
      </header>
      {runId ? <nav aria-label="实验台视图" className="flex gap-2 border-b border-mars-border px-4 py-3">{([['tensorboard', 'TensorBoard'], ['compare', '多实验对比']] as const).map(([key, label]) => <button type="button" key={key} aria-pressed={view === key} onClick={() => setView(key)} className={`rounded-lg px-3 py-2 text-xs ${view === key ? "bg-mars-accent text-white" : "border border-mars-border text-slate-400"}`}>{label}</button>)}</nav> : null}
      {view === "tensorboard" && session ? <div className="border-b border-mars-border px-4 py-2 text-xs">
        <nav aria-label="可视化内容" className="flex flex-wrap gap-2">{([['scalars', '训练与性能曲线'], ['images', '频谱与模型图像'], ['histograms', '参数分布'], ['text', '配置与记录来源']] as const).map(([key, label]) => <button type="button" key={key} aria-pressed={dashboard === key} onClick={() => setDashboard(key)} className={`rounded px-3 py-1.5 ${dashboard === key ? "bg-mars-accent text-white" : "border border-mars-border text-slate-400"}`}>{label}</button>)}</nav>
        {sources.length >= 2 ? <fieldset className="mt-3 flex flex-wrap items-center gap-3" aria-label="基线对比设置">
          <label className="flex items-center gap-2 text-slate-200"><input type="checkbox" checked={compareBaseline} onChange={(event) => setCompareBaseline(event.target.checked)} />与基线对比</label>
          {compareBaseline ? <>
            <label className="flex items-center gap-2 text-slate-400">基线<select aria-label="对比基线" value={baseline?.id || ""} onChange={(event) => setReference(event.target.value)} className="max-w-64 rounded border border-mars-border bg-mars-panel px-2 py-1.5 text-slate-200"><option value="">选择基线</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.label}</option>)}</select></label>
            <label className="flex items-center gap-2 text-slate-400">候选<select aria-label="对比候选" value={selectedCandidate?.id || ""} onChange={(event) => setCandidate(event.target.value)} className="max-w-64 rounded border border-mars-border bg-mars-panel px-2 py-1.5 text-slate-200">{sources.filter((source) => source.id !== baseline?.id).map((source) => <option key={source.id} value={source.id}>{source.label}</option>)}</select></label>
            <label className="flex items-center gap-2 text-slate-200"><input type="checkbox" checked={overlay} onChange={(event) => setOverlay(event.target.checked)} />放在同一张图上</label>
            <span className="text-slate-500">{split ? "基线和候选分开展示" : "同名指标叠加对比"}</span>
          </> : null}
        </fieldset> : null}
        {compareBaseline && pair.length !== 2 ? <p className="mt-2 text-amber-200">请选择基线和另一组候选记录。</p> : null}
        {comparing && dashboard === "images" && overlay && split ? <p className="mt-2 text-slate-400">所选两组没有单独的叠加图，显示各自已有图像；不会混入其他候选。</p> : null}
        {comparing && pair[0].attempt !== pair[1].attempt ? <p className="mt-2 text-amber-200">两组来自不同作业，请核对数据与训练条件后解释差异。</p> : null}
        {session.coverage?.models.some((model) => !model.native) ? <p className="mt-2 text-slate-400">训练曲线由已保存的真实记录补回；缺少的历史参数快照不会补造。</p> : null}
        {session.coverage?.warnings.map((warning) => <p key={warning} role="alert" className="mt-2 text-amber-200">{warning}</p>)}
      </div> : null}
      {view === "compare" && runId ? <div className="min-h-0 flex-1 overflow-y-auto p-5"><ExperimentComparison runId={runId} /></div> : loading ? <div role="status" className="flex flex-1 items-center justify-center text-sm text-slate-400">正在打开 TensorBoard…</div>
        : error ? <div role="alert" className="flex flex-1 items-center justify-center p-8 text-sm text-amber-200">{error}</div>
        : session ? split ? <div className="grid min-h-0 flex-1 grid-cols-1 gap-px overflow-auto bg-mars-border md:grid-cols-2">{pair.map((source, index) => <section key={source.id} className="flex min-h-[520px] flex-col bg-mars-panel"><h3 className="px-4 py-2 text-xs text-slate-200">{index === 0 ? "基线" : "候选"} · {source.label}<a href={comparisonUrl(session.url, dashboard, [source], false)} target="_blank" rel="noreferrer" className="ml-3 text-mars-accent">独立打开 ↗</a></h3><iframe key={`${source.id}-${dashboard}-${retry}`} src={comparisonUrl(session.url, dashboard, [source], false)} title={`TensorBoard · ${index === 0 ? "基线" : "候选"} · ${source.label}`} className="min-h-0 w-full flex-1 border-0 bg-white" /></section>)}</div>
          : <iframe key={`${session.key}-${retry}-${dashboard}-${frameUrl}`} src={frameUrl} title={comparing ? "TensorBoard · 基线与候选同图对比" : runId ? `TensorBoard · ${runId}` : "TensorBoard · 项目实验记录"} className="min-h-0 w-full flex-1 border-0 bg-white" /> : null}
    </section>
  );
}
