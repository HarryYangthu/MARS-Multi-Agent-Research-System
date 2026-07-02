"use client";

import { useEffect, useMemo, useState } from "react";

import { getRunTimeline, type TimelineItem } from "@/lib/api";

type TimelinePanelProps = {
  runId: string;
  compact?: boolean;
};

const KIND_CLASS: Record<string, string> = {
  langgraph: "border-cyan-500/40 bg-cyan-500/10 text-cyan-100",
  reporting: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100",
  hitl: "border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-100",
  tool: "border-amber-500/40 bg-amber-500/10 text-amber-100",
  trace_span: "border-sky-500/40 bg-sky-500/10 text-sky-100",
  evaluation: "border-violet-500/40 bg-violet-500/10 text-violet-100",
  context_manifest: "border-slate-500/40 bg-slate-500/10 text-slate-100",
};

export function TimelinePanel({ runId, compact = false }: TimelinePanelProps): JSX.Element {
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [kind, setKind] = useState<string>("all");
  const [message, setMessage] = useState<string>("");

  useEffect(() => {
    let alive = true;
    const refresh = (): void => {
      void getRunTimeline(runId)
        .then((next) => {
          if (!alive) return;
          setItems(next);
          setMessage("");
        })
        .catch((error) => {
          if (alive) setMessage(error instanceof Error ? error.message : "timeline load failed");
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [runId]);

  const kinds = useMemo(() => {
    const unique = Array.from(new Set(items.map((item) => item.kind))).sort();
    return ["all", ...unique];
  }, [items]);
  const visible = kind === "all" ? items : items.filter((item) => item.kind === kind);

  return (
    <section className="rounded border border-mars-border bg-mars-bg/50">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-mars-border px-3 py-2.5">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">执行流</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">
            LangGraph 状态、工具调用、HITL、报告生成与审计摘要
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {kinds.map((nextKind) => (
            <button
              key={nextKind}
              onClick={() => setKind(nextKind)}
              className={`rounded border px-2 py-1 text-[11px] ${
                kind === nextKind
                  ? "border-mars-accent bg-mars-accent text-white"
                  : "border-mars-border bg-mars-panel2 text-slate-300 hover:bg-mars-subtle"
              }`}
            >
              {nextKind}
            </button>
          ))}
        </div>
      </div>
      {message ? <p className="px-3 py-2 text-xs text-red-300">{message}</p> : null}
      <ol className={`${compact ? "max-h-[420px]" : "max-h-[68vh]"} overflow-auto p-3`}>
        {visible.length ? (
          visible.map((item) => <TimelineRow key={item.id} item={item} />)
        ) : (
          <li className="rounded border border-dashed border-mars-border px-3 py-8 text-center text-sm text-slate-500">
            暂无 timeline 事件
          </li>
        )}
      </ol>
    </section>
  );
}

function TimelineRow({ item }: { item: TimelineItem }): JSX.Element {
  const tone = KIND_CLASS[item.kind] ?? "border-mars-border bg-mars-panel/60 text-slate-100";
  const time = formatTime(item.timestamp);
  return (
    <li className="relative border-l border-mars-border pb-3 pl-4 last:pb-0">
      <span className="absolute -left-[5px] top-2 h-2.5 w-2.5 rounded-full border border-mars-border bg-mars-accent" />
      <div className="rounded border border-mars-border bg-mars-panel/70 p-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded border px-1.5 py-0.5 text-[10px] ${tone}`}>
                {item.kind}
              </span>
              {item.status ? (
                <span className="rounded bg-mars-bg px-1.5 py-0.5 text-[10px] text-slate-300">
                  {item.status}
                </span>
              ) : null}
              <h4 className="break-words text-sm font-semibold text-slate-100">{item.title}</h4>
            </div>
            {item.summary ? <p className="mt-1 text-xs text-slate-400">{item.summary}</p> : null}
          </div>
          <span className="font-mono text-[10px] text-slate-500">{time}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] text-slate-400">
          {item.agent ? <span className="rounded bg-mars-bg px-1.5 py-0.5">agent={item.agent}</span> : null}
          {item.node ? <span className="rounded bg-mars-bg px-1.5 py-0.5">node={item.node}</span> : null}
          <span className="rounded bg-mars-bg px-1.5 py-0.5">source={item.source}</span>
        </div>
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">
            审计证据
          </summary>
          <pre className="mt-2 max-h-56 overflow-auto rounded bg-black/30 p-2 text-[11px] leading-relaxed text-slate-300">
            {JSON.stringify(sanitizePayload(item.payload), null, 2)}
          </pre>
        </details>
      </div>
    </li>
  );
}

function sanitizePayload(payload: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload)) {
    const lowered = key.toLowerCase();
    if (
      lowered.includes("chain_of_thought") ||
      lowered === "cot" ||
      lowered.includes("private_reasoning")
    ) {
      out[key] = "[redacted]";
      continue;
    }
    out[key] = value;
  }
  return out;
}

function formatTime(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString();
}

