"use client";

import { useEffect, useState } from "react";
import { getProjectTraining, saveProjectTraining, type ProjectTraining, type TrainingDraft } from "@/lib/api";

const field = "mt-2 block min-h-11 w-full rounded-lg border border-mars-border bg-mars-bg px-3 py-2 text-sm";
export function ProjectTrainingSetup({ project, onBusy }: { project: string; onBusy: (value: boolean) => void }): JSX.Element {
  const [view, setView] = useState<ProjectTraining | null>(null);
  const [draft, setDraft] = useState<TrainingDraft | null>(null);
  const [busy, setBusy] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => { onBusy(busy); return () => onBusy(false); }, [busy, onBusy]);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError("");
    void getProjectTraining(project, controller.signal).then((data) => {
      if (!controller.signal.aborted) { setView(data); setDraft(data.draft); setDirty(false); }
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "读取训练配置失败"); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [project, reload]);
  function change(patch: Partial<TrainingDraft>) { setDraft((old) => old ? { ...old, ...patch } : old); setDirty(true); }
  async function save() {
    if (!draft) return;
    setBusy(true); setError("");
    try { const data = await saveProjectTraining(project, draft); setView(data); setDraft(data.draft); setDirty(false); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败，请重试"); }
    finally { setBusy(false); }
  }
  return <section className="space-y-4 text-sm leading-6" aria-label="训练启动设置">
    <p className="text-slate-400">在接入代码后设置一次：用哪个 Python 启动训练入口。后续任务沿用本项目设置，无需编辑配置文件或重启服务。</p>
    {busy && !view ? <p role="status">正在读取本机训练环境…</p> : null}
    {view?.backend !== "local_command" && view ? <p>当前使用 {view.backend} 执行适配器，无需配置本机训练入口。</p> : draft ? <>
      <label className="block">训练使用的 Python<input className={field} value={draft.python} list="training-python-options" disabled={busy} onChange={(e) => change({ python: e.target.value })} /><datalist id="training-python-options">{view?.python_options.map((item) => <option key={item} value={item} />)}</datalist><span className="text-xs text-slate-400">优先推荐已用过的 Python。使用它原有的依赖，不会重新安装。</span></label>
      <label className="block">训练入口脚本<input className={field} value={draft.entrypoint} disabled={busy} onChange={(e) => change({ entrypoint: e.target.value })} /><span className="text-xs text-slate-400">相对于代码目录，通常保留 mars_job.py。编码 Agent 会创建或适配此入口，调用你的训练代码并交付结果。</span></label>
      <details className="rounded-lg border border-mars-border p-3"><summary className="cursor-pointer">参数与结果约定（可选）</summary><div className="mt-3 space-y-3">
        <label className="block">实验参数文件位置<input className={field} disabled={busy} value={draft.config_pattern} onChange={(e) => change({ config_pattern: e.target.value })} /></label>
        <label className="block">必须输出的指标（用逗号分隔，可留空）<input className={field} disabled={busy} value={draft.required_metrics.join(", ")} onChange={(e) => change({ required_metrics: e.target.value.split(/[,，]/).map((x) => x.trim()) })} /></label>
        <label className="flex gap-2"><input type="checkbox" disabled={busy} checked={draft.consumes_data_path} onChange={(e) => change({ consumes_data_path: e.target.checked })} />使用本项目选定的数据</label>
      </div></details>
      <button type="button" onClick={() => void save()} disabled={busy || !draft.python.trim() || !draft.entrypoint.trim()} className="min-h-11 rounded-lg bg-mars-accent px-4 py-2 text-white disabled:opacity-40">{busy ? "正在保存…" : "保存训练启动设置"}</button>
      <p role="status" className={dirty || !view?.configured ? "text-amber-200" : "text-emerald-200"}>{dirty ? "设置已修改，保存后生效。" : view?.message}</p>
      <p className="text-xs text-slate-500">这里只检查启动配置和入口位置。训练依赖、数据、实验参数会在正式执行前验证，保存不代表实验已通过。</p>
    </> : null}
    {error ? <div role="alert" className="text-amber-200"><p>{error}</p><button type="button" disabled={busy} onClick={() => setReload((n) => n + 1)} className="mt-2 min-h-11 underline">重新读取</button></div> : null}
  </section>;
}
