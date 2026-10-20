"use client";

import {useCallback, useEffect, useState} from "react";
import {useParams} from "next/navigation";
import Link from "next/link";
import {TopBar} from "@/components/TopBar";
import {useProject} from "@/lib/project";
import {researchMemoryRequest, researchMemoryEvidenceUrl, type ResearchMemoryOverview, type ResearchMemoryRecord} from "@/lib/api";

const button = "rounded-lg border border-mars-border px-3 py-2 text-sm hover:bg-mars-panel2 disabled:opacity-40";
const qualities: Record<string,string> = {measured:"已有测量",code_only:"只有代码",execution_failure:"运行失败",evidence_conflict:"证据冲突",mock_excluded:"模拟记录，不参与比较",no_measurements:"未找到测量"};
function JsonDetails({label,value}: {label:string;value:unknown}) {
  return <details className="rounded-lg border border-mars-border p-3"><summary className="cursor-pointer text-sm">{label}</summary><pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs text-slate-300">{JSON.stringify(value,null,2)}</pre></details>;
}

export default function ResearchMemoryPage(): JSX.Element {
  const {name=""}=useParams<{name:string}>()||{};
  const {projects}=useProject();
  const [overview,setOverview]=useState<ResearchMemoryOverview|null>(null);
  const [selected,setSelected]=useState<ResearchMemoryRecord|null>(null);
  const [path,setPath]=useState("");
  const [filter,setFilter]=useState("");
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  const [loading,setLoading]=useState(true);
  const [notes,setNotes]=useState("");
  const [limitations,setLimitations]=useState("");
  const [changes,setChanges]=useState("");
  const refresh=useCallback(async()=>setOverview(await researchMemoryRequest<ResearchMemoryOverview>(name)),[name]);
  useEffect(()=>{let live=true;setLoading(true);void researchMemoryRequest<ResearchMemoryOverview>(name).then(data=>{if(live)setOverview(data);}).catch(e=>{if(live)setError(String(e));}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[name]);
  async function act(fn:()=>Promise<void>) {setBusy(true);setError("");setMessage("");try{await fn();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
  async function open(id:string) {await act(async()=>{const row=await researchMemoryRequest<ResearchMemoryRecord>(name,`/${id}`);setSelected(row);setNotes(String(row.annotations.conclusion||""));setLimitations(String(row.annotations.limitations||""));setChanges(String(row.annotations.changes||""));});}
  const records=overview?.records.filter(r=>`${r.title} ${r.annotations.conclusion||""} ${JSON.stringify(r.architecture)}`.toLowerCase().includes(filter.toLowerCase()))||[];
  const label=projects.find(p=>p.name===name)?.display_name||name;
  return <div className="min-h-screen bg-mars-bg"><TopBar/><main className="mx-auto max-w-6xl space-y-5 p-4 md:p-8">
    <Link href={`/projects/${encodeURIComponent(name)}/research`} className="text-sm text-slate-400">← 返回所有研究</Link>
    <header><h1 className="text-2xl font-semibold">{label} · 基线与实验记忆</h1><p className="mt-2 text-sm text-slate-400">保存已有结果、训练配置、曲线、频谱和权重，供后续研究按任务查阅。导入不会启动训练。</p></header>
    {error?<p role="alert" className="rounded-lg border border-red-400/40 p-3 text-red-200">{error}</p>:null}
    {message?<p role="status" className="rounded-lg border border-indigo-400/40 p-3 text-sm text-indigo-200">{message}</p>:null}
    <section className="space-y-3 rounded-xl border border-mars-border bg-mars-panel p-4"><h2 className="font-medium">导入已有实验</h2><p className="text-xs text-slate-400">选择包含 summary.json、config.yaml 和图片的结果文件夹。缺少的条件会标记待补齐，不能直接作为公平比较依据。</p><form className="flex flex-wrap gap-2" onSubmit={e=>{e.preventDefault();void act(async()=>{const result=await researchMemoryRequest<{added:number;existing:number;warnings:string[]}>(name,"/import",{path});await refresh();setMessage(`新增 ${result.added} 条，已有 ${result.existing} 条。${result.warnings.join("；")}`);});}}><input aria-label="已有结果文件夹" value={path} onChange={e=>setPath(e.target.value)} placeholder="结果文件夹的完整路径" className="min-w-0 flex-1 rounded-lg border border-mars-border bg-mars-bg p-2 text-sm"/><button className={button} disabled={busy||!path.trim()}>导入结果</button><button type="button" className={button} disabled={busy} onClick={()=>void act(async()=>{await researchMemoryRequest(name,"/capture-code",{});await refresh();setMessage("已保存基线代码快照。训练性能需要真实结果证据。");})}>保存代码快照</button></form></section>
    <section className="grid gap-3 md:grid-cols-2">{[["original","原始基线"],["current_best","当前最好版本"]].map(([role,title])=>{const row=overview?.records.find(r=>r.id===overview.pointers[role]);return <div key={role} className="rounded-xl border border-mars-border bg-mars-panel p-4"><h2 className="text-sm text-slate-400">{title}</h2>{row?<><button className="mt-2 text-left font-medium text-indigo-200 hover:underline" onClick={()=>void open(row.id)} disabled={busy}>{row.title}</button><p className="mt-2 text-xs text-slate-400">{qualities[row.result_quality]||row.result_quality} · {row.missing.length?`${row.missing.length}项待补齐`:"比较条件已记录"}</p></>:<p className="mt-2 text-sm text-slate-500">尚未指定，从记录详情选择。</p>}</div>;})}</section>
    <div className="flex items-center gap-3"><input aria-label="搜索实验记忆" value={filter} onChange={e=>setFilter(e.target.value)} placeholder="搜索方法、结构或实验结论" className="min-w-0 flex-1 rounded-lg border border-mars-border bg-mars-panel p-2 text-sm"/><span className="text-sm text-slate-400">{records.length} 条记录</span></div>
    {loading?<p role="status">正在读取记忆…</p>:!records.length?<p className="p-8 text-center text-slate-400">还没有匹配的记忆记录。</p>:null}
    <div className="grid items-start gap-5 lg:grid-cols-[320px_1fr]"><section aria-label="实验记忆列表" className="space-y-2">{records.map(r=><button key={r.id} className={`w-full rounded-xl border bg-mars-panel p-4 text-left ${selected?.id===r.id?"border-indigo-400":"border-mars-border"}`} disabled={busy} onClick={()=>void open(r.id)}><span className="block break-words text-sm font-medium">{r.title}</span><span className="mt-2 block text-xs text-slate-400">{qualities[r.result_quality]||r.result_quality} · {new Date(r.created_at).toLocaleDateString()}</span>{r.missing.length?<span className="mt-2 block text-xs text-amber-200">{r.missing.length} 项证据或条件待补齐</span>:null}</button>)}</section>
    {selected?<article className="min-w-0 space-y-4 rounded-xl border border-mars-border bg-mars-panel p-5"><h2 className="break-words text-lg font-semibold">{selected.title}</h2><p className="text-xs text-slate-400">{selected.id}</p>{selected.integrity&&!selected.integrity.intact?<p role="alert" className="text-red-200">保存的证据缺失或损坏，不能复用。</p>:null}
      <div className="flex flex-wrap gap-2">{[["original","指定为原始基线"],["current_best","指定为当前最好版本"]].map(([role,title])=><button key={role} className={button} disabled={busy||!["completed","code_reference"].includes(selected.status)||selected.is_mock||selected.integrity?.intact===false||(role==="original"&&!!overview?.pointers.original&&overview.pointers.original!==selected.id)} onClick={()=>void act(async()=>{await researchMemoryRequest(name,"/select",{record_id:selected.id,role});await refresh();setMessage(`${title}已保存。比较时仍会核对实际协议。`);})}>{title}</button>)}</div>
      {selected.missing.length?<details open className="rounded-lg border border-amber-400/30 p-3"><summary className="text-sm text-amber-200">待补齐的信息</summary><ul className="mt-2 list-inside list-disc text-xs text-slate-400">{selected.missing.map((s,i)=><li key={i}>{s}</li>)}</ul></details>:null}
      <JsonDetails label="模型结构与参数量" value={selected.architecture}/><JsonDetails label="实际测量结果" value={selected.metrics}/><JsonDetails label="训练方式、初始化与超参数" value={selected.recipe}/><JsonDetails label="完整配置" value={selected.config}/><JsonDetails label="比较条件与评测口径" value={selected.protocol}/>
      <section className="space-y-2"><h3 className="text-sm font-medium">研究结论与适用条件</h3><label className="block text-xs text-slate-400">本次改动<textarea value={changes} onChange={e=>setChanges(e.target.value)} className="mt-1 block min-h-16 w-full rounded-lg border border-mars-border bg-mars-bg p-2 text-sm text-slate-200"/></label><label className="block text-xs text-slate-400">实验结论<textarea value={notes} onChange={e=>setNotes(e.target.value)} className="mt-1 block min-h-16 w-full rounded-lg border border-mars-border bg-mars-bg p-2 text-sm text-slate-200"/></label><label className="block text-xs text-slate-400">限制与尚未验证的部分<textarea value={limitations} onChange={e=>setLimitations(e.target.value)} className="mt-1 block min-h-16 w-full rounded-lg border border-mars-border bg-mars-bg p-2 text-sm text-slate-200"/></label><button className={button} disabled={busy} onClick={()=>void act(async()=>{const row=await researchMemoryRequest<ResearchMemoryRecord>(name,`/${selected.id}`,{conclusion:notes,limitations,changes},"PATCH");setSelected(row);await refresh();setMessage("备注已保存，原始测量未改变。");})}>保存结论</button></section>
      <details className="rounded-lg border border-mars-border p-3"><summary className="cursor-pointer text-sm">原始证据 · {selected.evidence.length} 项</summary><div className="mt-3 max-h-80 space-y-3 overflow-auto">{selected.evidence.map((a,i)=><div key={`${a.asset}-${i}`} className="break-all text-xs"><a target="_blank" rel="noreferrer" href={researchMemoryEvidenceUrl(name,selected.id,i)} className="text-indigo-200 hover:underline">{a.category} · {a.name}</a><p className="mt-1 text-slate-500">SHA256 {a.sha256}</p></div>)}</div></details>
    </article>:<p className="rounded-xl border border-dashed border-mars-border p-8 text-sm text-slate-400">选择记录查看结构、训练过程、结果与原始证据。</p>}</div>
    <p className="text-xs text-slate-500">{overview?.policy}</p>
  </main></div>;
}
