"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useProject } from "@/lib/project";
import type { ProjectSummary } from "@/lib/api";
const FolderProjectDialog = dynamic(() => import("./FolderProjectDialog").then((m) => m.FolderProjectDialog), { ssr: false });

export function ProjectSwitcher({ compact = false }: { compact?: boolean }): JSX.Element {
  const { selectedProject, projects, loading, setSelectedProject, refreshProjects } = useProject();
  const [mode, setMode] = useState<"create" | "open" | "context" | null>(null);
  const current = projects.find((project) => project.name === selectedProject);
  // Same display names (for example two folders both called "test") are
  // indistinguishable in a flat list; suffix the folder path for duplicates.
  const labelCounts = new Map<string, number>();
  for (const project of projects) {
    const label = project.display_name || project.name;
    labelCounts.set(label, (labelCounts.get(label) ?? 0) + 1);
  }
  const optionLabel = (project: Pick<ProjectSummary, "display_name" | "name" | "folder_path">): string => {
    const label = project.display_name || project.name;
    if ((labelCounts.get(label) ?? 0) <= 1 || !project.folder_path) return label;
    const tail = project.folder_path.split(/[\\/]/).filter(Boolean).slice(-2).join("/");
    return `${label}（${tail}）`;
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded border border-mars-border bg-mars-panel2 px-2 py-1">
      <span className="text-[10px] text-slate-500">项目</span>
      {projects.length > 0 ? (
        <select
          aria-label="当前项目"
          value={selectedProject}
          onChange={(event) => setSelectedProject(event.target.value)}
          className="max-w-40 rounded bg-transparent py-1 text-xs text-slate-200"
          title={current?.folder_path || current?.description || selectedProject}
        >
          {projects.map((project) => (
            <option key={project.name} value={project.name} className="bg-mars-panel">
              {optionLabel(project)}
            </option>
          ))}
        </select>
      ) : (
        <span className="font-mono text-xs text-slate-300">
          {loading ? "加载中" : selectedProject || "尚未选择"}
        </span>
      )}
      {current ? (
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            current.repo_exists ? "bg-emerald-400" : "bg-amber-400"
          }`}
          title={current.repo_exists ? current.repo_path : "代码仓链接缺失"}
        />
      ) : null}
      {!compact ? <>
        <button type="button" className="px-1 text-xs text-cyan-200 hover:underline" onClick={() => setMode("create")}>新建项目</button>
        <button type="button" className="px-1 text-xs text-slate-300 hover:underline" onClick={() => setMode("open")}>打开文件夹</button>
        {current ? <button type="button" className="px-1 text-xs text-slate-300 hover:underline" onClick={() => setMode("context")}>项目资料</button> : null}
      </> : null}
      {mode ? <FolderProjectDialog mode={mode} current={current} onClose={() => setMode(null)} onOpened={async (project) => {
        await refreshProjects();
        setSelectedProject(project.name);
      }} /> : null}
    </div>
  );
}
