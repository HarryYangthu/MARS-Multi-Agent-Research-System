"use client";

import { useState } from "react";

import { sendFeedback } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export function HumanFeedback({ runId }: { runId?: string | null }): JSX.Element {
  const { t } = useI18n();
  const [v, setV] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function send(): Promise<void> {
    if (!v.trim()) return;
    if (!runId) {
      setErr(t("feedback.no_run"));
      setTimeout(() => setErr(null), 1500);
      return;
    }
    try {
      await sendFeedback(runId, v);
      setSent(v);
      setV("");
      setTimeout(() => setSent(null), 1500);
    } catch (e) {
      setErr(String(e));
      setTimeout(() => setErr(null), 2500);
    }
  }

  return (
    <div className="flex items-center gap-2 border-t border-mars-border bg-mars-panel/80 px-4 py-2 text-xs">
      <span className="text-slate-400">💬 {t("feedback.title")}</span>
      <input
        value={v}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && void send()}
        placeholder={runId ? t("feedback.placeholder") : t("feedback.no_run")}
        className="flex-1 rounded border border-mars-border bg-mars-bg/60 px-2 py-1 text-xs text-slate-200 outline-none focus:border-mars-accent"
      />
      {err ? (
        <span className="text-[10px] text-rose-300">{err}</span>
      ) : sent ? (
        <span className="text-[10px] text-emerald-300">✓ {t("feedback.sent_ok")}</span>
      ) : (
        <button
          onClick={() => void send()}
          className="rounded bg-mars-accent px-2 py-0.5 text-[10px] text-white hover:bg-mars-accent2"
        >
          {t("feedback.send")}
        </button>
      )}
    </div>
  );
}
