"use client";

import { useProject } from "@/lib/project";

export function ProjectSwitcher(): JSX.Element {
  const { selectedProject, projects, loading, setSelectedProject } = useProject();
  const current = projects.find((project) => project.name === selectedProject);
  return (
    <div className="flex items-center gap-1.5 rounded border border-mars-border bg-mars-panel2 px-2 py-1">
      <span className="text-[10px] text-slate-500">项目</span>
      {projects.length > 0 ? (
        <select
          value={selectedProject}
          onChange={(event) => setSelectedProject(event.target.value)}
          className="max-w-40 bg-transparent font-mono text-xs text-slate-200 outline-none"
          title={current?.description || selectedProject}
        >
          {projects.map((project) => (
            <option key={project.name} value={project.name} className="bg-mars-panel">
              {project.name}
            </option>
          ))}
        </select>
      ) : (
        <span className="font-mono text-xs text-slate-300">
          {loading ? "加载中" : selectedProject}
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
    </div>
  );
}
