"use client";

import { useEffect, useState } from "react";
import { getResults, type RunResults } from "@/lib/results";

const format = (value: number | null | undefined): string => value == null || !Number.isFinite(value) ? "—" : Number.isInteger(value) ? String(value) : value.toFixed(2);
const LABELS: Record<string, string> = { training_loss: "训练损失", cancellation_residual_ratio: "抵消残余功率比", saved_curve_unclassified: "历史曲线（指标口径未记录）" };
const ROLES = { baseline: "基线", candidate: "候选", ablation: "消融", unknown: "角色未标注" };

export function ExperimentComparison({ runId, results }: { runId: string; results?: RunResults }): JSX.Element {
  const [loaded, setLoaded] = useState<RunResults | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [reference, setReference] = useState("");
  const [selectedMetric, setSelectedMetric] = useState("");
  useEffect(() => {
    if (results) return;
    const controller = new AbortController(); setError(""); setLoaded(null);
    void getResults(runId, controller.signal).then(next => { if (!controller.signal.aborted) setLoaded(next); }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "结果读取失败"); });
    return () => controller.abort();
  }, [runId, results, refresh]);
  const data = results || loaded;
  const experiments = data?.experiments.filter(item => item.verification === "verified_local_receipt").sort((a, b) => a.experiment_id.localeCompare(b.experiment_id)) || [];
  const referenceId = experiments.some(item => item.experiment_id === reference) ? reference : experiments.find(item => item.role === "baseline")?.experiment_id || "";
  const availableMetrics = Array.from(new Set(data?.curves.map(curve => curve.metric) || []));
  const metric = availableMetrics.includes(selectedMetric) ? selectedMetric : availableMetrics.includes("RES") ? "RES" : availableMetrics[0] || "";
  const curves = data?.curves.filter(curve => curve.metric === metric && curve.points.length && curve.points.every(Number.isFinite)) || [];
  const values = curves.flatMap(curve => curve.points);
  const low = values.length ? values.reduce((value, next) => Math.min(value, next), Infinity) : 0;
  const high = values.length ? values.reduce((value, next) => Math.max(value, next), -Infinity) : 1;
  const scale = Math.max(Math.abs(low), Math.abs(high)) || 1;
  const spread = high / scale - low / scale || 1;
  const maxPoints = Math.max(2, ...curves.map(curve => curve.points.length));
  const colors = ["#a5b4fc", "#34d399", "#fb923c", "#38bdf8", "#e879f9", "#facc15"];
  const observed = (id: string, name: string): number | null => data?.metrics.find(item => item.experiment_id === id && item.name === name && item.verification === "verified_local_receipt")?.value ?? null;
  const metricNames = ["RES", "APE", "PIM", "loss"].filter(name => data?.metrics.some(item => item.name === name) && (name !== "loss" || !data?.metrics.some(item => item.name === "cancellation_residual_ratio")));
  const columns = metricNames.length ? metricNames : Array.from(new Set(data?.metrics.map(item => item.name) || [])).slice(0, 4);
  return <section aria-label="多实验对比" className="space-y-4">
    <header className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">实验对比</h2><p className="mt-1 text-xs leading-6 text-slate-400">真实作业的最终指标与已记录曲线。数值差异不代表统计显著性或目标达成。</p></div>{!results ? <button type="button" className="rounded-lg border border-mars-border px-3 py-2 text-xs" onClick={() => setRefresh(value => value + 1)}>刷新对比</button> : null}</header>
    {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : !data ? <p role="status" className="text-sm text-slate-400">正在读取作业记录…</p> : !experiments.length ? <p className="text-sm text-slate-400">尚无通过执行回执核验的实验。</p> : <>
      <label className="flex flex-wrap items-center gap-3 text-xs text-slate-400">对照参考<select aria-label="对照参考" className="max-w-full rounded-lg border border-mars-border bg-mars-panel px-3 py-2 text-slate-200" value={referenceId} onChange={event => setReference(event.target.value)}><option value="">选择一项实验查看差值</option>{experiments.map(item => <option key={`${item.experiment_id}:${item.job_id}`} value={item.experiment_id}>{item.experiment_id}</option>)}</select></label>
      {experiments.every(item => item.role === "unknown") ? <p className="text-xs leading-6 text-amber-200">历史作业未保存结构化角色。可自行选择对照参考；系统不会根据实验名称猜测基线或候选。</p> : null}
      <div className="overflow-x-auto rounded-lg border border-mars-border"><table className="w-full text-left text-xs"><caption className="sr-only">真实实验指标与所选参考的差值</caption><thead className="bg-mars-panel text-slate-400"><tr><th className="p-3">实验 / 种子</th><th className="p-3">角色</th>{columns.map(name => <th key={name} className="p-3">{name}{["RES", "APE", "PIM"].includes(name) ? " (dB)" : ""}{referenceId ? " / 差值" : ""}</th>)}</tr></thead><tbody>{experiments.map(item => <tr key={`${item.experiment_id}:${item.job_id}`} className="border-t border-mars-border"><th scope="row" className="max-w-72 break-all p-3 font-normal">{item.experiment_id}<span className="mt-1 block text-slate-500">种子 {item.seed ?? "未记录"} · {format(item.duration_seconds)} 秒</span></th><td className="p-3 text-slate-400">{ROLES[item.role]}</td>{columns.map(name => { const value = observed(item.experiment_id, name), baseline = observed(referenceId, name); return <td key={name} className="whitespace-nowrap p-3 font-mono tabular-nums">{format(value)}{referenceId && item.experiment_id !== referenceId && value !== null && baseline !== null ? <span className="mt-1 block text-slate-400">Δ {value - baseline > 0 ? "+" : ""}{(value - baseline).toFixed(2)}</span> : null}</td>; })}</tr>)}</tbody></table></div>
      {referenceId ? <p className="text-xs leading-6 text-slate-500">差值 = 当前实验 − 所选参考。是否同条件、配对种子及指标方向请结合已审核实验方案；此处不自动判定达标。</p> : null}
      {curves.length ? <details className="rounded-lg border border-mars-border p-4"><summary className="cursor-pointer text-sm">训练过程与指标曲线（{curves.length} 组）</summary><label className="mt-4 flex items-center gap-3 text-xs">指标<select aria-label="对比曲线指标" value={metric} onChange={event => setSelectedMetric(event.target.value)} className="rounded-lg border border-mars-border bg-mars-panel p-2">{availableMetrics.map(name => <option key={name} value={name}>{LABELS[name] || name}</option>)}</select></label><svg role="img" aria-label={`${LABELS[metric] || metric}，${curves.length} 组实验，使用共同坐标轴`} viewBox="0 0 800 250" className="my-3 w-full"><line x1="60" y1="210" x2="780" y2="210" stroke="#475569" /><text x="5" y="35" fill="#94a3b8" fontSize="12">{format(high)}</text><text x="5" y="210" fill="#94a3b8" fontSize="12">{format(low)}</text>{curves.map((curve, index) => <polyline key={`${curve.job_id}:${index}`} points={curve.points.map((value, step) => `${60 + step / (maxPoints - 1) * 720},${210 - (value / scale - low / scale) / spread * 180}`).join(" ")} fill="none" stroke={colors[index % colors.length]} strokeWidth="2" vectorEffect="non-scaling-stroke" />)}<text x="60" y="240" fill="#94a3b8" fontSize="12">记录点 1</text><text x="700" y="240" fill="#94a3b8" fontSize="12">{maxPoints}</text></svg><div className="flex flex-wrap gap-x-5 gap-y-2 text-xs">{curves.map((curve, index) => <span key={`${curve.job_id}:${index}`} style={{ color: colors[index % colors.length] }}>{curve.experiment_id}</span>)}</div><p className="mt-3 text-xs text-slate-500">按实际记录顺序展示。RES 使用原始测量 dB；不从训练损失推算 RES，不添加未经方案确认的门槛。</p></details> : null}
    </>}
  </section>;
}
