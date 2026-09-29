"use client";

import { useEffect, useState } from "react";
import { getCodeFilePage, type CodeFilePage } from "@/lib/api";

export function CodeFileContent({ runId, project, path, token }: { runId: string; project: string; path: string; token: string }): JSX.Element {
  const [page, setPage] = useState<CodeFilePage | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [request, setRequest] = useState({ start: 0, version: "", attempt: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    void getCodeFilePage(runId, project, path, request.start, token, request.version, controller.signal).then(value => {
      if (value.run_id !== runId || value.project !== project || value.path !== path || value.repository_token !== token || value.start !== request.start) throw new Error("代码文件来源不匹配，请重新读取");
      if (!controller.signal.aborted) { setPage(value); setLines(previous => request.start ? [...previous.slice(0, request.start), ...value.lines] : value.lines); }
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "读取失败"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runId, project, path, token, request]);
  const reload = (): void => { setPage(null); setLines([]); setRequest(previous => ({ start: 0, version: "", attempt: previous.attempt + 1 })); };
  return <div className="flex min-h-0 flex-1 flex-col">
    <div className="flex shrink-0 items-center justify-between border-b border-mars-border px-4 py-2 text-xs text-slate-500"><span>当前项目文件{page ? ` · ${lines.length} / ${page.total_lines} 行${page.next_start === null ? " · 完整" : ""}` : ""}</span><button type="button" onClick={reload} disabled={loading} className="shrink-0 text-indigo-300 disabled:opacity-50">重新读取</button></div>
    <div className="min-h-0 flex-1 overflow-auto">
      <table aria-label="完整代码文件" className="w-full border-collapse font-mono text-xs leading-6"><tbody>{lines.map((line, index) => <tr key={index}><td className="w-12 select-none border-r border-mars-border px-3 text-right align-top text-slate-500">{index + 1}</td><td className="whitespace-pre px-4 text-slate-300">{line.replace(/[\r\n]+$/, "") || " "}</td></tr>)}</tbody></table>
      {error ? <p role="alert" className="p-4 text-xs text-amber-300">{error}</p> : null}
      {loading ? <p role="status" className="p-4 text-xs text-slate-500">正在读取文件…</p> : page?.next_start !== null && page?.next_start !== undefined && !error ? <button type="button" onClick={() => setRequest({ start: page.next_start ?? 0, version: page.version, attempt: 0 })} className="m-4 rounded border border-mars-border px-4 py-2 text-xs text-indigo-300">继续加载后续代码</button> : page && !lines.length && !error ? <p className="p-4 text-xs text-slate-500">空文件</p> : null}
    </div>
  </div>;
}
