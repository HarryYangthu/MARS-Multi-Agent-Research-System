"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatMessageFailure, createConversation, getConversation, sendChatMessage, type ChatMessageView, type Conversation } from "@/lib/api";
import { ActivityGroup, ResearchAgentPanel, useResearchActivity } from "./ResearchActivity";
import { conversationEntries, groupConversationEntries, type Activity } from "@/lib/researchActivity";
import { CLIENT_POLICY } from "@/lib/clientPolicy";

const storageKey = (project: string): string => `mars.commander.conv.${project}`;
function savedConversation(project: string): string | null {
  try { return window.localStorage.getItem(storageKey(project)); } catch { return null; }
}
function remember(project: string, id: string | null): void {
  try {
    if (id) window.localStorage.setItem(storageKey(project), id);
    else window.localStorage.removeItem(storageKey(project));
  } catch { /* The current conversation still works without browser storage. */ }
}

export function ResearchConversation({ project, name }: { project: string; name: string }): JSX.Element {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [pending, setPending] = useState<{ text: string; after: number } | null>(null);
  const sending = useRef(false);
  const alive = useRef(true);
  const bottom = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const activity = useResearchActivity(conversation?.linked_run_id, project);
  const processing = busy || conversation?.processing === true;
  const publicActivities: Activity[] = (conversation?.activities ?? []).map(item => ({ ...item, id: `commander:${item.id}`, agent: "commander", detail: item.status === "failed" ? "本次处理失败" : item.status === "interrupted" ? "本次处理已中断" : "", title: item.status === "completed" ? item.title.replace("正在调用", "已调用").replace("正在执行", "已执行") : item.title }));
  const entries = conversationEntries(conversation?.messages ?? [], [...publicActivities, ...activity.activities]);
  const groups = groupConversationEntries(entries);
  const lastEntry = entries.at(-1)?.id;
  const current = [...publicActivities].reverse().find(item => item.status === "running")?.title;


  useEffect(() => {
    alive.current = true;
    let active = true;
    const id = savedConversation(project);
    if (!id) { setLoading(false); return () => { alive.current = false; }; }
    void getConversation(id).then((value) => {
      if (!active) return;
      if (value.project !== project) throw new Error("历史对话所属项目不匹配，请新开对话。");
      setConversation(value);
    }).catch(() => {
      if (active) { setError("暂时无法恢复历史对话，请重新读取或新开对话。"); setNeedsRefresh(true); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; alive.current = false; };
  }, [project]);

  // Read actual persisted/in-flight Commander messages, including tool receipts.
  // Polling never sends the user's message again.
  useEffect(() => {
    if (!conversation?.conv_id || (!busy && !conversation.processing && !conversation.linked_run_id)) return;
    const id = conversation.conv_id;
    let active = true;
    let reading = false;
    const timer = setInterval(() => {
      if (reading) return;
      reading = true;
      void getConversation(id).then((value) => { if (active) { setConversation(value); setPollError(""); } })
        .catch(() => { if (active) setPollError("对话进度更新失败，正在重试。"); })
        .finally(() => { reading = false; });
    }, CLIENT_POLICY.controlRefreshMs);
    return () => { active = false; clearInterval(timer); };
  }, [conversation?.conv_id, conversation?.linked_run_id, conversation?.processing, busy]);

  useEffect(() => { if (follow.current) bottom.current?.scrollIntoView({ block: "end" }); }, [lastEntry, pending, busy]);

  async function refresh(): Promise<void> {
    const id = conversation?.conv_id || savedConversation(project);
    if (sending.current) return;
    if (!id) { setNeedsRefresh(false); setError(""); setPending(null); return; }
    setLoading(true);
    try {
      const value = await getConversation(id);
      if (value.project !== project) throw new Error("对话所属项目不匹配。");
      if (alive.current) { setConversation(value); setPending(null); setNeedsRefresh(false); setError(""); }
    } catch (cause: unknown) {
      if (alive.current) setError(cause instanceof Error ? cause.message : "读取失败，请稍后重试。");
    } finally { if (alive.current) setLoading(false); }
  }

  async function send(): Promise<void> {
    const text = draft.trim();
    if (!text || sending.current || conversation?.processing || loading || needsRefresh) return;
    follow.current = true; sending.current = true; setBusy(true); setError("");
    setPending({ text, after: conversation?.messages.length ?? 0 }); setDraft("");
    try {
      const current = conversation || await createConversation(project);
      remember(project, current.conv_id);
      if (!alive.current) return;
      setConversation(current);
      const updated = await sendChatMessage(current.conv_id, text);
      if (alive.current) { setConversation(updated); setPending(null); }
    } catch (cause: unknown) {
      if (alive.current) {
        const saved = cause instanceof ChatMessageFailure && cause.messageSaved;
        setError(saved ? cause.message : `${cause instanceof Error ? cause.message : "消息未能确认发送"}。请重新读取对话，核对后再继续。`);
        setNeedsRefresh(true); setDraft(saved ? "" : text);
      }
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  }

  const messages = conversation?.messages.filter((message) => message.role !== "system") ?? [];
  const showPending = pending && !conversation?.messages.slice(pending.after).some((message) => message.role === "user" && message.content === pending.text);
  return <section aria-label="研究对话" className="flex min-h-0 flex-1 flex-col">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-mars-border px-4 py-3 sm:px-6">
      <div className="min-w-0"><h1 className="text-base font-medium">研究对话</h1><p className="mt-1 truncate text-xs text-slate-500">{name}</p></div>
      <div className="flex gap-3 text-xs text-slate-400">
        <Link href="/runs" className="hover:text-white">研究记录</Link>
        <button type="button" disabled={processing || loading} onClick={() => { remember(project, null); setConversation(null); setPending(null); setDraft(""); setError(""); setNeedsRefresh(false); }} className="hover:text-white disabled:opacity-40">新对话</button>
      </div>
    </header>
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
    <div className="flex min-h-[65vh] min-w-0 flex-1 flex-col lg:min-h-0">
    <div onScroll={event => { const el = event.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }} className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6">
      <div className="mx-auto flex min-h-full max-w-3xl flex-col gap-6">
        {loading && !conversation ? <p role="status" className="my-auto text-center text-sm text-slate-400">正在读取对话…</p> : messages.length === 0 && !pending ? <div className="my-auto py-12 text-center"><h2 className="text-2xl font-medium">这次想研究什么？</h2><p className="mt-3 text-sm text-slate-400">直接描述你的研究目标，也可以先一起讨论思路。</p></div> : null}
        {groups.map((entry, index) => entry.kind === "message" ? <ResearchMessage key={entry.id} message={entry.message} /> : <ActivityGroup key={entry.id} activities={entry.activities} expanded={index === groups.length - 1} />)}
        {showPending ? <div className="ml-auto max-w-[90%] whitespace-pre-wrap break-words rounded-2xl bg-mars-accent/25 px-5 py-3 text-sm leading-7">{pending.text}</div> : null}
        {processing ? <p role="status" className="text-sm text-indigo-300">{current || "正在处理本次请求…"}</p> : null}
        {pollError ? <p role="status" className="text-xs text-amber-300">{pollError}</p> : null}
        {conversation?.linked_run_id ? <Link href={`/runs/${encodeURIComponent(conversation.linked_run_id)}`} className="text-sm text-indigo-300 hover:underline">查看关联研究任务 →</Link> : null}
        <div ref={bottom} />
      </div>
    </div>
    <form onSubmit={(event) => { event.preventDefault(); void send(); }} className="shrink-0 px-4 pb-5 sm:px-6">
      <div className="mx-auto max-w-3xl">
        {error ? <div role="alert" className="mb-3 rounded-lg border border-amber-500/30 p-3 text-sm text-amber-200"><p>{error}</p><button type="button" disabled={loading || busy} onClick={() => void refresh()} className="mt-2 underline disabled:opacity-40">重新读取对话</button></div> : null}
        <div className="rounded-2xl border border-mars-border bg-mars-panel p-3 focus-within:border-indigo-400/60">
          <textarea aria-label="研究目标或补充要求" value={draft} onChange={(event) => setDraft(event.target.value)} rows={3} placeholder="描述你的研究目标，或继续补充要求…" onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
          }} className="block w-full resize-none bg-transparent px-2 py-1 text-sm leading-7 text-slate-100 outline-none placeholder:text-slate-500" />
          <div className="mt-2 flex items-center justify-between gap-3"><span className="text-xs text-slate-500">Enter 发送 · Shift + Enter 换行</span><button type="submit" disabled={processing || loading || needsRefresh || !draft.trim()} className="rounded-xl bg-mars-accent px-5 py-2 text-sm font-medium text-white hover:brightness-110 disabled:opacity-40">{processing ? "处理中…" : "发送"}</button></div>
        </div>
      </div>
    </form>
    </div>
    <ResearchAgentPanel run={activity.run} runId={conversation?.linked_run_id} activities={activity.activities} processing={processing} current={current} error={activity.error} updated={activity.updated} />
    </div>
  </section>;
}

function ResearchMessage({ message }: { message: ChatMessageView }): JSX.Element {
  if (message.role === "tool") return <details className="rounded-lg border border-mars-border bg-mars-panel/40 px-4 py-3 text-xs text-slate-400">
    <summary className="cursor-pointer">工具调用 · {message.tool_name}{message.tool_result?.ok === false ? " · 未完成" : ""}</summary>
    <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words leading-6">{message.content}{message.tool_result ? `\n${JSON.stringify(message.tool_result, null, 2)}` : ""}</pre>
  </details>;
  if (message.role === "user") return <div className="ml-auto max-w-[90%] whitespace-pre-wrap break-words rounded-2xl bg-mars-accent/25 px-5 py-3 text-sm leading-7">{message.content}</div>;
  return <div className="min-w-0 break-words text-sm leading-7 text-slate-200 [&_p]:my-3 [&_li]:ml-5 [&_ul]:list-disc [&_ol]:list-decimal [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-mars-panel [&_pre]:p-4 [&_a]:text-indigo-300 [&_h2]:my-4 [&_h2]:text-lg [&_h2]:font-medium [&_table]:block [&_table]:overflow-x-auto [&_td]:border [&_td]:border-mars-border [&_td]:p-2 [&_th]:border [&_th]:border-mars-border [&_th]:p-2">
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
  </div>;
}
