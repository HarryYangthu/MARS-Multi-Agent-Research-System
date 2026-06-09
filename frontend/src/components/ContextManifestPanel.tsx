"use client";

import { useEffect, useState } from "react";

import { getContextManifest, type ContextManifest, type Stage } from "@/lib/api";

/** Read-only view of the context a given agent loaded for its LLM call. */
export function ContextManifestPanel({
  runId,
  stage,
  refreshKey,
}: {
  runId: string;
  stage: Stage;
  refreshKey?: string;
}): JSX.Element {
  const [m, setM] = useState<ContextManifest | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getContextManifest(runId, stage)
      .then((d) => alive && setM(d))
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [runId, stage, refreshKey]);

  if (err) return <p className="p-4 text-xs text-rose-300">{err}</p>;
  if (!m) return <p className="p-4 text-xs text-slate-500">加载中…</p>;
  if (!m.exists) {
    return (
      <p className="p-4 text-xs text-slate-500">
        该 Agent 尚未生成 ContextManifest(运行后产生)。
      </p>
    );
  }

  const summary = m.summary ?? {};
  return (
    <div className="space-y-3 overflow-auto p-3 text-xs text-slate-300">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-mars-accent/20 px-2 py-0.5 font-mono text-mars-accent">
          ~{m.tokens_estimated} tokens
        </span>
        <span className="text-[10px] text-slate-500">{m.timestamp}</span>
      </div>
      <p className="text-[11px] text-slate-500">
        这是本次调用真实装载进 LLM 的上下文清单(3 层:system / project / task)。
      </p>
      {Object.entries(summary).map(([layer, val]) => (
        <div key={layer} className="rounded border border-mars-border bg-mars-panel2 p-2">
          <div className="mb-1 font-mono text-[11px] text-slate-200">{layer}</div>
          <pre className="overflow-auto whitespace-pre-wrap text-[10px] text-slate-400">
            {typeof val === "string" ? val : JSON.stringify(val, null, 2)}
          </pre>
        </div>
      ))}
    </div>
  );
}
