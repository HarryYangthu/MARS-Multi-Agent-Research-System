"use client";

import { useEffect, useRef, useState } from "react";
import { getCodeChanges, type CodeChange, type CodeChanges } from "@/lib/api";
import { CodeRepositoryPanel } from "./CodeRepositoryPanel";
import { CLIENT_POLICY } from "@/lib/clientPolicy";

const label = (status: string): string => status === "applied" ? "已核对" : "待核对";
function Counts({ item }: { item: CodeChange }): JSX.Element {
  return item.additions === null || item.deletions === null ? <span className="text-xs text-slate-500">内容预览</span> : <span className="flex shrink-0 gap-2 font-mono text-xs"><span className="text-emerald-400">+{item.additions}</span><span className="text-rose-400">−{item.deletions}</span></span>;
}

export function CodeChangesCard({ runId, project }: { runId: string; project: string }): JSX.Element {
  const [view, setView] = useState<CodeChanges | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    setView(null); setError(""); setOpen(false); setExpanded(false); setSelected("");
    const controller = new AbortController(); let reading = false;
    const refresh = async (): Promise<void> => {
      if (reading) return; reading = true;
      try {
        const next = await getCodeChanges(runId, project, controller.signal);
        if (next.run_id !== runId || next.project !== project) throw new Error("代码记录所属任务不匹配");
        if (!controller.signal.aborted) { setView(next); setError(""); }
      } catch (cause: unknown) { if (!controller.signal.aborted) { setView(null); setError(cause instanceof Error ? cause.message : "代码改动暂时无法读取"); } }
      finally { reading = false; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), CLIENT_POLICY.readinessRefreshMs);
    return () => { controller.abort(); clearInterval(timer); };
  }, [runId, project]);
  useEffect(() => { if (open) dialog.current?.showModal(); else dialog.current?.close(); }, [open]);
  const history = [...(view?.items ?? [])].filter(item => item.status === "applied" && item.change !== "unchanged").sort((a, b) => b.timestamp.localeCompare(a.timestamp) || b.id.localeCompare(a.id));
  const byPath = new Map<string, CodeChange>();
  for (const item of history) if (!byPath.has(item.path)) byPath.set(item.path, item);
  const files = [...byPath.values()].sort((a, b) => a.path.localeCompare(b.path));
  const chosen = history.find(item => item.id === selected);
  const inspect = (id: string): void => { setSelected(id); setOpen(true); };
  if (!files.length) return error || view?.warnings.length ? <p role="status" className="text-xs text-amber-300">代码改动暂时无法完整核对，请查看任务详情。</p> : <></>;
  return <section aria-label="代码改动" className="rounded-xl border border-mars-border bg-mars-panel/30">
    <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"><h3 className="text-sm font-medium">代码改动 <span className="ml-2 font-normal text-slate-500">{files.length} 个文件</span></h3><div className="flex items-center gap-4"><button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)} className="rounded-lg border border-mars-border px-3 py-1.5 text-xs">{expanded ? "收起" : "展开"}</button><button type="button" onClick={() => inspect("")} className="text-xs text-indigo-300 hover:underline">查看全部代码 ↗</button></div></div>
    {error ? <p role="alert" className="px-4 pb-3 text-xs text-amber-300">{error}</p> : null}
    {expanded ? <div className="border-t border-mars-border">{files.map(item => <button key={item.id} type="button" onClick={() => inspect(item.id)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-white/5"><span className="min-w-0 flex-1 truncate font-mono text-xs" title={item.path}>{item.path}</span><span className="text-xs text-slate-500">{label(item.status)}</span><Counts item={item} /></button>)}</div> : null}
    {view?.warnings.length ? <details className="px-4 py-2 text-xs text-amber-300"><summary>部分改动尚未核对</summary>{view.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</details> : null}
    <dialog ref={dialog} aria-labelledby="code-review-title" onClose={() => setOpen(false)} className="m-auto h-[92dvh] w-[96vw] max-w-[1600px] overflow-hidden rounded-xl border border-mars-border bg-mars-bg p-0 text-slate-200 shadow-2xl backdrop:bg-black/70">
      {open ? <div className="flex h-full min-h-0 flex-col">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-mars-border px-5 py-4"><div><h2 id="code-review-title" className="font-medium">代码工程</h2><p className="mt-1 max-w-[65vw] truncate text-xs text-slate-500">{runId}</p></div><button type="button" autoFocus onClick={() => setOpen(false)} className="rounded-lg border border-mars-border px-3 py-1.5 text-sm hover:bg-mars-panel">关闭</button></header>
        <CodeRepositoryPanel runId={runId} project={project} history={history} initialChange={chosen} />
      </div> : null}
    </dialog>
  </section>;
}
