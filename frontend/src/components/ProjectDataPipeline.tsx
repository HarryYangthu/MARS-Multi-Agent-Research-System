"use client";

import { useEffect, useState } from "react";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { dataPipelineAction, dataPipelineArtifact, inspectDataPipelineSource, listDataPipelineJobs,
  listDataSources, setDefaultDataSource, startDataPipeline, uploadDataSource,
  type DataPipelineField, type DataPipelineJob, type DataSourceProfile } from "@/lib/api";

const INPUT = "mt-1 w-full rounded border border-mars-border bg-mars-panel px-3 py-2 text-sm";
const BUTTON = "rounded border border-mars-border px-3 py-2 text-sm hover:bg-mars-panel2 disabled:opacity-40";
const STATUS: Record<string, string> = { processing: "正在处理数据", processed: "处理完成", analyzing: "正在生成简报", analyzed: "简报已生成", failed: "处理失败", analysis_failed: "简报生成失败", interrupted: "处理已中断" };

export function ProjectDataPipeline({ project }: { project: string }): JSX.Element {
  const [sources, setSources] = useState<DataSourceProfile[]>([]);
  const [source, setSource] = useState("");
  const [defaultId, setDefaultId] = useState("");
  const [notice, setNotice] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [fields, setFields] = useState<DataPipelineField[]>([]);
  const [signalKey, setSignalKey] = useState("");
  const [reference, setReference] = useState("");
  const [fs, setFs] = useState("");
  const [axis, setAxis] = useState<0 | 1>(1);
  const [channel, setChannel] = useState("0");
  const [shift, setShift] = useState("0");
  const [delay, setDelay] = useState("0");
  const [autoAlign, setAutoAlign] = useState(false);
  const [referenceMode, setReferenceMode] = useState<"linear" | "cubic">("linear");
  const [cutoff, setCutoff] = useState("");
  const [jobs, setJobs] = useState<DataPipelineJob[]>([]);
  const [selectedJob, setSelectedJob] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const job = jobs.find(item => item.id === selectedJob) ?? jobs[0];
  const running = jobs.some(item => item.status === "processing" || item.status === "analyzing");

  useEffect(() => {
    let alive = true;
    void listDataSources(project).then(items => { if (alive) { setSources(items); const saved = items.find(item => item.is_default)?.id || ""; setDefaultId(saved); setSource(saved || items[0]?.id || ""); } }).catch(e => { if (alive) setError(String(e)); });
    return () => { alive = false; };
  }, [project]);
  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh(): Promise<void> {
      try { const items = await listDataPipelineJobs(project, controller.signal); if (!controller.signal.aborted) { setJobs(items); setPollError(""); } }
      catch (e) { if (!controller.signal.aborted) setPollError(String(e)); }
      finally { if (!controller.signal.aborted) timer = setTimeout(() => { void refresh(); }, CLIENT_POLICY.controlRefreshMs); }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [project, expanded]);
  useEffect(() => {
    setFields([]); setSignalKey(""); setReference("");
    if (!source || !expanded) return;
    const controller = new AbortController();
    void inspectDataPipelineSource(project, source, controller.signal).then(items => {
      if (controller.signal.aborted) return;
      setFields(items); setSignalKey(items.find(item => item.shape.length === 2)?.key ?? "");
    }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
    return () => controller.abort();
  }, [source, project, expanded]);

  async function perform(action: () => Promise<DataPipelineJob>): Promise<void> {
    setBusy(true); setError("");
    try { const result = await action(); setJobs(items => [result, ...items.filter(item => item.id !== result.id)]); setSelectedJob(result.id); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function upload(file: File): Promise<void> {
    setBusy(true); setError("");
    try {
      const profile = await uploadDataSource({ file, project, kind: "auto" });
      setSources(items => [profile, ...items]); setSource(profile.id);
      if (profile.fs_mhz) setFs(String(profile.fs_mhz));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function selectForResearch(): Promise<void> {
    setBusy(true); setError(""); setNotice("");
    try { await setDefaultDataSource(project, source); setDefaultId(source); setNotice("已设置本项目的默认数据，仿真启动前还会核对数据和划分方式。"); }
    catch (cause: unknown) { setError(cause instanceof Error ? cause.message : "设置未确认，请重新读取数据列表核对。"); }
    finally { setBusy(false); }
  }

  return <section className="space-y-4">
    <p className="text-sm leading-6 text-slate-400">数据可保留在自己的目录，在研究目标中提供绝对路径；也可上传并设为本项目默认数据。没有数据时可稍后接入。</p>
    <div className="flex flex-wrap items-center gap-3">
      <label className={BUTTON}>上传数据<input type="file" aria-label="上传研究数据" accept=".mat,.npz,.pth,.pt,.npy,.csv,.json,.h5,.hdf5" className="sr-only" disabled={busy || running} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void upload(file); }} /></label>
      <span className="text-xs text-slate-500">MAT / NPZ / PTH / CSV / JSON / HDF5 · 原始数据保留</span>
    </div>
    <label className="block text-xs text-slate-400">数据文件<select className={INPUT} value={source} disabled={busy || running} onChange={e => { setSource(e.target.value); const item = sources.find(s => s.id === e.target.value); setFs(item?.fs_mhz ? String(item.fs_mhz) : ""); }}><option value="">请选择</option>{sources.map(item => <option key={item.id} value={item.id}>{item.original_name}</option>)}</select></label>
    <button type="button" className={BUTTON} disabled={busy || !source || defaultId === source} onClick={() => void selectForResearch()}>{defaultId && defaultId === source ? "已设为本项目默认数据" : "用于本项目研究"}</button>
    {notice ? <p role="status" className="text-xs text-emerald-200">{notice}</p> : null}
    {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
    <details open={expanded} onToggle={event => setExpanded(event.currentTarget.open)} className="rounded-lg border border-mars-border p-4"><summary className="cursor-pointer text-sm">PIMC 信号处理（可选）</summary><div className="mt-4 space-y-4"><p className="text-xs leading-5 text-slate-500">适用于已接入 PIMC 信号处理代码的项目。仅选择默认研究数据时，无需进行以下处理或调用模型。</p>
    <fieldset disabled={busy || running} className="grid gap-3 text-xs text-slate-400 sm:grid-cols-3">
      <label>处理信号<select className={INPUT} value={signalKey} onChange={e => setSignalKey(e.target.value)}><option value="">请选择字段</option>{fields.map(item => <option key={item.key}>{item.key}</option>)}</select><span>{fields.find(f => f.key === signalKey)?.shape.join(" × ")}</span></label>
      <label>采样率（MHz）<input className={INPUT} type="number" min="0" step="any" value={fs} onChange={e => setFs(e.target.value)} /></label>
      <label>数据布局<select className={INPUT} value={axis} onChange={e => setAxis(e.target.value === "0" ? 0 : 1)}><option value="1">通道 × 采样</option><option value="0">采样 × 通道</option></select></label>
      <label>移频（MHz，正值向高频）<input type="number" step="any" className={INPUT} value={shift} onChange={e => setShift(e.target.value)} /></label>
      <label>低通截止（MHz，可留空）<input type="number" step="any" min="0" className={INPUT} value={cutoff} onChange={e => setCutoff(e.target.value)} /></label>
      <label>显示 / 估计通道（从 0 起）<input type="number" min="0" className={INPUT} value={channel} onChange={e => setChannel(e.target.value)} /></label>
    </fieldset>
    <details className="rounded border border-mars-border p-3 text-sm"><summary className="cursor-pointer">时延对齐</summary><fieldset disabled={busy || running} className="mt-3 grid gap-3 text-xs text-slate-400 sm:grid-cols-2">
      <label>参考字段<select className={INPUT} value={reference} onChange={e => setReference(e.target.value)}><option value="">不对齐</option>{fields.map(f => <option key={f.key}>{f.key}</option>)}</select></label>
      <label>参考方式<select className={INPUT} value={referenceMode} onChange={e => setReferenceMode(e.target.value === "cubic" ? "cubic" : "linear")}><option value="linear">直接互相关</option><option value="cubic">单带三阶参考 x·|x|²</option></select></label>
      <label className="flex items-center gap-2"><input type="checkbox" checked={autoAlign} onChange={e => { setAutoAlign(e.target.checked); setDelay("0"); }} />自动估计公共时延</label>
      <label>手动时延（采样点，正值裁信号头部）<input disabled={autoAlign} type="number" className={INPUT} value={delay} onChange={e => setDelay(e.target.value)} /></label>
    </fieldset></details>
    <button className={`${BUTTON} bg-mars-accent text-white`} disabled={busy || running || !source || !signalKey || !(Number(fs) > 0)} onClick={() => void perform(() => startDataPipeline(project, { source_id: source, signal_key: signalKey, reference_key: reference, sample_axis: axis, channel: Number(channel), fs_mhz: Number(fs), shift_mhz: Number(shift), delay_samples: Number(delay), auto_align: autoAlign, reference_mode: referenceMode, lowpass_mhz: cutoff === "" ? null : Number(cutoff) }))}>分析并处理</button>
    {pollError ? <p role="status" className="text-xs text-amber-200">连接暂时中断，正在重连…</p> : null}
    {job ? <div className="space-y-3 rounded-lg border border-mars-border p-4">
      <div className="flex flex-wrap justify-between gap-2"><p role="status" className="text-sm">{STATUS[job.status] ?? job.status}</p><select aria-label="处理记录" className="max-w-72 bg-mars-panel text-xs" value={job.id} onChange={e => setSelectedJob(e.target.value)}>{jobs.map(j => <option key={j.id} value={j.id}>{j.id.slice(0, 8)} · {j.params.signal_key} · {STATUS[j.status]}</option>)}</select></div>
      {job.error ? <p role="alert" className="text-sm text-amber-200">{job.error}</p> : null}
      {job.metrics ? <>
        <p className="text-xs text-slate-400">{job.metrics.input_shape.join(" × ")} → {job.metrics.output_shape.join(" × ")} · RMS {job.metrics.rms_before.toPrecision(4)} → {job.metrics.rms_after.toPrecision(4)}</p>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img alt="处理前后数字基带功率谱" className="w-full rounded" src={dataPipelineArtifact(project, job.id, "spectrum.png")} />
        <p className="text-xs text-slate-500">频谱使用前 {job.metrics.spectrum_samples.toLocaleString()} 点 · 数字幅度 dB/MHz，非 dBm</p>
        <div className="flex flex-wrap gap-2"><a className={BUTTON} href={dataPipelineArtifact(project, job.id, "processed.npz")} download>下载处理数据</a><button disabled={busy || running} className={BUTTON} onClick={() => void perform(() => dataPipelineAction(project, job.id, "analyze"))}>{job.summary ? "重新生成简报" : "LLM 分析"}</button></div>
        <details className="text-xs text-slate-500"><summary>数据口径</summary>{job.metrics.warnings.map(w => <p key={w}>{w}</p>)}</details>
      </> : null}
      {job.summary ? <div className="space-y-3 border-t border-mars-border pt-3"><p className="whitespace-pre-wrap text-sm leading-6">{job.summary}</p><button disabled={busy || running || job.status !== "analyzed"} className={BUTTON} onClick={() => void perform(() => dataPipelineAction(project, job.id, "share"))}>用于后续 Agent</button>{job.shared ? <span className="ml-3 text-xs text-emerald-300">已共享 · 新任务自动加载</span> : null}</div> : null}
    </div> : null}
    </div></details>
  </section>;
}
