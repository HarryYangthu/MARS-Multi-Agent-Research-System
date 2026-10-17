"use client";

import { useEffect, useRef, useState } from "react";
import { previewProjectRemoval, removeProject, type ProjectRemoval, type ProjectSummary, type RemovedProject } from "@/lib/api";

export function ProjectRemovalDialog({ project, onClose, onRemoved }: { project: ProjectSummary; onClose: () => void; onRemoved: (result: RemovedProject) => void }): JSX.Element {
  const ref = useRef<HTMLDialogElement>(null);
  const [preview, setPreview] = useState<ProjectRemoval | null>(null);
  const [mode, setMode] = useState<"detach" | "recycle">("detach");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => dialog?.close(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    void previewProjectRemoval(project.name, controller.signal).then(setPreview).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "读取项目状态失败");
    });
    return () => controller.abort();
  }, [project.name]);
  const blockers = preview ? mode === "recycle" ? preview.recycle_blockers : preview.blockers : [];
  async function submit() {
    if (!preview) return;
    setBusy(true); setError("");
    try { onRemoved(await removeProject(project.name, { mode, expected_path: preview.path, confirmation })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "移除失败，文件可能被占用，请停止任务后重试"); }
    finally { setBusy(false); }
  }
  const button = "min-h-11 rounded-lg border border-mars-border px-4 py-2 text-sm disabled:opacity-40";
  return <dialog ref={ref} aria-labelledby="remove-project-title" onCancel={(event) => { if (busy) event.preventDefault(); else onClose(); }} className="w-[min(620px,95vw)] max-h-[85vh] space-y-4 overflow-y-auto rounded-xl border border-mars-border bg-mars-bg p-6 text-slate-200 backdrop:bg-black/60">
    <h2 id="remove-project-title" className="text-lg font-semibold">移除项目 · {project.display_name || project.name}</h2>
    <p className="break-all text-sm text-slate-400">{preview?.path || project.folder_path}</p>
    <fieldset disabled={busy} className="space-y-3"><legend className="mb-2 text-sm">如何处理项目文件？</legend>
      <label className="flex gap-3 rounded-lg border border-mars-border p-4"><input type="radio" name="removal-mode" checked={mode === "detach"} onChange={() => setMode("detach")} /><span>仅从列表移除<span className="mt-1 block text-xs text-slate-400">保留原位置的代码、数据、对话和结果，可重新打开。</span></span></label>
      <label className="flex gap-3 rounded-lg border border-mars-border p-4"><input type="radio" name="removal-mode" checked={mode === "recycle"} onChange={() => setMode("recycle")} /><span>移除并回收项目文件<span className="mt-1 block text-xs text-slate-400">将整个项目文件夹移到同级回收目录，可恢复。不会释放磁盘空间。</span></span></label>
    </fieldset>
    {preview ? <p className="text-xs text-slate-400">{preview.message}</p> : <p role="status">正在核对运行任务与目录…</p>}
    <label className="block text-sm">输入项目名称「{preview?.display_name || project.display_name}」确认<input autoComplete="off" disabled={busy} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} className="mt-2 min-h-11 w-full rounded-lg border border-mars-border bg-mars-panel px-3 py-2" /></label>
    {blockers.length ? <div role="alert" className="text-sm text-amber-200">{blockers.join("；")}</div> : null}
    {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
    <footer className="flex justify-end gap-3"><button type="button" autoFocus disabled={busy} className={button} onClick={onClose}>取消</button><button type="button" disabled={busy || !preview || blockers.length > 0 || confirmation !== preview.display_name} className={`${button} border-red-400/40 text-red-200`} onClick={() => void submit()}>{busy ? "正在移除…" : "确认移除"}</button></footer>
  </dialog>;
}
