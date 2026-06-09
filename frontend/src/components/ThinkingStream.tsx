"use client";

import { useEffect, useRef } from "react";

/** Live LLM thinking: streamed chain-of-thought (reasoning) + answer (content). */
export function ThinkingStream({
  reasoning,
  content,
  active,
}: {
  reasoning: string;
  content: string;
  active: boolean;
}): JSX.Element {
  const reasonRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    reasonRef.current?.scrollTo({ top: reasonRef.current.scrollHeight });
  }, [reasoning]);
  useEffect(() => {
    contentRef.current?.scrollTo({ top: contentRef.current.scrollHeight });
  }, [content]);

  if (!reasoning && !content) {
    return (
      <p className="p-4 text-xs text-slate-500">
        {active ? "等待模型开始思考…" : "本步骤暂无可回放的思考过程。"}
      </p>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-3">
      <div className="flex min-h-0 flex-[2] flex-col">
        <div className="mb-1 flex items-center gap-2 text-[11px] text-amber-300">
          <span>🧠 思维链 (reasoning)</span>
          {active ? (
            <span className="animate-pulse rounded bg-amber-500/20 px-1.5 py-0.5 text-[9px]">
              ● 实时
            </span>
          ) : null}
        </div>
        <div
          ref={reasonRef}
          className="min-h-0 flex-1 overflow-auto rounded border border-amber-500/20 bg-amber-500/[0.03] p-2 font-mono text-[11px] leading-relaxed text-amber-100/80 whitespace-pre-wrap"
        >
          {reasoning || "…"}
          {active && reasoning ? <span className="animate-pulse">▌</span> : null}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="mb-1 text-[11px] text-emerald-300">📝 输出 (content)</div>
        <div
          ref={contentRef}
          className="min-h-0 flex-1 overflow-auto rounded border border-emerald-500/20 bg-emerald-500/[0.03] p-2 font-mono text-[11px] leading-relaxed text-slate-200 whitespace-pre-wrap"
        >
          {content}
          {active && content ? <span className="animate-pulse">▌</span> : null}
        </div>
      </div>
    </div>
  );
}
