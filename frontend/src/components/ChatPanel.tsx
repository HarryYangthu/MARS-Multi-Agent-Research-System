"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

import {
  createConversation,
  getConversation,
  sendChatMessage,
  setConversationAutoMode,
  type ChatMessageView,
  type Conversation,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useProject } from "@/lib/project";

const STATE_LABEL_ZH: Record<string, string> = {
  idle: "待命",
  clarifying: "澄清需求",
  planning: "规划中",
  awaiting_confirm: "等待确认",
  executing: "执行中",
  awaiting_review: "等待审核",
  reporting: "汇报中",
};
const STATE_LABEL_EN: Record<string, string> = {
  idle: "idle",
  clarifying: "clarifying",
  planning: "planning",
  awaiting_confirm: "awaiting confirm",
  executing: "executing",
  awaiting_review: "awaiting review",
  reporting: "reporting",
};
const STATE_COLOR: Record<string, string> = {
  idle: "bg-slate-600 text-slate-100",
  clarifying: "bg-sky-500/30 text-sky-200",
  planning: "bg-indigo-500/30 text-indigo-200",
  awaiting_confirm: "bg-amber-500/30 text-amber-200",
  executing: "bg-emerald-500/30 text-emerald-200",
  awaiting_review: "bg-fuchsia-500/30 text-fuchsia-200",
  reporting: "bg-violet-500/30 text-violet-200",
};

const LS_KEY = "mars.commander.conv";

export function ChatPanel({ onLinkRun }: { onLinkRun?: (runId: string) => void }): JSX.Element {
  const { t, lang } = useI18n();
  const { selectedProject } = useProject();
  const [conv, setConv] = useState<Conversation | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // bootstrap: restore or create a conversation
  useEffect(() => {
    let alive = true;
    const storageKey = `${LS_KEY}.${selectedProject}`;
    const saved = typeof window !== "undefined" ? window.localStorage.getItem(storageKey) : null;
    const boot = async (): Promise<void> => {
      try {
        let c: Conversation;
        if (saved) {
          try {
            c = await getConversation(saved);
            if (c.project !== selectedProject) {
              c = await createConversation(selectedProject);
              window.localStorage.setItem(storageKey, c.conv_id);
            }
          } catch {
            c = await createConversation(selectedProject);
            window.localStorage.setItem(storageKey, c.conv_id);
          }
        } else {
          c = await createConversation(selectedProject);
          window.localStorage.setItem(storageKey, c.conv_id);
        }
        if (alive) setConv(c);
      } catch (e) {
        if (alive) setErr(String(e));
      }
    };
    void boot();
    return () => {
      alive = false;
    };
  }, [selectedProject]);

  // poll while a run is linked (background pipeline changes state)
  useEffect(() => {
    if (!conv?.conv_id || !conv.linked_run_id) return;
    let alive = true;
    const iv = setInterval(() => {
      if (busy) return;
      void getConversation(conv.conv_id)
        .then((c) => alive && setConv(c))
        .catch(() => {});
    }, 3000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [conv?.conv_id, conv?.linked_run_id, busy]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    if (conv?.linked_run_id) onLinkRun?.(conv.linked_run_id);
  }, [conv?.messages.length, conv?.linked_run_id]);

  async function send(): Promise<void> {
    if (!conv || !input.trim() || busy) return;
    const text = input.trim();
    setInput("");
    setBusy(true);
    setErr(null);
    // optimistic user bubble
    setConv({
      ...conv,
      messages: [
        ...conv.messages,
        { role: "user", content: text, timestamp: "", state: conv.state, tool_name: null, tool_args: null, tool_result: null },
      ],
    });
    try {
      const updated = await sendChatMessage(conv.conv_id, text);
      setConv(updated);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleAuto(): Promise<void> {
    if (!conv) return;
    try {
      const updated = await setConversationAutoMode(conv.conv_id, !conv.auto_mode);
      setConv(updated);
    } catch {
      /* ignore */
    }
  }

  function newConversation(): void {
    setBusy(true);
    const storageKey = `${LS_KEY}.${selectedProject}`;
    void createConversation(selectedProject)
      .then((c) => {
        window.localStorage.setItem(storageKey, c.conv_id);
        setConv(c);
      })
      .finally(() => setBusy(false));
  }

  const stateLabel = (s: string): string =>
    (lang === "zh" ? STATE_LABEL_ZH : STATE_LABEL_EN)[s] ?? s;

  return (
    <div className="flex h-full min-h-0 flex-col rounded border border-mars-border bg-mars-bg/40">
      {/* header */}
      <div className="flex items-center justify-between gap-1 border-b border-mars-border px-2 py-1.5">
        <span className="text-xs font-semibold text-slate-200">🧭 {t("chat.title")}</span>
        <div className="flex items-center gap-1">
          {conv ? (
            <span className={`rounded px-1.5 py-0.5 text-[9px] ${STATE_COLOR[conv.state] ?? "bg-slate-600"}`}>
              {stateLabel(conv.state)}
            </span>
          ) : null}
          <button
            onClick={toggleAuto}
            className={`rounded px-1.5 py-0.5 text-[9px] ${conv?.auto_mode ? "bg-emerald-500/30 text-emerald-200" : "bg-mars-subtle text-slate-400"}`}
            title={t("chat.autoToggle")}
          >
            {conv?.auto_mode ? t("chat.auto") : t("chat.semi")}
          </button>
          <button
            onClick={newConversation}
            className="rounded bg-mars-subtle px-1.5 py-0.5 text-[9px] text-slate-300 hover:bg-mars-border"
            title={t("chat.new")}
          >
            ＋
          </button>
        </div>
      </div>

      {/* linked run banner */}
      {conv?.linked_run_id ? (
        <Link
          href={`/runs/${conv.linked_run_id}`}
          className="border-b border-mars-border bg-mars-panel2 px-2 py-1 text-[9px] text-mars-accent hover:underline"
        >
          ▶ run: {conv.linked_run_id}
        </Link>
      ) : null}

      {/* input — pinned to the top so it doesn't get pushed down by an empty
          message area (command-console style) */}
      <div className="border-b border-mars-border p-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          rows={2}
          placeholder={t("chat.placeholder")}
          className="w-full resize-none rounded border border-mars-border bg-mars-bg px-2 py-1 text-[11px] text-slate-100 outline-none focus:border-mars-accent"
        />
        <button
          onClick={send}
          disabled={busy || !input.trim()}
          className="mt-1 w-full rounded bg-mars-accent py-1 text-[11px] font-medium text-white hover:bg-mars-accent2 disabled:opacity-50"
        >
          {busy ? t("chat.thinking") : t("chat.send")}
        </button>
      </div>

      {err ? <p className="px-2 pt-1 text-[10px] text-red-300">{err}</p> : null}

      {/* messages / history below the input */}
      <div ref={scrollRef} className="flex-1 min-h-0 space-y-2 overflow-auto p-2">
        {!conv ? (
          <p className="text-[11px] text-slate-500">{t("common.loading")}</p>
        ) : conv.messages.length === 0 ? (
          <p className="text-[11px] leading-relaxed text-slate-500">{t("chat.hello")}</p>
        ) : (
          conv.messages.map((m, i) => <Bubble key={i} m={m} />)
        )}
        {busy ? (
          <div className="text-[10px] text-slate-500">🧭 {t("chat.thinking")}</div>
        ) : null}
      </div>
    </div>
  );
}

function Bubble({ m }: { m: ChatMessageView }): JSX.Element {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  if (m.role === "tool") {
    const ok = (m.tool_result as { ok?: boolean } | null)?.ok ?? true;
    return (
      <div className="rounded border border-mars-border/60 bg-mars-panel/60 px-2 py-1 text-[9px]">
        <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between text-left">
          <span className={ok ? "text-emerald-300" : "text-red-300"}>
            🔧 {m.tool_name} {ok ? "✓" : "✗"}
          </span>
          <span className="text-slate-500">{open ? "▴" : "▾"}</span>
        </button>
        {open ? (
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-[9px] text-slate-400">
            {JSON.stringify(m.tool_result, null, 2)}
          </pre>
        ) : (
          <p className="mt-0.5 truncate text-slate-500">{m.content}</p>
        )}
      </div>
    );
  }

  const isUser = m.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[90%] rounded-lg px-2 py-1 text-[11px] leading-relaxed ${
          isUser
            ? "bg-mars-accent/30 text-slate-100"
            : "bg-mars-panel2 text-slate-200"
        }`}
      >
        {!isUser ? <span className="mr-1 text-[9px] text-slate-500">🧭</span> : null}
        {m.content}
      </div>
    </div>
  );
}
