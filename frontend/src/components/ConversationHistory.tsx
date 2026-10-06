"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { existingConversationUrl, listConversationSummaries, projectConversations, type ConversationSummary } from "@/lib/conversationHistory";

export function useConversationHistory(): { rows: ConversationSummary[]; loading: boolean; error: string; refresh: () => Promise<void> } {
  const [rows, setRows] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const refresh = useCallback(async (): Promise<void> => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true); setError("");
    try {
      const next = await listConversationSummaries(controller.signal);
      if (!controller.signal.aborted) setRows(next);
    } catch (cause: unknown) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "暂时无法读取历史对话，请重试。");
    } finally { if (!controller.signal.aborted) setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); return () => request.current?.abort(); }, [refresh]);
  return { rows, loading, error, refresh };
}

export function ProjectConversationActions({ project, rows, loading, error, onSelect, onHistory, buttonClass }: {
  project: string; rows: ConversationSummary[]; loading: boolean; error: string;
  onSelect: () => void; onHistory: () => void; buttonClass: string;
}): JSX.Element {
  const latest = projectConversations(rows, project)[0];
  return <>
    {latest && !loading && !error ? <Link href={existingConversationUrl(latest)} onClick={onSelect} className={`${buttonClass} border-indigo-400/40 text-indigo-100`}>继续对话</Link>
      : <button type="button" disabled className={buttonClass} title={error ? "请从历史对话中重新读取" : loading ? "正在读取历史对话" : "暂无历史对话"}>继续对话</button>}
    <button type="button" onClick={onHistory} className={buttonClass}>历史对话</button>
  </>;
}

function dateLabel(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function ConversationHistoryDialog({ project, name, rows, loading, error, currentId, experimentId, onClose, onSelect, onRefresh }: {
  project: string; name: string; rows: ConversationSummary[]; loading: boolean; error: string; currentId?: string; experimentId?: string;
  onClose: () => void; onSelect: () => void; onRefresh: () => Promise<void>;
}): JSX.Element {
  const dialog = useRef<HTMLDialogElement>(null);
  const conversations = projectConversations(rows, project, experimentId);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  return <dialog ref={dialog} aria-labelledby="conversation-history-title" onCancel={onClose} className="max-h-[85dvh] w-[min(640px,calc(100vw-2rem))] rounded-2xl border border-mars-border bg-mars-bg p-0 text-slate-200 shadow-2xl backdrop:bg-black/60">
    <header className="flex items-start justify-between gap-4 border-b border-mars-border p-5">
      <div><h2 id="conversation-history-title" className="text-lg font-semibold">历史对话</h2><p className="mt-1 text-xs text-slate-400">{name} · 按最近更新排序</p></div>
      <button type="button" onClick={onClose} className="rounded-lg border border-mars-border px-3 py-2 text-xs hover:bg-mars-panel">关闭</button>
    </header>
    <div className="space-y-3 p-5">
      {loading ? <p role="status" className="py-6 text-center text-sm text-slate-400">正在读取历史对话…</p> : null}
      {error ? <div role="alert" className="rounded-lg border border-amber-500/30 p-3 text-sm text-amber-200"><p>{error}</p><button type="button" disabled={loading} onClick={() => void onRefresh()} className="mt-2 underline disabled:opacity-40">重新读取</button></div> : null}
      {!loading && !error && !conversations.length ? <p className="py-8 text-center text-sm text-slate-400">这个项目还没有历史对话。开始研究后，对话会保存在这里。</p> : null}
      {!loading && !error ? conversations.map((row, index) => <Link key={row.conv_id} href={existingConversationUrl(row)} onClick={() => { onSelect(); onClose(); }} className="block rounded-xl border border-mars-border p-4 hover:border-indigo-400/50 hover:bg-mars-panel focus-visible:outline-indigo-400">
        <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm font-medium">研究对话 · {dateLabel(row.created_at)}</span><span className="text-xs text-indigo-300">{row.conv_id === currentId ? "当前对话" : index === 0 ? "最近对话" : "打开对话 →"}</span></div>
        <p className="mt-2 text-xs text-slate-400">{row.message_count} 条消息{row.processing ? " · 正在处理" : row.linked_run_id ? " · 已关联研究任务" : ""}</p>
        <p className="mt-2 text-xs text-slate-500">更新于 {dateLabel(row.updated_at)}</p>
      </Link>) : null}
    </div>
  </dialog>;
}
