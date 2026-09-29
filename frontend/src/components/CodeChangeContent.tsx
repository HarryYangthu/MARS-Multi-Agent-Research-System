"use client";

import { useEffect, useState } from "react";
import { getCodeChange, type CodeChange, type CodeChangeDetail } from "@/lib/api";

export function ChangeContent({ runId, project, item }: { runId: string; project: string; item: CodeChange }): JSX.Element {
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
