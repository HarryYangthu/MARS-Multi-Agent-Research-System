"use client";

import { Suspense, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { TopBar } from "@/components/TopBar";
import { NewResearchWizard } from "@/components/NewResearchWizard";
import { ResearchConversation } from "@/components/ResearchConversation";
import { useProject } from "@/lib/project";

const LegacyResearchForm = dynamic(() => import("@/components/LegacyResearchForm"), {
  loading: () => <p role="status" className="p-8 text-sm text-slate-400">正在加载旧项目表单…</p>,
});

export default function NewResearchPage(): JSX.Element {
  return <Suspense fallback={<p role="status" className="p-8 text-slate-400">正在打开研究对话…</p>}><NewResearchPageInner /></Suspense>;
}
function NewResearchPageInner(): JSX.Element {
  const params = useSearchParams();
  const { selectedProject, projects, loading, error, setSelectedProject } = useProject();
  const requestedProject = params?.get("project") ?? "";
  const requestedRun = params?.get("run") ?? "";
  const [applied, setApplied] = useState("");
  const target = `${requestedProject}:${requestedRun}`;
  useEffect(() => {
    if (!loading && requestedProject && applied !== target && projects.some(item => item.name === requestedProject)) {
      setApplied(target);
      setSelectedProject(requestedProject);
    }
  }, [loading, requestedProject, target, projects, setSelectedProject, applied]);
  const project = projects.find((item) => item.name === selectedProject);
  const incoming = !requestedProject || requestedProject === project?.name;
  const experimentId = incoming ? params?.get("experiment") ?? undefined : undefined;
  // Keep explicit contract/stage forms available through advanced links.
  if (params?.get("mode") === "legacy" || params?.has("entrypoint")) return <LegacyResearchForm />;
  if (params?.get("mode") !== "contract") return <div className="flex h-dvh min-h-0 flex-col bg-mars-bg"><TopBar />
    <main className="flex min-h-0 flex-1 flex-col">
      {loading ? <p role="status" className="p-8 text-sm text-slate-400">正在读取项目…</p> : error ? <p role="alert" className="p-8 text-sm text-amber-200">{error}</p> : requestedProject && !projects.some(item => item.name === requestedProject) ? <p role="alert" className="p-8 text-sm text-amber-200">找不到该研究任务所属的项目。</p> : requestedProject && applied !== target ? <p role="status" className="p-8 text-sm text-slate-400">正在打开研究对话…</p> : project ? <ResearchConversation key={`${project.name}:${experimentId ?? ""}:${incoming ? requestedRun : ""}`} project={project.name} name={project.display_name || project.name} experimentId={experimentId} initialRunId={incoming ? requestedRun || undefined : undefined} /> : <div className="m-auto space-y-4 text-center"><p>先选择一个项目，再开始研究。</p><Link href="/projects" className="inline-block rounded-lg bg-mars-accent px-4 py-2 text-sm">选择项目</Link></div>}
    </main>
  </div>;
  return <div className="grid h-screen grid-rows-[auto_1fr] bg-mars-bg"><TopBar />
    <main className="mx-auto w-full max-w-5xl overflow-y-auto px-4 py-6 sm:px-6 sm:py-8">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-semibold text-slate-100">新建研究</h1>
        <p className="mt-2 text-sm text-slate-400">连接已有项目，明确目标、命令与有限预算，保存一份可核对的研究计划。</p></div>
        <Link href="/runs" className="rounded-md border border-mars-border px-3 py-2 text-sm text-slate-300 hover:bg-mars-panel2">研究任务</Link></header>
      <NewResearchWizard />
    </main>
  </div>;
}
