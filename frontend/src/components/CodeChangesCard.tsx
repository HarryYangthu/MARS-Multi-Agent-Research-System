"use client";

import { useEffect, useRef, useState } from "react";
import { getCodeChanges, getCodeChange, type CodeChange, type CodeChanges, type CodeChangeDetail } from "@/lib/api";
import { CLIENT_POLICY } from "@/lib/clientPolicy";

const label = (status: string): string => ({ applied: "已写入", not_applied: "写入未成功", proposed: "待应用", recorded: "已记录 · 待核对" }[status] || "待核对");
function Counts({ item }: { item: CodeChange }): JSX.Element {
  return item.additions === null || item.deletions === null ? <span className="text-xs text-slate-500">内容预览</span> : <span className="flex shrink-0 gap-2 font-mono text-xs"><span className="text-emerald-400">+{item.additions}</span><span className="text-rose-400">−{item.deletions}</span></span>;
}

export function CodeChangesCard({ runId, project }: { runId: string; project: string }): JSX.Element {
  const [view, setView] = useState<CodeChanges | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const controller = new AbortController(); let reading = false;
    const refresh = async (): Promise<void> => {
      if (reading) return; reading = true;
      try {
        const next = await getCodeChanges(runId, project, controller.signal);
        if (next.run_id !== runId || next.project !== project) throw new Error("代码记录所属任务不匹配");
        if (!controller.signal.aborted) { setView(next); setError(""); }
      } catch (cause: unknown) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "代码改动暂时无法读取"); }
      finally { reading = false; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), CLIENT_POLICY.readinessRefreshMs);
    return () => { controller.abort(); clearInterval(timer); };
  }, [runId, project]);
  useEffect(() => { if (open) dialog.current?.showModal(); else dialog.current?.close(); }, [open]);
  const history = [...(view?.items ?? [])].sort((a, b) => b.timestamp.localeCompare(a.timestamp) || b.id.localeCompare(a.id));
  const byPath = new Map<string, CodeChange>();
  for (const item of history) if (!byPath.has(item.path)) byPath.set(item.path, item);
  const files = [...byPath.values()].sort((a, b) => a.path.localeCompare(b.path));
  const chosen = history.find(item => item.id === selected);
  const versions = history.filter(item => item.path === chosen?.path);
  const inspect = (id: string): void => { setSelected(id); setOpen(true); };
  return <section aria-label="代码改动" className="rounded-xl border border-mars-border bg-mars-panel/30">
    <div className="flex items-center justify-between gap-3 px-4 py-3"><h3 className="text-sm font-medium">代码改动 <span className="ml-2 font-normal text-slate-500">{files.length ? `${files.length} 个文件` : "暂无记录"}</span></h3>{files.length ? <button type="button" onClick={() => inspect(files[0].id)} className="text-xs text-indigo-300 hover:underline">查看改动 ↗</button> : null}</div>
    {error ? <p role="alert" className="px-4 pb-3 text-xs text-amber-300">{error}</p> : null}
    {files.length ? <div className="max-h-52 overflow-y-auto border-t border-mars-border">{files.map(item => <button key={item.id} type="button" onClick={() => inspect(item.id)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-white/5"><span className="min-w-0 flex-1 truncate font-mono text-xs" title={item.path}>{item.path}</span><span className="text-xs text-slate-500">{label(item.status)}</span><Counts item={item} /></button>)}</div> : null}
    {view?.warnings.length ? <details className="px-4 py-2 text-xs text-amber-300"><summary>部分记录不可展示</summary>{view.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</details> : null}
    <dialog ref={dialog} aria-labelledby="code-review-title" onClose={() => setOpen(false)} className="m-auto h-[92dvh] w-[96vw] max-w-[1600px] overflow-hidden rounded-xl border border-mars-border bg-mars-bg p-0 text-slate-200 shadow-2xl backdrop:bg-black/70">
      {open ? <div className="flex h-full min-h-0 flex-col">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-mars-border px-5 py-4"><div><h2 id="code-review-title" className="font-medium">代码改动</h2><p className="mt-1 max-w-[65vw] truncate text-xs text-slate-500">{runId}</p></div><button type="button" autoFocus onClick={() => setOpen(false)} className="rounded-lg border border-mars-border px-3 py-1.5 text-sm hover:bg-mars-panel">关闭</button></header>
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-mars-border bg-mars-panel/40 px-4 py-3"><span className="min-w-0 break-all font-mono text-xs">{chosen?.path}</span>{chosen ? <div className="flex items-center gap-3"><span className={chosen.status === "applied" ? "text-xs text-emerald-300" : "text-xs text-amber-300"}>{label(chosen.status)}</span><Counts item={chosen} /></div> : null}
              {versions.length > 1 ? <select aria-label="改动记录版本" value={selected} onChange={event => setSelected(event.target.value)} className="max-w-full rounded border border-mars-border bg-mars-panel px-2 py-1 text-xs">{versions.map((item, index) => <option key={item.id} value={item.id}>{index === 0 ? "最新记录" : "历史记录"} · {new Date(item.timestamp).toLocaleString()} · {label(item.status)}</option>)}</select> : null}
            </div>
            {chosen ? <ChangeContent key={`${runId}:${chosen.id}`} runId={runId} project={project} item={chosen} /> : <p className="p-6 text-sm text-slate-400">请选择文件。</p>}
          </div>
          <nav aria-label="改动文件" className="order-first max-h-36 shrink-0 overflow-y-auto border-b border-mars-border bg-mars-panel/30 md:order-last md:max-h-none md:w-72 md:border-b-0 md:border-l"><p className="px-4 py-3 text-xs text-slate-500">{files.length} 个文件</p>{files.map(item => <button key={item.path} type="button" aria-pressed={item.path === chosen?.path} onClick={() => setSelected(item.id)} className={`flex w-full items-center gap-2 px-4 py-3 text-left ${item.path === chosen?.path ? "bg-indigo-400/15 text-indigo-200" : "hover:bg-white/5"}`}><span className="min-w-0 flex-1 break-all font-mono text-xs">{item.path}</span><Counts item={item} /></button>)}</nav>
        </div>
      </div> : null}
    </dialog>
  </section>;
}

function ChangeContent({ runId, project, item }: { runId: string; project: string; item: CodeChange }): JSX.Element {
  const [detail, setDetail] = useState<CodeChangeDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void getCodeChange(runId, project, item.id, controller.signal).then(value => {
      if (value.run_id !== runId || value.project !== project || value.id !== item.id) throw new Error("代码记录所属任务不匹配");
      if (!controller.signal.aborted) setDetail(value);
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "读取失败，请关闭后重试。"); });
    return () => controller.abort();
  }, [runId, project, item.id]);
  if (error) return <p role="alert" className="p-6 text-sm text-amber-300">{error}</p>;
  if (!detail) return <p role="status" className="p-6 text-slate-400">正在读取改动…</p>;
  return <div className="min-h-0 flex-1 overflow-auto">
    {detail.warning ? <p className="border-b border-mars-border px-4 py-3 text-xs text-amber-200">{detail.warning}</p> : null}
    {detail.change === "content" ? <p className="border-b border-mars-border px-4 py-3 text-xs text-amber-200">拟写入内容 · 缺少修改前快照，无法计算差异。</p> : null}
    <table aria-label="代码逐行改动" className="w-full border-collapse font-mono text-xs leading-6"><tbody>{detail.lines.map((line, index) => <tr key={index} className={line.kind === "add" ? "bg-emerald-500/10 text-emerald-200" : line.kind === "delete" ? "bg-rose-500/10 text-rose-200" : line.kind === "hunk" ? "bg-indigo-400/10 text-indigo-300" : "text-slate-300"}>
      <td className="w-12 select-none px-2 text-right text-slate-500">{line.old_line}</td><td className="w-12 select-none border-r border-mars-border px-2 text-right text-slate-500">{line.new_line}</td><td className="w-6 select-none px-2">{line.kind === "add" ? "+" : line.kind === "delete" ? "−" : ""}</td><td className="whitespace-pre pr-6">{line.text || " "}</td>
    </tr>)}</tbody></table>
    {!detail.lines.length ? <p className="p-6 text-sm text-slate-400">没有可展示的逐行差异。</p> : null}
    {detail.truncated ? <p className="p-4 text-xs text-amber-300">内容较长，当前仅展示前部分行。</p> : null}
  </div>;
}
