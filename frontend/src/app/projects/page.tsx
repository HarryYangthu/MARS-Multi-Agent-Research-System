"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { TopBar } from "@/components/TopBar";
import { useProject } from "@/lib/project";
import { listRemovedProjects, restoreRemovedProject, type ProjectSummary, type RemovedProject } from "@/lib/api";
import { ProjectRemovalDialog } from "@/components/ProjectRemovalDialog";
import { ProjectConversationActions, useConversationHistory } from "@/components/ConversationHistory";

const FolderProjectDialog = dynamic(() => import("@/components/FolderProjectDialog").then((module) => module.FolderProjectDialog), { ssr: false });

export default function ProjectsPage(): JSX.Element {
  const { projects, selectedProject, loading, error, setSelectedProject, refreshProjects } = useProject();
  const [dialog, setDialog] = useState<{ mode: "open" | "create" | "context" | "configure"; current?: ProjectSummary } | null>(null);
  const history = useConversationHistory();
  const [removing, setRemoving] = useState<ProjectSummary | null>(null);
  const [showRemoved, setShowRemoved] = useState(false);
  const [removed, setRemoved] = useState<RemovedProject[]>([]);
  const [removalRevision, setRemovalRevision] = useState(0);
  const [removalError, setRemovalError] = useState("");
  const [restoreBusy, setRestoreBusy] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!showRemoved) return;
    let cancelled = false;
    void listRemovedProjects().then((rows) => { if (!cancelled) { setRemoved(rows); setRemovalError(""); } })
      .catch((cause: unknown) => { if (!cancelled) setRemovalError(cause instanceof Error ? cause.message : "读取已移除项目失败"); });
    return () => { cancelled = true; };
  }, [showRemoved, removalRevision]);
  async function restore(id: string) {
    setRestoreBusy(id); setRemovalError("");
    try { await restoreRemovedProject(id); await refreshProjects(); setRemovalRevision((n) => n + 1); setNotice("项目已恢复到原位置。"); }
    catch (cause) { setRemovalError(cause instanceof Error ? cause.message : "恢复失败"); }
    finally { setRestoreBusy(""); }
  }
  const button = "rounded-md border border-mars-border bg-mars-panel2 px-4 py-2 text-sm hover:bg-mars-subtle disabled:opacity-50";
  return <div className="min-h-screen bg-mars-bg">
    <TopBar />
    <main className="mx-auto max-w-6xl space-y-6 p-4 md:p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div><h1 className="text-2xl font-semibold">项目</h1><p className="mt-2 text-sm text-slate-400">接入已有代码和资料，为每次研究保留独立记录。</p></div>
        <div className="flex flex-wrap gap-2"><button type="button" onClick={() => setDialog({ mode: "open" })} className={button}>打开已有文件夹</button><button type="button" onClick={() => setDialog({ mode: "create" })} className={button}>新建项目</button></div>
      </header>
      {error ? <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100"><p>{error}</p><button type="button" disabled={loading} onClick={() => void refreshProjects()} className={button}>重新连接</button></div> : null}
      {notice ? <p role="status" className="text-sm text-emerald-200">{notice}</p> : null}
      {loading && projects.length === 0 ? <p role="status" className="py-12 text-center text-slate-400">正在加载项目…</p> : null}
      {!loading && !error && projects.length === 0 ? <section className="rounded-lg border border-dashed border-mars-border p-10 text-center"><h2 className="text-lg font-medium">还没有项目</h2><p className="mt-2 text-sm text-slate-400">选择已有文件夹，或创建一个文件夹保存研究资料。</p></section> : null}
      <section aria-label="项目列表" className="grid gap-4 md:grid-cols-2">
        {projects.map((project) => <article key={project.name} className={`relative flex min-w-0 flex-col rounded-lg border bg-mars-panel p-5 transition hover:border-indigo-400/60 ${project.name === selectedProject ? "border-indigo-400/50" : "border-mars-border"}`}>
          <Link href={`/projects/${encodeURIComponent(project.name)}/research`} aria-label={`查看 ${project.display_name || project.name} 的所有研究`} onClick={() => setSelectedProject(project.name)} className="absolute inset-0 rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400" /><div className="pointer-events-none flex flex-wrap items-center justify-between gap-2"><h2 className="break-words text-lg font-medium">{project.display_name || project.name}</h2>{project.name === selectedProject ? <span className="text-xs text-indigo-200">当前项目</span> : null}</div>
          <p className="mt-2 text-sm leading-6 text-slate-400">{project.description || "尚未填写项目说明"}</p>
          <dl className="my-5 space-y-2 text-sm"><div><dt className="text-slate-500">代码位置</dt><dd className="mt-1 break-all text-slate-300">{project.repo_path || project.folder_path || "尚未绑定"}</dd></div><div><dt className="sr-only">路径状态</dt><dd className={project.repo_exists ? "text-emerald-200" : "text-amber-200"}>{project.repo_exists ? "代码路径可访问" : "代码路径不可访问，请检查项目接入"}</dd></div></dl>
          <div className="relative z-10 mt-auto flex flex-wrap gap-2"><button type="button" onClick={() => { setSelectedProject(project.name); setDialog({ mode: "configure", current: project }); }} className={button}>项目配置</button><Link href={`/runs/new?project=${encodeURIComponent(project.name)}&new=1`} onClick={() => setSelectedProject(project.name)} className={`${button} border-indigo-400/40 text-indigo-100`}>启动研究</Link><ProjectConversationActions project={project.name} rows={history.rows} loading={history.loading} error={history.error} buttonClass={button} onSelect={() => setSelectedProject(project.name)} /><button type="button" onClick={() => { setSelectedProject(project.name); setDialog({ mode: "context", current: project }); }} className={button}>项目资料</button></div>
          {project.project_type === "folder" ? <button type="button" className="relative z-10 mt-4 min-h-11 self-start rounded px-3 py-2 text-sm text-slate-400 hover:bg-mars-subtle hover:text-red-200" onClick={() => setRemoving(project)}>移除项目</button> : null}
        </article>)}
      </section>
      <p className="text-xs leading-5 text-slate-500">路径可访问仅代表项目已接入。模型连接、数据、运行入口和修改权限将在任务启动前另行检查。</p>
      <button type="button" className={button} aria-expanded={showRemoved} onClick={() => setShowRemoved((value) => !value)}>已移除项目与恢复</button>
      {showRemoved ? <section aria-label="已移除项目" className="space-y-3 rounded-lg border border-mars-border p-4">
        <p className="text-sm text-slate-400">保留文件的项目可重新打开；回收的项目会恢复到原路径，已有文件夹不会被覆盖。</p>
        {removalError ? <p role="alert" className="text-sm text-amber-200">{removalError}</p> : null}
        {removed.length === 0 && !removalError ? <p className="text-sm text-slate-400">没有已移除项目。</p> : null}
        {removed.map((item) => <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 border-t border-mars-border py-3"><div className="min-w-0 text-sm"><p>{item.display_name} · {item.mode === "detach" ? "文件保留在原位置" : "文件已回收"}</p><p className="mt-1 break-all text-xs text-slate-400">{item.stored_path}</p></div><button type="button" disabled={Boolean(restoreBusy)} className={button} onClick={() => void restore(item.id)}>{restoreBusy === item.id ? "恢复中…" : "恢复项目"}</button></div>)}
      </section> : null}
      {removing ? <ProjectRemovalDialog project={removing} onClose={() => setRemoving(null)} onRemoved={(result) => { setRemoving(null); setNotice(result.mode === "detach" ? "项目已从列表移除，原文件保留。" : `项目文件已回收至 ${result.stored_path}`); setShowRemoved(true); setRemovalRevision((n) => n + 1); void refreshProjects(); }} /> : null}
      {dialog ? <FolderProjectDialog mode={dialog.mode} current={dialog.current} onClose={() => setDialog(null)} onOpened={async (project) => { await refreshProjects(); setSelectedProject(project.name); }} /> : null}
    </main>
  </div>;
}
