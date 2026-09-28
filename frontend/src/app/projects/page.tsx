"use client";

import { useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { TopBar } from "@/components/TopBar";
import { useProject } from "@/lib/project";
import type { ProjectSummary } from "@/lib/api";

const FolderProjectDialog = dynamic(() => import("@/components/FolderProjectDialog").then((module) => module.FolderProjectDialog), { ssr: false });

export default function ProjectsPage(): JSX.Element {
  const { projects, selectedProject, loading, error, setSelectedProject, refreshProjects } = useProject();
  const [dialog, setDialog] = useState<{ mode: "open" | "create" | "context" | "configure"; current?: ProjectSummary } | null>(null);
  const button = "rounded-md border border-mars-border bg-mars-panel2 px-4 py-2 text-sm hover:bg-mars-subtle disabled:opacity-50";
  return <div className="min-h-screen bg-mars-bg">
    <TopBar />
    <main className="mx-auto max-w-6xl space-y-6 p-4 md:p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div><h1 className="text-2xl font-semibold">项目</h1><p className="mt-2 text-sm text-slate-400">接入已有代码和资料，为每次研究保留独立记录。</p></div>
        <div className="flex flex-wrap gap-2"><button type="button" onClick={() => setDialog({ mode: "open" })} className={button}>打开已有文件夹</button><button type="button" onClick={() => setDialog({ mode: "create" })} className={button}>新建项目</button></div>
      </header>
      {error ? <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100"><p>{error}</p><button type="button" disabled={loading} onClick={() => void refreshProjects()} className={button}>重新连接</button></div> : null}
      {loading && projects.length === 0 ? <p role="status" className="py-12 text-center text-slate-400">正在加载项目…</p> : null}
      {!loading && !error && projects.length === 0 ? <section className="rounded-lg border border-dashed border-mars-border p-10 text-center"><h2 className="text-lg font-medium">还没有项目</h2><p className="mt-2 text-sm text-slate-400">选择已有文件夹，或创建一个文件夹保存研究资料。</p></section> : null}
      <section aria-label="项目列表" className="grid gap-4 md:grid-cols-2">
        {projects.map((project) => <article key={project.name} className={`flex min-w-0 flex-col rounded-lg border bg-mars-panel p-5 ${project.name === selectedProject ? "border-indigo-400/50" : "border-mars-border"}`}>
          <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="break-words text-lg font-medium">{project.display_name || project.name}</h2>{project.name === selectedProject ? <span className="text-xs text-indigo-200">当前项目</span> : null}</div>
          <p className="mt-2 text-sm leading-6 text-slate-400">{project.description || "尚未填写项目说明"}</p>
          <dl className="my-5 space-y-2 text-sm"><div><dt className="text-slate-500">代码位置</dt><dd className="mt-1 break-all text-slate-300">{project.repo_path || project.folder_path || "尚未绑定"}</dd></div><div><dt className="sr-only">路径状态</dt><dd className={project.repo_exists ? "text-emerald-200" : "text-amber-200"}>{project.repo_exists ? "代码路径可访问" : "代码路径不可访问，请检查项目接入"}</dd></div></dl>
          <div className="mt-auto flex flex-wrap gap-2"><button type="button" onClick={() => { setSelectedProject(project.name); setDialog({ mode: "configure", current: project }); }} className={`${button} border-indigo-400/40 text-indigo-100`}>项目配置</button><Link href="/runs" onClick={() => setSelectedProject(project.name)} className={button}>研究任务</Link><Link href="/results" onClick={() => setSelectedProject(project.name)} className={button}>查看结果</Link><button type="button" onClick={() => { setSelectedProject(project.name); setDialog({ mode: "context", current: project }); }} className={button}>项目资料</button></div>
        </article>)}
      </section>
      <p className="text-xs leading-5 text-slate-500">路径可访问仅代表项目已接入。模型连接、数据、运行入口和修改权限将在任务启动前另行检查。</p>
      {dialog ? <FolderProjectDialog mode={dialog.mode} current={dialog.current} onClose={() => setDialog(null)} onOpened={async (project) => { await refreshProjects(); setSelectedProject(project.name); }} /> : null}
    </main>
  </div>;
}
