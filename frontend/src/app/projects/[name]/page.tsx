"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { TopBar } from "@/components/TopBar";
import { useProject } from "@/lib/project";
import {
  createProjectExperiment,
  getProject,
  getProjectExperimentRuns,
  listProjectExperiments,
  type Experiment,
  type ExperimentRun,
  type ProjectSummary,
} from "@/lib/api";

const BUTTON = "rounded-md border border-mars-border bg-mars-panel2 px-4 py-2 text-sm hover:bg-mars-subtle disabled:opacity-50";
const PRIMARY = "rounded-md border border-mars-border bg-mars-accent px-4 py-2 text-sm font-medium text-white hover:brightness-110 disabled:opacity-50";

function RunRows({ runs }: { runs: ExperimentRun[] }): JSX.Element {
  return <ul className="mt-2 space-y-1">
    {runs.map((run) => <li key={run.run_id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-mars-border px-3 py-2 text-sm">
      <Link href={`/runs/${run.run_id}`} className="min-w-0 break-all text-indigo-200 hover:underline">{run.run_id}</Link>
      <span className="shrink-0 text-xs text-slate-500">{run.entrypoint} · {new Date(run.created_at).toLocaleString()}</span>
    </li>)}
  </ul>;
}

function ExperimentCard({ project, experiment }: { project: string; experiment: Experiment }): JSX.Element {
  const [open, setOpen] = useState(false);
  const [runs, setRuns] = useState<ExperimentRun[] | null>(null);
  function toggle(): void {
    const next = !open;
    setOpen(next);
    if (next && runs === null) void getProjectExperimentRuns(project, experiment.id).then(setRuns).catch(() => setRuns([]));
  }
  return <article className="rounded-lg border border-mars-border bg-mars-panel p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <button type="button" onClick={toggle} className="min-w-0 text-left" aria-expanded={open}>
        <h3 className="break-words text-base font-medium">{experiment.name}</h3>
        <p className="mt-1 text-xs text-slate-500">创建于 {new Date(experiment.created_at).toLocaleString()} · {experiment.run_count} 次运行{experiment.latest_run_created_at ? ` · 最近 ${new Date(experiment.latest_run_created_at).toLocaleString()}` : ""}</p>
      </button>
      <div className="flex shrink-0 gap-2">
        <Link href={`/runs/new?project=${encodeURIComponent(project)}&experiment=${encodeURIComponent(experiment.id)}`} className={BUTTON}>进入对话</Link>
        <button type="button" onClick={toggle} className={BUTTON}>{open ? "收起" : "查看运行"}</button>
      </div>
    </div>
    {experiment.description ? <p className="mt-2 text-sm text-slate-400">{experiment.description}</p> : null}
    {open ? (runs === null ? <p role="status" className="mt-3 text-sm text-slate-400">正在读取运行…</p>
      : runs.length === 0 ? <p className="mt-3 text-sm text-slate-500">还没有运行记录；进入对话描述研究目标即可开始。</p>
      : <RunRows runs={runs} />) : null}
  </article>;
}

export default function ProjectDetailPage(): JSX.Element {
  const params = useParams<{ name: string }>();
  const router = useRouter();
  const name = params && typeof params.name === "string" ? params.name : "";
  const { setSelectedProject } = useProject();
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [naming, setNaming] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const [summary, exps] = await Promise.all([getProject(name), listProjectExperiments(name)]);
      setProject(summary); setExperiments(exps); setError("");
    } catch { setError("无法读取项目信息，请确认项目仍已接入。"); }
    finally { setLoading(false); }
  }, [name]);

  useEffect(() => { if (name) { setSelectedProject(name); void refresh(); } }, [name, refresh, setSelectedProject]);

  async function startResearch(): Promise<void> {
    const value = draftName.trim();
    if (!value || creating) return;
    setCreating(true); setCreateError("");
    try {
      const experiment = await createProjectExperiment(name, value);
      router.push(`/runs/new?project=${encodeURIComponent(name)}&experiment=${encodeURIComponent(experiment.id)}`);
    } catch (exc) {
      setCreateError(exc instanceof Error ? exc.message : "创建实验失败，请稍后重试。");
    } finally { setCreating(false); }
  }

  return <div className="min-h-screen bg-mars-bg">
    <TopBar />
    <main className="mx-auto max-w-6xl space-y-6 p-4 md:p-8">
      <nav aria-label="面包屑" className="text-sm text-slate-400"><Link href="/projects" className="hover:text-white">项目</Link><span aria-hidden> / </span><span className="text-slate-300">{name}</span></nav>
      {loading ? <p role="status" className="py-12 text-center text-slate-400">正在读取项目…</p>
        : error ? <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100"><p>{error}</p><button type="button" className={BUTTON} onClick={() => void refresh()}>重试</button></div>
        : project ? <>
          <header className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h1 className="break-words text-2xl font-semibold">{project.display_name || project.name}</h1>
              <div className="flex gap-2">
                <Link href="/runs/new" className={BUTTON}>自由对话研究</Link>
                <button type="button" className={PRIMARY} onClick={() => { setNaming(true); setDraftName(""); setCreateError(""); }}>开始研究</button>
              </div>
            </div>
            {project.description ? <p className="text-sm text-slate-400">{project.description}</p> : null}
            <dl className="space-y-1 text-sm">
              <div className="flex flex-wrap gap-x-2"><dt className="text-slate-500">项目目录</dt><dd className="break-all text-slate-300">{project.folder_path || "—"}</dd></div>
              <div className="flex flex-wrap gap-x-2"><dt className="text-slate-500">当前工作仓</dt><dd className="break-all text-slate-300">{project.repo_path || "尚未绑定"}{project.repo_role === "ainative" ? <span className="ml-2 rounded bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-200">AI Native 可写副本</span> : project.repo_read_only ? <span className="ml-2 rounded bg-mars-panel px-2 py-0.5 text-xs text-slate-400">基线只读</span> : null}</dd></div>
              {project.repo_role === "ainative" && project.baseline_repo_path ? <div className="flex flex-wrap gap-x-2"><dt className="text-slate-500">原始基线（只读）</dt><dd className="break-all text-slate-300">{project.baseline_repo_path}</dd></div> : null}
            </dl>
          </header>
          <section aria-label="研究实验" className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-lg font-medium">实验（{experiments.length}）</h2>
              <p className="text-xs text-slate-500">同一项目的实验共享项目背景资料，无需重复配置。</p>
            </div>
            {naming ? <div className="space-y-2 rounded-lg border border-indigo-400/40 bg-indigo-500/5 p-4">
              <label className="block text-sm">实验名称<div className="mt-2 flex flex-wrap gap-2"><input aria-label="实验名称" autoFocus className="min-w-0 flex-1 rounded border border-mars-border bg-mars-bg px-3 py-2" value={draftName} maxLength={120} placeholder="例如：残差降低 2dB" onChange={(event) => setDraftName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void startResearch(); }} /><button type="button" className={PRIMARY} disabled={creating || !draftName.trim()} onClick={() => void startResearch()}>{creating ? "创建中…" : "创建并进入对话"}</button><button type="button" className={BUTTON} disabled={creating} onClick={() => setNaming(false)}>取消</button></div></label>
              {createError ? <p role="alert" className="text-sm text-amber-200">{createError}</p> : null}
            </div> : null}
            {experiments.length === 0 ? <p className="rounded-lg border border-dashed border-mars-border p-6 text-center text-sm text-slate-400">还没有实验；点击「开始研究」输入名称创建第一个实验。</p>
              : <div className="space-y-3">{experiments.map((experiment) => <ExperimentCard key={experiment.id} project={name} experiment={experiment} />)}</div>}
          </section>
        </> : null}
    </main>
  </div>;
}
