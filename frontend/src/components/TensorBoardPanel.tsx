"use client";

import { useEffect, useState } from "react";
import { openTensorBoard, type TensorBoardSession } from "@/lib/tensorboard";

export function TensorBoardPanel({ project, runId, fullHeight = false }: {
  project: string; runId?: string; fullHeight?: boolean;
}): JSX.Element {
  const [session, setSession] = useState<TensorBoardSession | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setSession(null); setError(""); setLoading(true);
    // TensorBoard's persistent-settings contract; keep the user's other chart settings.
    try {
      const stored: unknown = JSON.parse(localStorage.getItem("_tb_global_settings") || "{}");
      const settings = stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
      localStorage.setItem("_tb_global_settings", JSON.stringify({ ...settings, autoReload: true, autoReloadPeriodInMs: 30000 }));
    } catch { /* TensorBoard's own Reload control remains available. */ }
    void openTensorBoard(project, runId).then((result) => {
      if (alive) setSession(result);
    }).catch((caught: unknown) => {
      if (alive) setError(caught instanceof Error ? caught.message : String(caught));
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [project, runId, retry]);
  return (
    <section className={`flex min-h-0 flex-col overflow-hidden rounded-xl border border-mars-border bg-mars-panel ${fullHeight ? "h-full" : "h-[720px]"}`}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-mars-border px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">TensorBoard · 实验过程</h2>
          <p className="mt-1 text-xs text-slate-400">{runId ? "当前运行的训练指标与实验结果；首条指标写入后即可查看。" : "项目已保存的实验记录，可在 TensorBoard 中选择实验进行对比。"}</p>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-slate-400">每 30 秒更新</span>
          {session ? <a href={session.url + "#scalars"} target="_blank" rel="noreferrer" className="text-mars-accent hover:underline">独立打开 ↗</a> : null}
          <button className="rounded border border-mars-border px-3 py-1.5 hover:bg-mars-subtle" onClick={() => setRetry((value) => value + 1)}>{error ? "重试" : "重新加载"}</button>
        </div>
      </header>
      {loading ? <div role="status" className="flex flex-1 items-center justify-center text-sm text-slate-400">正在打开 TensorBoard…</div>
        : error ? <div role="alert" className="flex flex-1 items-center justify-center p-8 text-sm text-amber-200">{error}</div>
        : session ? <iframe key={`${session.key}-${retry}`} src={session.url + "#scalars"} title={runId ? `TensorBoard · ${runId}` : "TensorBoard · 项目实验记录"} className="min-h-0 w-full flex-1 border-0 bg-white" /> : null}
    </section>
  );
}
