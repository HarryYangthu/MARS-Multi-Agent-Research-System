"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { TopBar } from "@/components/TopBar";
import { OnboardingModelSetup } from "@/components/OnboardingModelSetup";
import { ProjectContextFiles } from "@/components/ProjectContextFiles";
import { getProjectAutoContext, uploadProjectBackground, type ProjectAutoContext } from "@/lib/api";
import { useProject } from "@/lib/project";

const FolderProjectDialog = dynamic(() => import("./FolderProjectDialog").then((module) => module.FolderProjectDialog), { ssr: false });
const STEPS = ["连接模型 API", "选择项目代码", "添加背景资料", "创建研究任务"];
const BUTTON = "rounded-lg border border-mars-border px-4 py-2 text-sm hover:bg-mars-panel2 focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:opacity-40";

export function GettingStarted(): JSX.Element {
  const { projects, selectedProject, setSelectedProject, refreshProjects, error: projectError } = useProject();
  const [step, setStep] = useState(0);
  const [apiSaved, setApiSaved] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [context, setContext] = useState<ProjectAutoContext | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const activeProject = useRef(selectedProject);
  const project = projects.find((item) => item.name === selectedProject);
  const accessible = !!project?.repo_exists;
  useEffect(() => {
    activeProject.current = selectedProject;
    const controller = new AbortController();
    setContext(null); setError("");
    if (selectedProject) void getProjectAutoContext(selectedProject, controller.signal).then((value) => {
      if (!controller.signal.aborted) setContext(value);
    }).catch(() => { if (!controller.signal.aborted) setError("项目资料暂时无法读取，请重新扫描或检查代码位置。"); });
    return () => controller.abort();
  }, [selectedProject, revision]);
  async function upload(file: File): Promise<void> {
    if (busy || !project) return;
    if (!file.name.toLowerCase().endsWith(".md")) { setError("请选择 Markdown（.md）文件。"); return; }
    const target = project.name;
    setBusy(true); setError(""); setMessage("");
    try {
      const saved = await uploadProjectBackground(target, file);
      const next = await getProjectAutoContext(target);
      if (activeProject.current !== target) return;
      setContext(next); setRevision((value) => value + 1);
      setMessage(`已上传 ${saved.path}，可在下方预览。`);
    } catch (cause: unknown) { if (activeProject.current === target) setError(cause instanceof Error ? cause.message : "上传未能确认，请重新扫描资料列表后再试。"); }
    finally { setBusy(false); }
  }
  const knownContext = context?.project === selectedProject ? context : null;
  const hasBackground = !!knownContext?.files.some((file) => file.role === "reference");
  const states = [apiSaved ? "配置已保存" : "待配置", accessible ? "代码路径可访问" : "待选择", hasBackground ? `${knownContext?.files.length} 份文档可读取` : "可稍后补充", "目标与运行条件待填写"];
  return <div className="min-h-screen bg-mars-bg"><TopBar /><main className="mx-auto max-w-5xl space-y-6 px-4 py-6 md:p-8">
    <header><Link href="/projects" className="text-sm text-slate-400 hover:text-white">← 返回项目</Link><p className="mt-6 text-xs font-medium tracking-widest text-indigo-200">开始你的第一次研究</p><h1 className="mt-2 text-3xl font-semibold">新手使用引导</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">把模型、代码和背景资料准备好，再描述你希望解决的问题。每一步都可以返回修改；已经保存的配置和资料会保留。</p></header>
    <nav aria-label="新手引导步骤" className="grid grid-cols-2 gap-2 lg:grid-cols-4">{STEPS.map((title, index) => <button key={title} type="button" disabled={busy} aria-current={step === index ? "step" : undefined} onClick={() => setStep(index)} className={`rounded-xl border p-4 text-left focus-visible:ring-2 focus-visible:ring-indigo-300 ${step === index ? "border-indigo-400 bg-indigo-500/10" : "border-mars-border bg-mars-panel"}`}><span className="text-xs text-slate-400">0{index + 1}</span><span className="mt-2 block text-sm font-medium">{title}</span><span className="mt-2 block text-xs leading-5 text-slate-400">{states[index]}</span></button>)}</nav>
    <section aria-label={STEPS[step]} className="space-y-5 rounded-xl border border-mars-border bg-mars-panel p-5 sm:p-7">
      <div><span className="text-xs text-indigo-200">第 {step + 1} 步 / 4</span><h2 className="mt-2 text-xl font-semibold">{STEPS[step]}</h2></div>
      <div hidden={step !== 0}><OnboardingModelSetup onStatus={setApiSaved} /></div>
      {step === 1 ? <div className="space-y-4"><p className="text-sm leading-6 text-slate-400">选择你要研究的代码仓库根目录，例如包含 README、训练脚本和依赖文件的文件夹。MARS 会在其中创建 .mars 项目记录，代码仍保留在原处。</p><button type="button" className={`${BUTTON} bg-mars-accent`} onClick={() => setDialog(true)}>选择代码文件夹</button><label className="block text-sm">或继续使用已接入项目<select disabled={busy} value={selectedProject} onChange={(event) => setSelectedProject(event.target.value)} className="mt-2 block w-full rounded-lg border border-mars-border bg-mars-bg p-3"><option value="">选择一个项目</option>{projects.map((item) => <option key={item.name} value={item.name}>{item.display_name || item.name}</option>)}</select></label>{project ? <div className="rounded-lg border border-mars-border p-4"><p className="font-medium">{project.display_name || project.name}</p><p className="mt-2 break-all text-xs text-slate-400">{project.repo_path || project.folder_path || "尚无代码路径"}</p><p className={`mt-2 text-sm ${accessible ? "text-emerald-200" : "text-amber-200"}`}>{states[1]}</p></div> : null}{projectError ? <p role="alert" className="text-amber-200">{projectError}</p> : null}<button type="button" className="text-sm text-indigo-200 underline" onClick={() => void refreshProjects()}>重新检查项目列表</button></div> : null}
      {step === 2 ? <div className="space-y-4"><p className="text-sm leading-6 text-slate-400">提供研究背景、已有方法、目标指标和不能改动的部分。README.md 会自动读取，也可上传你整理好的 Markdown。上传内容作为背景参考，不会替代你的操作指令。</p>{accessible && project?.project_type === "folder" ? <label className="block rounded-lg border border-dashed border-indigo-400/40 bg-indigo-500/5 p-5 text-sm">上传背景 Markdown<input aria-label="上传背景 Markdown" type="file" accept=".md,text/markdown" disabled={busy} className="mt-3 block w-full text-sm file:mr-4 file:rounded file:border-0 file:bg-indigo-500/20 file:px-3 file:py-2 file:text-indigo-100" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file); }} /><span className="mt-3 block text-xs leading-5 text-slate-400">保存到当前项目的 context/ 文件夹。同名文件不会覆盖；每次上传一份，也可以暂时跳过。</span></label> : <p className="text-sm text-amber-200">请先在第 2 步选择代码文件夹，再向该项目上传资料。</p>}{busy ? <p role="status">正在上传并核对资料…</p> : null}{error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}{message ? <p role="status" className="text-sm text-emerald-200">{message}</p> : null}{selectedProject ? <ProjectContextFiles key={`${selectedProject}:${revision}`} project={selectedProject} /> : null}</div> : null}
      {step === 3 ? <div className="space-y-4"><p className="text-sm leading-6 text-slate-400">接下来填写研究目标、运行命令、指标和预算。所选项目的代码位置与可读取的背景文件会带入新任务，你可以逐项核对。</p><dl className="space-y-3 rounded-lg border border-mars-border p-4 text-sm"><div><dt className="text-slate-400">模型</dt><dd className="mt-1">{states[0]}</dd></div><div><dt className="text-slate-400">当前项目</dt><dd className="mt-1">{project?.display_name || project?.name || "尚未选择"}</dd></div><div><dt className="text-slate-400">背景资料</dt><dd className="mt-1">{states[2]}</dd></div></dl><p className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm leading-6 text-amber-100">当前版本可以配置和保存研究任务，完整研究启动仍未开放。完成引导不代表模型连接或研究执行已验收。</p>{accessible ? <Link href={`/runs/new?project=${encodeURIComponent(selectedProject)}`} className={`${BUTTON} inline-block bg-mars-accent`}>填写研究目标</Link> : <button type="button" className={BUTTON} onClick={() => setStep(1)}>先选择代码文件夹</button>}</div> : null}
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-mars-border pt-5"><button type="button" disabled={step === 0 || busy} className={BUTTON} onClick={() => setStep((value) => value - 1)}>上一步</button>{step < 3 ? <button type="button" disabled={busy} className={`${BUTTON} bg-mars-accent`} onClick={() => setStep((value) => value + 1)}>{step === 2 && !hasBackground ? "暂时跳过，继续" : "下一步"}</button> : <Link href="/projects" className={BUTTON}>回到项目</Link>}</footer>
    </section>
    {dialog ? <FolderProjectDialog mode="open" onClose={() => setDialog(false)} onOpened={async (opened) => { await refreshProjects(); setSelectedProject(opened.name); setDialog(false); }} /> : null}
  </main></div>;
}
