"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { TopBar } from "@/components/TopBar";
import { useProject } from "@/lib/project";
import { listRuns, type RunSummary } from "@/lib/api";
import { useConversationHistory } from "@/components/ConversationHistory";
import { projectResearch, researchDate } from "@/lib/projectResearch";

export default function ProjectResearchPage(): JSX.Element {
  const { name = "" } = useParams<{ name: string }>() || {};
  const { projects, setSelectedProject } = useProject();
  const history = useConversationHistory();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => { if (projects.some(project => project.name === name)) setSelectedProject(name); }, [name, projects, setSelectedProject]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    void listRuns(name, controller.signal).then(next => { if (!controller.signal.aborted) setRuns(next); }).catch(() => { if (!controller.signal.aborted) setError("研究记录暂时无法加载，请重新连接。"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [name, revision]);
  const label = projects.find(project => project.name === name)?.display_name || name;
  const records = projectResearch(history.rows, runs, name).filter(row => row.title.toLocaleLowerCase().includes(filter.toLocaleLowerCase().trim()));
  const failure = error || history.error;
  return <div className="min-h-screen bg-mars-bg"><TopBar /><main className="mx-auto max-w-5xl space-y-6 p-4 md:p-8">
    <Link href="/projects" className="text-sm text-slate-400 hover:text-white">← 返回项目</Link>
    <header className="flex flex-wrap items-center justify-between gap-4"><div><h1 className="text-2xl font-semibold">{label} · 所有研究</h1><p className="mt-2 text-sm text-slate-400">每次研究保留独立对话，按最近更新排序。</p></div><Link href={`/runs/new?project=${encodeURIComponent(name)}&new=1`} className="rounded-lg bg-mars-accent px-4 py-2 text-sm font-medium">启动研究</Link></header>
    <div className="flex gap-3"><input type="search" aria-label="搜索研究" placeholder="搜索研究内容" value={filter} onChange={event => setFilter(event.target.value)} className="min-w-0 flex-1 rounded-lg border border-mars-border bg-mars-panel px-4 py-2 text-sm" /><button type="button" onClick={() => { setRevision(value => value + 1); void history.refresh(); }} disabled={loading || history.loading} className="rounded-lg border border-mars-border px-4 py-2 text-sm disabled:opacity-40">刷新</button></div>
    {failure ? <p role="alert" className="rounded-lg border border-amber-500/30 p-4 text-sm text-amber-200">{failure}</p> : null}
    {loading || history.loading ? <p role="status" className="py-12 text-center text-slate-400">正在读取研究…</p> : !failure && !records.length ? <p className="rounded-xl border border-dashed border-mars-border p-10 text-center text-slate-400">{filter ? "没有匹配的研究" : "还没有研究，点击启动研究开始新的对话。"}</p> : null}
    <section aria-label="研究列表" className="space-y-3">{records.map(row => <article key={row.id} className="relative rounded-xl border border-mars-border bg-mars-panel p-5 transition hover:border-indigo-400/50"><Link href={row.href} className="absolute inset-0 rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400" aria-label={`打开研究 ${row.title}`} /><div className="pointer-events-none"><h2 className="pr-28 text-base font-medium">{researchDate(row.createdAt)} · {row.title.replace(/\s+/g, " ").slice(0, 80)}</h2><p className="mt-3 text-xs text-slate-400">{row.processing ? "正在处理 · " : ""}{row.messages ? `${row.messages} 条消息 · ` : ""}更新于 {researchDate(row.updatedAt)}</p></div>{row.runId ? <Link href={`/results/${encodeURIComponent(row.runId)}`} className="relative z-10 mt-4 inline-block rounded-lg border border-mars-border px-3 py-2 text-xs text-indigo-200 hover:bg-mars-panel2">查看结果</Link> : <span className="mt-4 block text-xs text-slate-500">讨论中，尚未生成实验结果</span>}</article>)}</section>
  </main></div>;
}
