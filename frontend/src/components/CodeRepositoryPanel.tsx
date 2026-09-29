"use client";

import { useEffect, useState } from "react";
import { getCodeDirectory, type CodeChange, type CodeDirectory, type CodeDirectoryEntry } from "@/lib/api";
import { ChangeContent } from "./CodeChangeContent";
import { CodeFileContent } from "./CodeFileContent";

const statusLabel = (status: string): string => ({ applied: "已写入", not_applied: "未写入", proposed: "待应用", recorded: "待核对" }[status] || "待核对");
const message = (error: unknown): string => error instanceof Error ? error.message : "读取失败，请重试";

export function CodeRepositoryPanel({ runId, project, history, initialChange }: {
  runId: string; project: string; history: CodeChange[]; initialChange?: CodeChange;
}): JSX.Element {
  const [selected, setSelected] = useState(initialChange?.path ?? "");
  const [changeId, setChangeId] = useState(initialChange?.id ?? "");
  const [mode, setMode] = useState<"file" | "change">(initialChange ? "change" : "file");
  const [diskSelected, setDiskSelected] = useState(false);
  const [onlyChanges, setOnlyChanges] = useState(false);
  const [root, setRoot] = useState<CodeDirectory | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setRoot(null); setError("");
    void getCodeDirectory(runId, project, "", 0, "", controller.signal).then(value => {
      if (value.run_id !== runId || value.project !== project) throw new Error("代码工程所属任务不匹配");
      if (!controller.signal.aborted) setRoot(value);
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(message(cause)); });
    return () => controller.abort();
  }, [runId, project, refresh]);
  const latest = new Map<string, CodeChange>();
  for (const item of history) if (!latest.has(item.path)) latest.set(item.path, item);
  const changes = [...latest.values()].sort((a, b) => a.path.localeCompare(b.path));
  const versions = history.filter(item => item.path === selected);
  const chosen = versions.find(item => item.id === changeId) ?? versions[0];
  const selectFile = (entry: CodeDirectoryEntry): void => { setSelected(entry.path); setDiskSelected(true); setMode("file"); setChangeId(latest.get(entry.path)?.id ?? ""); };
  const selectChange = (item: CodeChange): void => { setSelected(item.path); setChangeId(item.id); setMode("change"); setDiskSelected(false); };
  return <div className="flex min-h-0 flex-1 flex-col md:flex-row">
    <main className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-mars-border bg-mars-panel/40 px-4 py-3">
        <span className="min-w-0 break-all font-mono text-xs">{selected || "选择代码文件"}</span>
        {selected ? <div className="flex items-center gap-3 text-xs">
          {diskSelected ? <button type="button" aria-pressed={mode === "file"} onClick={() => setMode("file")} className={mode === "file" ? "text-indigo-300" : "text-slate-500"}>完整文件</button> : null}
          {chosen ? <button type="button" aria-pressed={mode === "change"} onClick={() => setMode("change")} className={mode === "change" ? "text-indigo-300" : "text-slate-500"}>改动记录</button> : null}
        </div> : null}
        {mode === "change" && chosen ? <div className="flex w-full flex-wrap items-center gap-3 text-xs"><span className={chosen.status === "applied" ? "text-emerald-300" : "text-amber-300"}>{statusLabel(chosen.status)}</span>{versions.length > 1 ? <select aria-label="改动记录版本" value={chosen.id} onChange={event => setChangeId(event.target.value)} className="max-w-full rounded border border-mars-border bg-mars-panel px-2 py-1">{versions.map((item, index) => <option key={item.id} value={item.id}>{index === 0 ? "最新记录" : "历史记录"} · {new Date(item.timestamp).toLocaleString()} · {statusLabel(item.status)}</option>)}</select> : null}</div> : null}
      </div>
      {mode === "change" && chosen ? <ChangeContent key={`${runId}:${chosen.id}`} runId={runId} project={project} item={chosen} /> : selected && root ? <CodeFileContent key={`${root.repository_token}:${selected}:${refresh}`} runId={runId} project={project} path={selected} token={root.repository_token} /> : <p className="p-6 text-sm text-slate-500">从右侧选择文件查看完整内容。</p>}
    </main>
    <aside aria-label="代码文件" className="order-first flex max-h-[32vh] min-h-0 shrink-0 flex-col border-b border-mars-border bg-mars-panel/30 md:order-last md:max-h-none md:w-80 md:border-b-0 md:border-l">
      <div className="shrink-0 border-b border-mars-border px-3 py-3">
        <div className="flex items-center gap-3 text-xs"><button type="button" aria-pressed={!onlyChanges} onClick={() => setOnlyChanges(false)} className={!onlyChanges ? "text-indigo-300" : "text-slate-500"}>全部文件</button><button type="button" aria-pressed={onlyChanges} onClick={() => setOnlyChanges(true)} className={onlyChanges ? "text-indigo-300" : "text-slate-500"}>仅看改动</button><button type="button" aria-label="刷新代码工程" onClick={() => setRefresh(value => value + 1)} className="ml-auto text-slate-400">刷新</button></div>
        {!onlyChanges && root ? <p title={root.root_path} className="mt-3 truncate font-mono text-xs text-slate-400">{root.root_name}<span className="ml-2 font-sans text-slate-500">{root.read_only ? "只读" : "当前项目"}</span></p> : null}
      </div>
      <nav aria-label="项目代码目录" className="min-h-0 flex-1 overflow-auto py-2">
        {!onlyChanges ? <>{error ? <p role="alert" className="break-words p-3 text-xs text-amber-300">{error}</p> : root ? <DirectoryBranch key={`${root.repository_token}:${refresh}`} runId={runId} project={project} path="" token={root.repository_token} depth={0} selected={selected} changes={latest} onSelect={selectFile} initial={root} /> : <p role="status" className="p-3 text-xs text-slate-500">正在读取目录…</p>}</> : null}
        {changes.length ? <div className={!onlyChanges ? "mt-3 border-t border-mars-border pt-3" : ""}>
          {!onlyChanges ? <p className="px-3 pb-2 text-xs text-slate-500">改动记录 · {changes.length}</p> : null}
          {changes.map(item => <button key={item.path} type="button" onClick={() => selectChange(item)} aria-pressed={selected === item.path && mode === "change"} className={`flex w-full items-start gap-2 px-3 py-2 text-left text-xs ${selected === item.path && mode === "change" ? "bg-indigo-400/15 text-indigo-200" : "hover:bg-white/5"}`}><span className="min-w-0 flex-1 break-all font-mono">{item.path}</span><span className="shrink-0 text-slate-500">{statusLabel(item.status)}</span></button>)}
        </div> : onlyChanges ? <p className="p-3 text-xs text-slate-500">暂无改动记录</p> : null}
      </nav>
    </aside>
  </div>;
}

function DirectoryBranch({ runId, project, path, token, depth, selected, changes, onSelect, initial }: {
  runId: string; project: string; path: string; token: string; depth: number; selected: string;
  changes: Map<string, CodeChange>; onSelect: (entry: CodeDirectoryEntry) => void; initial?: CodeDirectory;
}): JSX.Element {
  const [page, setPage] = useState<CodeDirectory | null>(initial ?? null);
  const [entries, setEntries] = useState<CodeDirectoryEntry[]>(initial?.entries ?? []);
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(!initial);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (initial && offset === 0 && attempt === 0) return;
    const controller = new AbortController(); setLoading(true); setError("");
    void getCodeDirectory(runId, project, path, offset, token, controller.signal).then(value => {
      if (value.run_id !== runId || value.project !== project || value.path !== path || value.repository_token !== token) throw new Error("代码工程目录已变更，请刷新");
      if (!controller.signal.aborted) { setPage(value); setEntries(previous => [...new Map((offset ? [...previous, ...value.entries] : value.entries).map(entry => [entry.path, entry])).values()]); }
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(message(cause)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runId, project, path, offset, token, initial, attempt]);
  return <ul aria-label={path || "根目录"}>
    {entries.map(entry => <li key={entry.path}>
      {entry.kind === "directory" ? <><button type="button" aria-expanded={expanded.has(entry.path)} onClick={() => setExpanded(previous => { const next = new Set(previous); if (next.has(entry.path)) next.delete(entry.path); else next.add(entry.path); return next; })} style={{ paddingLeft: `${12 + depth * 14}px` }} className="flex w-full gap-2 py-2 pr-3 text-left text-xs hover:bg-white/5"><span aria-hidden="true">{expanded.has(entry.path) ? "▾" : "▸"}</span><span className="truncate font-mono">{entry.name}/</span></button>
        {expanded.has(entry.path) ? <DirectoryBranch runId={runId} project={project} path={entry.path} token={token} depth={depth + 1} selected={selected} changes={changes} onSelect={onSelect} /> : null}</> : <button type="button" aria-pressed={selected === entry.path} title={entry.path} onClick={() => onSelect(entry)} style={{ paddingLeft: `${26 + depth * 14}px` }} className={`flex w-full items-center gap-2 py-2 pr-3 text-left text-xs ${selected === entry.path ? "bg-indigo-400/15 text-indigo-200" : "hover:bg-white/5"}`}><span className="min-w-0 flex-1 truncate font-mono">{entry.name}</span>{changes.has(entry.path) ? <span aria-label="有改动记录" className="text-amber-300">●</span> : null}</button>}
    </li>)}
    {error ? <li className="p-3 text-xs text-amber-300"><p role="alert">{error}</p><button type="button" onClick={() => setAttempt(value => value + 1)} className="mt-2 underline">重试读取目录</button></li> : null}
    {loading ? <li role="status" className="p-3 text-xs text-slate-500">读取中…</li> : !error && page?.next_offset !== null && page?.next_offset !== undefined ? <li><button type="button" onClick={() => setOffset(page.next_offset ?? 0)} className="px-4 py-2 text-xs text-indigo-300">加载更多文件</button></li> : !error && page && !entries.length ? <li className="px-4 py-2 text-xs text-slate-500">无可浏览文件</li> : null}
  </ul>;
}
