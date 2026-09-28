"use client";

import { useEffect, useState } from "react";
import { browseProjectFolders, importProjectCodeFolder, type ProjectFolders, type ProjectSummary } from "@/lib/api";
import { useProject } from "@/lib/project";

const BUTTON = "rounded border border-mars-border px-3 py-2 text-sm hover:bg-mars-panel2 disabled:opacity-40";

export function ProjectCodeFolder({ project, onBusy }: { project: ProjectSummary; onBusy: (busy: boolean) => void }): JSX.Element {
  const { refreshProjects } = useProject();
  const [codePath, setCodePath] = useState(project.repo_path);
  const [readOnly, setReadOnly] = useState(!!project.repo_read_only);
  const [path, setPath] = useState(project.repo_path || project.folder_path || "");
  const [folders, setFolders] = useState<ProjectFolders | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { onBusy(busy); }, [busy, onBusy]);

  async function browse(next: string): Promise<void> {
    setBusy(true); setError("");
    try { const value = await browseProjectFolders(next); setFolders(value); setPath(value.path); }
    catch { setError("无法读取该文件夹，请检查路径与访问权限。"); }
    finally { setBusy(false); }
  }
  async function save(): Promise<void> {
    setBusy(true); setError(""); setMessage("");
    try {
      const value = await importProjectCodeFolder(project.name, path.trim());
      setCodePath(value.repo_path); setReadOnly(!!value.repo_read_only); setEditing(false);
      setMessage("基线代码仓已关联");
      await refreshProjects();
    } catch { setError("未能确认代码关联，请检查文件夹路径；也可重新打开项目配置核对。"); }
    finally { setBusy(false); }
  }

  return <section aria-label="研究仿真基线代码仓" className="space-y-3 rounded-lg border border-mars-border p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-2"><h4 className="text-sm font-semibold">基线代码仓</h4><span className={`rounded px-2 py-0.5 text-xs ${readOnly ? "bg-emerald-500/10 text-emerald-200" : "bg-mars-panel text-slate-400"}`}>{readOnly ? "只读" : "未启用保护"}</span></div>
      {!editing ? <button type="button" className={BUTTON} onClick={() => { setEditing(true); setMessage(""); void browse(path); }}>选择文件夹</button> : null}
    </div>
    <p className="break-all text-xs text-slate-300">{codePath || "未关联"}</p>
    {editing ? <div className="space-y-3">
      <label className="block text-sm">仿真基线仓库文件夹<div className="mt-2 flex gap-2"><input aria-label="仿真基线仓库路径" className="min-w-0 flex-1 rounded border border-mars-border bg-mars-bg px-3 py-2" value={path} disabled={busy} onChange={(event) => setPath(event.target.value)} /><button type="button" className={BUTTON} disabled={busy} onClick={() => void browse(path)}>浏览</button></div></label>
      {folders ? <div className="rounded border border-mars-border p-3"><div className="flex items-center justify-between gap-2"><p className="break-all text-xs text-slate-400">{folders.path}</p><button type="button" className={`${BUTTON} shrink-0`} disabled={busy || folders.path === folders.parent} onClick={() => void browse(folders.parent)}>上一级</button></div><div className="mt-2 grid max-h-40 gap-1 overflow-auto sm:grid-cols-2">{folders.directories.map((folder) => <button type="button" key={folder.path} disabled={busy} className="truncate rounded p-2 text-left text-sm hover:bg-mars-panel2" onClick={() => void browse(folder.path)}>📁 {folder.name}</button>)}</div>{folders.has_more ? <p className="text-xs text-slate-400">已显示前 300 项</p> : null}</div> : null}
      <div className="flex flex-wrap gap-2"><button type="button" className={`${BUTTON} bg-mars-accent`} disabled={busy || !path.trim()} onClick={() => void save()}>{busy ? "处理中…" : "确认为仿真基线仓库"}</button><button type="button" className={BUTTON} disabled={busy} onClick={() => setEditing(false)}>取消</button></div>
    </div> : null}
    {message ? <p role="status" className="text-sm text-emerald-200">{message}</p> : null}
    {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
  </section>;
}
