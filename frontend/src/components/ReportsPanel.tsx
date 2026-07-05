"use client";

import { useEffect, useMemo, useState } from "react";

import {
  getReportBundle,
  regenerateReportBundle,
  reportFileUrl,
  type ReportBundle,
  type ReportDeliverable,
} from "@/lib/api";

export function ReportsPanel({ runId }: { runId: string }): JSX.Element {
  const [bundle, setBundle] = useState<ReportBundle | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const refresh = useMemo(
    () => async (): Promise<void> => {
      const next = await getReportBundle(runId);
      setBundle(next);
    },
    [runId],
  );

  useEffect(() => {
    let alive = true;
    void refresh().catch((error) => {
      if (alive) setMessage(error instanceof Error ? error.message : "reports load failed");
    });
    return () => {
      alive = false;
    };
  }, [refresh]);

  async function regenerate(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      const next = await regenerateReportBundle(runId);
      setBundle(next);
      setMessage("报告包已重新生成");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "regenerate failed");
    } finally {
      setBusy(false);
    }
  }

  const metadata = bundle?.metadata;
  const deliverables = metadata?.deliverables ?? [];
  const qa = metadata?.qa_status;

  return (
    <section className="rounded border border-mars-border bg-mars-bg/50">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-mars-border px-3 py-2.5">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">报告产物</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">
            Markdown、Excel、Word、PPT 与 QA manifest
          </p>
        </div>
        <button
          onClick={regenerate}
          disabled={busy}
          className="rounded border border-mars-accent/60 bg-mars-accent/15 px-3 py-1.5 text-xs font-medium text-cyan-100 hover:bg-mars-accent/25 disabled:opacity-50"
        >
          {busy ? "生成中..." : "重新生成"}
        </button>
      </div>
      {message ? <p className="border-b border-mars-border px-3 py-2 text-xs text-slate-300">{message}</p> : null}
      {!bundle?.exists ? (
        <div className="p-4">
          <div className="rounded border border-dashed border-mars-border bg-mars-panel/40 p-6 text-center">
            <p className="text-sm text-slate-300">当前 run 还没有 report bundle。</p>
            <button
              onClick={regenerate}
              disabled={busy}
              className="mt-3 rounded bg-mars-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              生成报告包
            </button>
          </div>
        </div>
      ) : (
        <div className="grid gap-3 p-3 xl:grid-cols-[1.1fr,0.9fr]">
          <div className="space-y-3">
            <div className="rounded border border-mars-border bg-mars-panel/60 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs uppercase tracking-wider text-slate-500">Manifest</p>
                  <p className="mt-1 font-mono text-xs text-slate-300">{bundle.manifest}</p>
                </div>
                <span className={`rounded border px-2 py-1 text-xs ${qaTone(qa?.status ?? "")}`}>
                  {qa?.status ?? "unknown"}
                </span>
              </div>
              {metadata?.data_pack ? (
                <p className="mt-2 font-mono text-[11px] text-slate-500">data_pack={metadata.data_pack}</p>
              ) : null}
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {deliverables.map((item) => (
                <DeliverableCard key={`${item.kind}:${item.path}`} runId={runId} item={item} />
              ))}
            </div>
          </div>
          <div className="space-y-3">
            <div className="rounded border border-mars-border bg-mars-panel/60 p-3">
              <h4 className="text-sm font-semibold text-slate-100">QA 检查</h4>
              <ol className="mt-2 space-y-1.5">
                {(qa?.checks ?? []).map((check) => (
                  <li key={`${check.name}:${check.detail ?? ""}`} className="flex items-start justify-between gap-2 rounded bg-mars-bg/70 px-2 py-1.5 text-xs">
                    <span className="min-w-0">
                      <span className="font-medium text-slate-200">{check.name}</span>
                      {check.detail ? <span className="ml-2 text-slate-500">{check.detail}</span> : null}
                    </span>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 ${qaTone(check.status)}`}>
                      {check.status}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
            <div className="rounded border border-mars-border bg-mars-panel/60 p-3">
              <h4 className="text-sm font-semibold text-slate-100">Bundle 摘要</h4>
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-[11px] leading-relaxed text-slate-300">
                {bundle.body ?? ""}
              </pre>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function DeliverableCard({ runId, item }: { runId: string; item: ReportDeliverable }): JSX.Element {
  const filename = item.path.split("/").pop() ?? item.path;
  const completed = item.status === "completed";
  return (
    <div className="rounded border border-mars-border bg-mars-panel/60 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-100">{item.kind}</p>
          <p className="mt-1 break-all font-mono text-[11px] text-slate-500">{item.path}</p>
        </div>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] ${qaTone(item.status)}`}>
          {item.status}
        </span>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-[11px] text-slate-500">{item.bytes ? `${item.bytes} bytes` : item.error ?? ""}</span>
        {completed ? (
          <a
            href={reportFileUrl(runId, filename)}
            className="rounded border border-mars-border bg-mars-bg px-2 py-1 text-xs text-slate-200 hover:bg-mars-subtle"
          >
            下载
          </a>
        ) : null}
      </div>
    </div>
  );
}

function qaTone(status: string): string {
  if (status === "passed" || status === "completed") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100";
  }
  if (status === "failed") {
    return "border-red-500/40 bg-red-500/10 text-red-100";
  }
  if (status === "skipped") {
    return "border-slate-500/40 bg-slate-500/10 text-slate-300";
  }
  return "border-amber-500/40 bg-amber-500/10 text-amber-100";
}
