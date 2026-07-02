"use client";

import { useState } from "react";

import { useI18n } from "@/lib/i18n";

export function HumanFeedback(): JSX.Element {
  const { t } = useI18n();
  const [v, setV] = useState("");
  const [sent, setSent] = useState<string | null>(null);

  function send(): void {
    if (!v.trim()) return;
    // V0 just stores locally; V2 will POST to a feedback endpoint.
    try {
      const key = "mars.feedback.log";
      const prev = window.localStorage.getItem(key);
      const list = prev ? JSON.parse(prev) : [];
      list.push({ ts: new Date().toISOString(), text: v });
      window.localStorage.setItem(key, JSON.stringify(list.slice(-50)));
      setSent(v);
      setV("");
      setTimeout(() => setSent(null), 1500);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="flex items-center gap-2 border-t border-mars-border bg-mars-panel/80 px-4 py-2 text-xs">
      <span className="text-slate-400">💬 {t("feedback.title")}</span>
      <input
        value={v}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && send()}
        placeholder={t("feedback.placeholder")}
        className="flex-1 rounded border border-mars-border bg-mars-bg/60 px-2 py-1 text-xs text-slate-200 outline-none focus:border-mars-accent"
      />
      {sent ? (
        <span className="text-[10px] text-emerald-300">✓ saved</span>
      ) : (
        <span className="text-[10px] text-slate-500">▸ V2: 发送给当前活跃 Agent</span>
      )}
    </div>
  );
}
