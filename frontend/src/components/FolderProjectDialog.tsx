"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { browseProjectFolders, openProjectFolder, type ProjectFolders, type ProjectSummary } from "@/lib/api";
import { ProjectContextFiles } from "./ProjectContextFiles";

export function FolderProjectDialog({ mode, current, onClose, onOpened }: {
  mode: "create" | "open" | "context";
  current?: ProjectSummary;
  onClose: () => void;
  onOpened: (project: ProjectSummary) => Promise<void>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const router = useRouter();
  const [path, setPath] = useState("");
  const [folderName, setFolderName] = useState("");
  const [folders, setFolders] = useState<ProjectFolders | null>(null);
  const [opened, setOpened] = useState<ProjectSummary | null>(mode === "context" ? current ?? null : null);
  const [busy, setBusy] = useState(mode !== "context");
  const [error, setError] = useState("");
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => dialog?.close(); }, []);
  useEffect(() => {
    if (mode === "context") return;
    const controller = new AbortController();
    void browseProjectFolders("", controller.signal).then((data) => { if (!controller.signal.aborted) { setFolders(data); setPath(data.path); } }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(String(cause)); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [mode]);
  async function browse(next: string) {
    setBusy(true); setError("");
    try { const data = await browseProjectFolders(next); setFolders(data); setPath(data.path); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "无法打开文件夹"); }
    finally { setBusy(false); }
  }
  async function submit() {
    if (mode === "create" && (!folderName.trim() || /[\\/]/.test(folderName) || [".", ".."].includes(folderName.trim()))) { setError("请输入有效的新文件夹名称"); return; }
    setBusy(true); setError("");
    try {
      const selected = mode === "create" ? `${path.replace(/[\\/]+$/, "")}/${folderName.trim()}` : path;
      const project = await openProjectFolder(selected, mode === "create");
      await onOpened(project); setOpened(project);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "项目打开失败"); }
    finally { setBusy(false); }
  }
  const button = "rounded border border-mars-border px-3 py-2 text-xs text-slate-200 hover:bg-mars-panel2 disabled:opacity-40";
  return <dialog ref={ref} aria-labelledby="folder-project-title" onCancel={onClose} className="w-[min(880px,95vw)] max-h-[85vh] overflow-y-auto rounded-xl border border-mars-border bg-mars-bg p-6 text-slate-200 shadow-2xl backdrop:bg-black/60">
    <div className="flex items-center justify-between gap-3"><h2 id="folder-project-title" className="text-lg font-semibold">{opened ? opened.display_name || opened.name : mode === "create" ? "新建项目" : "打开文件夹"}</h2><button type="button" onClick={onClose} className={button}>关闭</button></div>
    {opened ? <div className="mt-4 space-y-4"><p className="break-all text-xs text-slate-400">{opened.folder_path || opened.repo_path}</p><ProjectContextFiles project={opened.name} /><button type="button" className={`${button} bg-mars-accent/30`} onClick={() => { onClose(); router.push("/runs/new?entrypoint=idea"); }}>新建研究任务</button></div> : <div className="mt-4 space-y-4">
      <p className="text-sm leading-relaxed text-slate-400">{mode === "create" ? "选择保存位置并创建文件夹，即可建立一个新项目。" : "选择已有文件夹。首次打开时自动识别为项目，再次打开会回到同一个项目。"}</p>
      <label className="block text-xs text-slate-300">{mode === "create" ? "保存位置" : "项目文件夹"}<div className="mt-2 flex gap-2"><input aria-label="文件夹路径" disabled={busy} value={path} onChange={(event) => setPath(event.target.value)} className="min-w-0 flex-1 rounded border border-mars-border bg-mars-panel px-3 py-2 text-sm" placeholder="输入本机文件夹的完整路径" /><button type="button" disabled={busy} onClick={() => void browse(path)} className={button}>浏览</button></div></label>
      <div className="rounded border border-mars-border p-3"><div className="flex items-center justify-between gap-2"><p className="break-all text-xs text-slate-500">{folders?.path}</p><button type="button" className={`${button} shrink-0`} disabled={busy || !folders || folders.path === folders.parent} onClick={() => folders && void browse(folders.parent)}>上一级</button></div><div className="mt-2 grid max-h-52 gap-1 overflow-auto sm:grid-cols-2">{folders?.directories.map((folder) => <button type="button" key={folder.path} disabled={busy} className="truncate rounded px-2 py-2 text-left text-sm hover:bg-mars-panel2" onClick={() => void browse(folder.path)}>📁 {folder.name}</button>)}</div>{folders?.has_more ? <p className="mt-2 text-xs text-slate-500">只显示前 300 个子文件夹，也可以直接输入路径。</p> : null}</div>
      {mode === "create" ? <label className="block text-xs text-slate-300">项目名称<input aria-label="项目名称" disabled={busy} value={folderName} onChange={(event) => setFolderName(event.target.value)} className="mt-2 block w-full rounded border border-mars-border bg-mars-panel px-3 py-2 text-sm" placeholder="同时用作新文件夹名称" /></label> : null}
      <p className="text-xs leading-relaxed text-slate-500">自动加载 AGENTS.md、README.md 和 context/ 中的 Markdown。代码仓绑定到选中的文件夹。</p>
      <button type="button" disabled={busy || !path} onClick={() => void submit()} className={`${button} bg-mars-accent/30`}>{busy ? "处理中…" : mode === "create" ? "创建项目文件夹" : "打开此文件夹"}</button>
    </div>}
    {error ? <p role="alert" className="mt-4 text-sm text-rose-300">{error}</p> : null}
  </dialog>;
}
