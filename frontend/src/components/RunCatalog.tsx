"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listRuns, type RunSummary } from "@/lib/api";
import { useProject } from "@/lib/project";

export function RunCatalog({ results = false }: { results?: boolean }): JSX.Element {
  const { selectedProject, projects } = useProject();
  const [records, setRecords] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [filter, setFilter] = useState("");
  const [currentProjectOnly, setCurrentProjectOnly] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setRecords([]);
    void listRuns(currentProjectOnly ? selectedProject : undefined, controller.signal).then((runs) => {
      if (!controller.signal.aborted) setRecords([...runs].reverse());
    }).catch(() => {
      if (!controller.signal.aborted) setError("无法加载研究记录。请检查本地服务连接后重试。");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [selectedProject, currentProjectOnly, refresh]);
  const query = filter.trim().toLocaleLowerCase();
  const filtered = records.filter((run) => `${run.task} ${run.run_id}`.toLocaleLowerCase().includes(query));
  const projectName = projects.find((project) => project.name === selectedProject)?.display_name || selectedProject;
  return <>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="space-y-2"><p className="text-sm text-slate-400">{currentProjectOnly && projectName ? projectName : "全部项目"}{!loading && !error ? ` · ${records.length} 项记录` : ""}</p><label className="flex items-center gap-2 text-xs text-slate-400"><input type="checkbox" checked={currentProjectOnly} disabled={!selectedProject} onChange={(event) => setCurrentProjectOnly(event.target.checked)} />仅看当前项目</label></div>
      <div className="flex flex-wrap items-center gap-2"><label className="sr-only" htmlFor="filter-runs">搜索研究名称或编号</label><input id="filter-runs" type="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="搜索研究名称或编号" className="w-full rounded-md border border-mars-border bg-mars-panel px-3 py-2 text-sm sm:w-64" /><button type="button" disabled={loading} onClick={() => setRefresh((value) => value + 1)} className="rounded-md border border-mars-border px-3 py-2 text-sm disabled:opacity-50">刷新</button></div>
    </div>
    {loading ? <p role="status" className="py-12 text-center text-slate-400">正在加载研究记录…</p> : error ? <p role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">{error}</p> : filtered.length === 0 ? <section className="rounded-lg border border-dashed border-mars-border p-10 text-center"><h2 className="font-medium">{query ? "没有匹配的研究" : "还没有研究记录"}</h2><p className="mt-2 text-sm text-slate-400">{query ? "请尝试其他名称或编号。" : "从顶部新建研究；研究过程与已有成果会保存在这里。"}</p></section> : <div className="overflow-x-auto rounded-lg border border-mars-border bg-mars-panel">
      <table className="w-full min-w-[640px] text-left text-sm"><caption className="sr-only">{results ? "研究结果记录" : "研究任务记录"}</caption><thead className="border-b border-mars-border text-xs text-slate-400"><tr><th scope="col" className="p-4">研究</th><th scope="col" className="p-4">创建时间</th><th scope="col" className="p-4">操作</th></tr></thead><tbody>
        {filtered.map((run) => <tr key={run.run_id} className="border-b border-mars-border last:border-0"><th scope="row" className="max-w-md p-4 font-normal"><Link href={results ? `/results/${encodeURIComponent(run.run_id)}` : `/runs/${encodeURIComponent(run.run_id)}`} className="break-words font-medium text-slate-100 hover:text-indigo-200">{run.task}</Link><p className="mt-1 break-all font-mono text-xs text-slate-500">{run.run_id}</p></th><td className="whitespace-nowrap p-4 text-slate-400"><time dateTime={run.created_at}>{run.created_at.replace("T", " ").slice(0, 16)}</time></td><td className="p-4"><div className="flex flex-wrap gap-4"><Link href={`/runs/${encodeURIComponent(run.run_id)}`} className="whitespace-nowrap text-indigo-200 hover:underline">任务详情</Link><Link href={`/results/${encodeURIComponent(run.run_id)}`} className="whitespace-nowrap text-indigo-200 hover:underline">结果与导出</Link></div></td></tr>)}
      </tbody></table>
    </div>}
    {results ? <p className="text-xs text-slate-500">记录可能仍在运行、已停止或缺少实验产物。打开后查看实际状态、证据与局限。</p> : null}
  </>;
}
