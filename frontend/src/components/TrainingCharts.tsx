"use client";
/* eslint-disable @next/next/no-img-element -- SVG evidence is rendered as an isolated image, not injected markup. */

import { useEffect, useRef, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getWorkspaceFile, getWorkspaceTree } from "@/lib/api";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { parseTraining, trainingIdentity, type TrainingPoint } from "@/lib/trainingCharts";

type Trace = { path: string; experiment: string; invocation: string; points: TrainingPoint[]; raw: string; status: string };
type Figure = { path: string; title: string; caption: string; svg: string };
const fmt = (n: number | null | undefined): string => n == null ? "—" : n.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
const statusLabel = (status: string): string => ({ completed: "作业已完成", failed: "作业失败 · 保留过程记录", running: "记录更新中" }[status] || "过程记录 · 状态待核验");

function download(text: string, name: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function TrainingCharts({ runId }: { runId: string }): JSX.Element {
  const [traces, setTraces] = useState<Trace[]>([]), [figures, setFigures] = useState<Figure[]>([]);
  const [selection, setSelection] = useState("");
  const [error, setError] = useState(""), [updated, setUpdated] = useState("");
  const [refresh, setRefresh] = useState(0), [expanded, setExpanded] = useState<Figure | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const reading = useRef(false);
  const figureCache = useRef<{ manifest: string; figures: Figure[] }>({ manifest: "", figures: [] });
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const load = async (): Promise<void> => {
      if (reading.current) { timer = setTimeout(() => void load(), CLIENT_POLICY.controlRefreshMs); return; }
      reading.current = true;
      try {
        const tree = await getWorkspaceTree(runId, "execution", controller.signal);
        const files = tree.entries.filter(entry => entry.kind === "file" && trainingIdentity(entry.relative_path));
        const next = await Promise.all(files.slice(-24).map(async file => {
          const identity = trainingIdentity(file.relative_path)!;
          const source = await getWorkspaceFile(runId, "execution", file.relative_path, controller.signal);
          const points = parseTraining(source.text);
          if (!points.length) throw new Error("训练记录正在写入，暂时保留上次图表。");
          const receiptPath = `local_commands/${identity.experiment}/${identity.invocation}/execution_receipt.json`;
          let status = "running";
          if (tree.entries.some(entry => entry.relative_path === receiptPath)) {
            const receipt = await getWorkspaceFile(runId, "execution", receiptPath, controller.signal);
            const parsed: unknown = JSON.parse(receipt.text);
            if (parsed && typeof parsed === "object" && "status" in parsed && typeof parsed.status === "string") status = parsed.status;
          }
          return { path: file.relative_path, ...identity, points, raw: source.text, status };
        }));
        const manifest = tree.entries.find(entry => entry.relative_path === "visualizations/figures.json");
        let images: Figure[] = [];
        if (manifest) {
          const file = await getWorkspaceFile(runId, "execution", manifest.relative_path, controller.signal);
          if (figureCache.current.manifest === file.text) images = figureCache.current.figures;
          else {
          const doc: unknown = JSON.parse(file.text);
          if (doc && typeof doc === "object" && "figures" in doc && Array.isArray(doc.figures)) {
            for (const f of doc.figures.slice(0, 24)) {
              if (!f || typeof f !== "object" || typeof f.path !== "string" || !/^visualizations\/[A-Za-z0-9_/-]+\.svg$/.test(f.path) || typeof f.title !== "string" || typeof f.caption !== "string") continue;
              const svg = await getWorkspaceFile(runId, "execution", f.path, controller.signal);
              if (svg.exists) images.push({ path: f.path, title: f.title, caption: f.caption, svg: svg.text });
            }
          }
          if (!controller.signal.aborted) figureCache.current = { manifest: file.text, figures: images };
          }
        }
        if (!controller.signal.aborted) { setTraces(next); setFigures(images); setError(""); setUpdated(new Date().toLocaleTimeString("zh-CN")); }
      } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "图表更新失败"); }
      finally { reading.current = false; if (!controller.signal.aborted) timer = setTimeout(() => void load(), Math.max(10000, CLIENT_POLICY.controlRefreshMs)); }
    };
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [runId, refresh]);
  useEffect(() => { if (expanded && !dialog.current?.open) dialog.current?.showModal(); }, [expanded]);
  const selected = traces.find(trace => trace.path === selection) || traces.at(-1);
  const last = selected?.points.at(-1);
  return <section aria-label="训练图表与分析图片" className="space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><h2 className="text-lg font-semibold">训练过程与结果图</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">按实际优化器更新次数绘制。训练过程指标与最终验证结果分别展示，失败作业的曲线也会保留。</p></div>
      <button type="button" className="rounded-lg border border-mars-border px-4 py-2 text-sm" onClick={() => setRefresh(value => value + 1)}>刷新图表</button>
    </header>
    {error ? <p role="status" className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">{error} {updated ? `上次成功更新：${updated}` : ""}</p> : null}
    {!selected ? <p role="status" className="rounded-xl border border-dashed border-mars-border p-10 text-center text-sm text-slate-400">等待训练程序写入 training.csv。已有 TensorBoard 指标可切换到对应视图查看。</p> : <>
      <div className="flex flex-wrap items-center justify-between gap-3"><label className="flex min-w-0 flex-wrap items-center gap-3 text-sm">实验记录<select aria-label="训练图表的实验记录" className="max-w-full rounded-lg border border-mars-border bg-mars-panel px-3 py-2" value={selected.path} onChange={event => setSelection(event.target.value)}>{traces.map(trace => <option key={trace.path} value={trace.path}>{trace.experiment} · {trace.invocation.slice(0, 8)}</option>)}</select></label><span className="text-xs text-slate-400">{statusLabel(selected.status)} · {updated} 更新</span></div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[["实际更新", fmt(last?.updates)], ["最新训练损失", fmt(last?.loss)], ["训练残余 RES", `${fmt(last?.res)} dB`], ["有效记录点", String(selected.points.length)]].map(([label, value]) => <div key={label} className="rounded-xl border border-mars-border bg-mars-panel p-4"><p className="text-xs text-slate-400">{label}</p><p className="mt-2 font-mono text-xl tabular-nums">{value}</p></div>)}</div>
      <div className="grid gap-4 xl:grid-cols-2"><ProcessChart title="训练损失 · 收敛趋势" subtitle="每个记录周期的平均损失，原始线性值；未平滑，空值不补零。" points={selected.points} metric="loss" /><ProcessChart title="训练带内功率 · 对消前后" subtitle="训练原生口径，相对噪声底（dB）；不代替最终验证集指标。" points={selected.points} metric="res" /></div>
      <details className="rounded-xl border border-mars-border p-4"><summary className="cursor-pointer text-sm">数据来源与逐点数值</summary><p className="my-3 break-all font-mono text-xs text-slate-400">execution/{selected.path}</p><button type="button" onClick={() => download(selected.raw, `${selected.experiment}-${selected.invocation.slice(0, 8)}-training.csv`, "text/csv;charset=utf-8")} className="mb-3 text-sm text-indigo-200">下载原始 CSV</button><div className="max-h-72 overflow-auto"><table className="w-full text-right text-xs"><caption className="sr-only">当前实验的真实训练记录</caption><thead><tr>{["轮次", "更新次数", "平均损失", "PIM (dB)", "RES (dB)", "学习率"].map(label => <th key={label} className="p-2">{label}</th>)}</tr></thead><tbody>{selected.points.map((point, i) => <tr key={i} className="border-t border-mars-border">{[point.circle, point.updates, point.loss, point.pim, point.res, point.lr].map((value, k) => <td key={k} className="p-2 font-mono">{k === 5 ? value?.toPrecision(3) : fmt(value)}</td>)}</tr>)}</tbody></table></div></details>
    </>}
    {figures.length ? <section className="space-y-4"><div><h3 className="font-semibold">分析图片 · 可下载</h3><p className="mt-1 text-sm text-slate-400">图内标注统计口径及来源；缺少验证数据的实验不会生成验证结论。</p></div><div className="grid gap-5 2xl:grid-cols-2">{figures.map(figure => <article key={figure.path} className="overflow-hidden rounded-xl border border-mars-border"><button type="button" aria-label={`放大：${figure.title}`} onClick={() => setExpanded(figure)} className="block w-full bg-white"><img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(figure.svg)}`} alt={figure.title} className="h-auto w-full" loading="lazy" /></button><div className="space-y-2 p-4"><h4 className="font-medium">{figure.title}</h4><p className="text-sm leading-6 text-slate-400">{figure.caption}</p><button type="button" onClick={() => download(figure.svg, figure.path.split("/").at(-1)!, "image/svg+xml")} className="text-sm text-indigo-200">下载矢量图 SVG</button></div></article>)}</div></section> : null}
    <dialog ref={dialog} onClose={() => setExpanded(null)} className="max-h-[95dvh] w-[min(1440px,95vw)] rounded-xl border border-mars-border bg-mars-panel p-4 text-slate-100 backdrop:bg-black/80">{expanded ? <><div className="mb-3 flex items-center justify-between gap-4"><h3 className="font-semibold">{expanded.title}</h3><button type="button" autoFocus onClick={() => dialog.current?.close()} className="rounded-lg border border-mars-border px-4 py-2">关闭</button></div><img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(expanded.svg)}`} alt={expanded.title} className="w-full bg-white" /><p className="mt-3 text-sm leading-6 text-slate-400">{expanded.caption}</p></> : null}</dialog>
  </section>;
}

function ProcessChart({ title, subtitle, points, metric }: { title: string; subtitle: string; points: TrainingPoint[]; metric: "loss" | "res" }): JSX.Element {
  return <article className="min-w-0 rounded-xl border border-mars-border bg-mars-panel p-4"><h3 className="text-sm font-semibold">{title}</h3><p className="mb-4 mt-1 text-xs leading-5 text-slate-400">{subtitle}</p><div role="img" aria-label={title + "，逐点数值见下方数据表"} className="h-72"><ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{ top: 12, right: 20, bottom: 24, left: 4 }}><CartesianGrid stroke="#334155" strokeDasharray="3 5" vertical={false} /><XAxis type="number" dataKey="updates" domain={[0, "dataMax"]} tick={{ fill: "#94a3b8", fontSize: 11 }} label={{ value: "实际优化器更新次数", position: "bottom", fill: "#94a3b8", fontSize: 12 }} /><YAxis width={54} domain={["auto", "auto"]} tickFormatter={value => fmt(Number(value))} tick={{ fill: "#94a3b8", fontSize: 11 }} /><Tooltip contentStyle={{ background: "#111827", border: "1px solid #475569", borderRadius: 8, color: "#e2e8f0" }} labelFormatter={value => `更新 ${value} 次`} formatter={(value: number) => fmt(value)} />{metric === "res" ? <Line name="对消前 PIM (dB)" dataKey="pim" stroke="#94a3b8" strokeDasharray="6 4" strokeWidth={2} dot={false} isAnimationActive={false} /> : null}<Line name={metric === "loss" ? "平均训练损失" : "对消后 RES (dB)"} dataKey={metric} stroke={metric === "loss" ? "#a5b4fc" : "#34d399"} strokeWidth={2.5} dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>{metric === "res" ? <p className="mt-2 text-xs text-slate-400">虚线：对消前 PIM　实线：对消后 RES</p> : null}</article>;
}
