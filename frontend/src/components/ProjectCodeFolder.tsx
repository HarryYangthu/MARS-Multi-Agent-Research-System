"use client";

import { useEffect, useState } from "react";
import { browseProjectFolders, importProjectCodeFolder, type ProjectFolders, type ProjectSummary } from "@/lib/api";
import { useProject } from "@/lib/project";

const BUTTON = "rounded border border-mars-border px-3 py-2 text-sm hover:bg-mars-panel2 disabled:opacity-40";

export function ProjectCodeFolder({ project, onBusy }: { project: ProjectSummary; onBusy: (busy: boolean) => void }): JSX.Element {
  const { refreshProjects } = useProject();
  const [codePath, setCodePath] = useState(project.repo_path);
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
      setCodePath(value.repo_path); setEditing(false);
      setMessage("代码工程已关联，源文件保留在原位置。");
      await refreshProjects();
    } catch { setError("未能确认代码关联，请检查文件夹路径；也可重新打开项目配置核对。"); }
    finally { setBusy(false); }
  }

  return <section aria-label="代码工程" className="space-y-3 rounded-lg border border-mars-border p-4">
    <h4 className="text-sm font-semibold">代码工程</h4>
    <p className="text-xs leading-5 text-slate-400">选择包含源码、依赖文件和运行脚本的工程根目录。导入只关联路径，不复制或移动源代码；Agent 会在研究时按需读取。</p>
    <p className="break-all text-xs text-slate-300">当前代码目录：{codePath || "尚未关联"}</p>
    {!editing ? <button type="button" className={BUTTON} onClick={() => { setEditing(true); setMessage(""); void browse(path); }}>导入代码工程文件夹</button> : <div className="space-y-3">
      <label className="block text-sm">代码工程文件夹<div className="mt-2 flex gap-2"><input aria-label="代码工程文件夹路径" className="min-w-0 flex-1 rounded border border-mars-border bg-mars-bg px-3 py-2" value={path} disabled={busy} onChange={(event) => setPath(event.target.value)} /><button type="button" className={BUTTON} disabled={busy} onClick={() => void browse(path)}>浏览</button></div></label>
      {folders ? <div className="rounded border border-mars-border p-3"><div className="flex items-center justify-between gap-2"><p className="break-all text-xs text-slate-400">{folders.path}</p><button type="button" className={`${BUTTON} shrink-0`} disabled={busy || folders.path === folders.parent} onClick={() => void browse(folders.parent)}>上一级</button></div><div className="mt-2 grid max-h-40 gap-1 overflow-auto sm:grid-cols-2">{folders.directories.map((folder) => <button type="button" key={folder.path} disabled={busy} className="truncate rounded p-2 text-left text-sm hover:bg-mars-panel2" onClick={() => void browse(folder.path)}>📁 {folder.name}</button>)}</div>{folders.has_more ? <p className="text-xs text-slate-400">仅显示前 300 个文件夹，也可直接输入完整路径。</p> : null}</div> : null}
      <div className="flex flex-wrap gap-2"><button type="button" className={`${BUTTON} bg-mars-accent`} disabled={busy || !path.trim()} onClick={() => void save()}>{busy ? "处理中…" : "确认导入此文件夹"}</button><button type="button" className={BUTTON} disabled={busy} onClick={() => setEditing(false)}>取消</button></div>
    </div>}
    {message ? <p role="status" className="text-sm text-emerald-200">{message}</p> : null}
    {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
  </section>;
}
