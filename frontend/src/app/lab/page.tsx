"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { TopBar } from "@/components/TopBar";
import { TensorBoardPanel } from "@/components/TensorBoardPanel";
import { listRuns, type RunSummary } from "@/lib/api";
import { useProject } from "@/lib/project";
import { researchConversationUrl } from "@/lib/runConversation";

function labUrl(runId: string, returnTo: string): string {
  const params = new URLSearchParams();
  if (runId) params.set("run", runId);
  if (returnTo) params.set("returnTo", returnTo);
  const query = params.toString();
  return `/lab${query ? `?${query}` : ""}`;
}

function ExperimentLab(): JSX.Element {
  const { selectedProject, setSelectedProject, projects, loading: projectLoading } = useProject();
  const search = useSearchParams();
  const router = useRouter();
  const runId = search?.get("run") || "";
  const source = search?.get("returnTo") || "";
  const returnTo = /^\/runs\/new(?:\?|$)/.test(source) ? source : "";
  const routedProject = search?.get("project") || "";
  const project = routedProject || selectedProject;
  const projectLabel = projects.find((item) => item.name === project)?.display_name || project;
  const previousProject = useRef<string | null>(null);
  useEffect(() => {
    // Hydrating the saved project is not a user project switch. Keep deep-linked
    // experiments until the provider has established the initial selection.
    if (projectLoading || !project) return;
    if (previousProject.current !== null && previousProject.current !== project && !routedProject && runId) router.replace("/lab");
    previousProject.current = project;
  }, [project, projectLoading, routedProject, runId, router]);
  useEffect(() => {
    if (routedProject) {
      if (routedProject !== selectedProject) setSelectedProject(routedProject);
      router.replace(labUrl(runId, returnTo));
    }
  }, [routedProject, selectedProject, setSelectedProject, router, runId, returnTo]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState("");
  function exitLab(): void {
    router.replace(returnTo || (runId ? researchConversationUrl(runId, project) : "/projects"));
  }
  useEffect(() => {
    if (projectLoading || !project) return;
    let alive = true;
    const refresh = async (): Promise<void> => {
      try {
        const result = await listRuns(project);
        if (alive) { setRuns([...result].reverse()); setError(""); }
      } catch { if (alive) setError("运行记录暂时无法加载，请稍后重试。"); }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [project, projectLoading]);
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-mars-bg">
      <TopBar />
      <main className="flex min-h-0 flex-1 flex-col gap-4 p-4 md:p-6">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">实验台</h1>
            <p className="mt-1 text-xs text-slate-400">训练曲线、验证指标与实验对比。Execution 开始执行后自动展示对应运行。</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-slate-400">
              展示实验
              <select aria-label="展示实验" value={runId}
                onChange={(event) => router.replace(labUrl(event.target.value, returnTo))}
                className="max-w-80 rounded-lg border border-mars-border bg-mars-panel px-3 py-2 text-slate-100">
                <option value="">{projectLabel} · 历史实验</option>
                {runId && !runs.some((run) => run.run_id === runId) ? <option value={runId}>{runId}</option> : null}
                {runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.task} · {run.run_id}</option>)}
              </select>
            </label>
            {runId ? <Link href={`/runs/${runId}?agent=execution`} className="text-xs text-mars-accent hover:underline">查看执行详情 →</Link> : null}
            <button type="button" onClick={exitLab} aria-label="关闭实验台，返回上一界面" title="关闭实验台，返回上一界面" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-2xl leading-none text-slate-400 hover:bg-mars-panel hover:text-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400"><span aria-hidden="true">×</span></button>
          </div>
        </header>
        {error ? <p role="alert" className="text-xs text-amber-200">{error}</p> : null}
        <div className="min-h-0 flex-1">{projectLoading || !project
          ? <p role="status" className="p-8 text-sm text-slate-400">正在加载项目…</p>
          : <TensorBoardPanel key={`${project}-${runId}`} project={project} runId={runId || undefined} fullHeight />}</div>
      </main>
    </div>
  );
}
export default function LabPage(): JSX.Element {
  return <Suspense fallback={<div className="p-8 text-slate-400">正在打开实验台…</div>}><ExperimentLab /></Suspense>;
}
