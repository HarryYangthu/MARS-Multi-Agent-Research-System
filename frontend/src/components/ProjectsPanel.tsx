"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import {
  deleteRun,
  getStats,
  listRuns,
  listTrashedRuns,
  permanentlyDeleteRun,
  restoreRun,
  type RunSummary,
  type Stats,
  type TrashRunSummary,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useProject } from "@/lib/project";

export function ProjectsPanel({ onSelectRun }: { onSelectRun?: (runId: string) => void }): JSX.Element {
  const { t } = useI18n();
  const { selectedProject } = useProject();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [trashedRuns, setTrashedRuns] = useState<TrashRunSummary[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [showTrash, setShowTrash] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const [r, tr, s] = await Promise.all([
          listRuns(selectedProject),
          listTrashedRuns(selectedProject),
          getStats(),
        ]);
        if (alive) {
          setRuns(r.reverse());
          setTrashedRuns(tr.reverse());
          setStats(s);
          setError("");
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    };
    void refresh();
    const iv = setInterval(refresh, 4000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [selectedProject]);

  const refreshRuns = async () => {
    const [r, tr, s] = await Promise.all([
      listRuns(selectedProject),
      listTrashedRuns(selectedProject),
      getStats(),
    ]);
    setRuns(r.reverse());
    setTrashedRuns(tr.reverse());
    setStats(s);
    setError("");
  };

  const waitingByRun = new Map<string, string>(
    (stats?.waiting_review_runs ?? []).map((w) => [w.run_id, w.agent]),
  );

  return (
    <aside className="flex h-full min-h-0 flex-col gap-2 overflow-hidden border-r border-mars-border bg-mars-panel/60 p-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">📋 {t("sidebar.projects")}</h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className={`rounded px-1.5 py-0.5 text-[11px] transition ${
              showTrash
                ? "bg-rose-500/20 text-rose-200"
                : "bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-slate-200"
            }`}
            onClick={() => setShowTrash((v) => !v)}
            title={showTrash ? t("run.active") : t("run.trash")}
          >
            {showTrash ? "↩" : "🗑"} {showTrash ? runs.length : trashedRuns.length}
          </button>
          <span className="text-[10px] text-slate-500">
            {selectedProject} · {runs.length} · {stats?.runs_running ?? 0} {t("sidebar.running")}
          </span>
        </div>
      </div>
      {error ? (
        <p className="rounded border border-rose-500/30 bg-rose-500/10 p-2 text-[10px] text-rose-200">
          {error}
        </p>
      ) : null}

      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
        {showTrash ? (
          trashedRuns.length === 0 ? (
            <p className="rounded border border-dashed border-mars-border p-2 text-center text-[10px] text-slate-500">
              {t("run.trash.empty")}
            </p>
          ) : (
            trashedRuns.map((r) => (
              <TrashRunCard
                key={r.run_id}
                run={r}
                onPermanentDelete={refreshRuns}
                onRestore={refreshRuns}
              />
            ))
          )
        ) : runs.length === 0 ? (
          <p className="rounded border border-dashed border-mars-border p-2 text-center text-[10px] text-slate-500">
            {t("sidebar.no_runs")}
          </p>
        ) : (
          runs.map((r, index) => (
            <RunCard
              key={`${r.run_id}-${index}`}
              run={r}
              onDeleted={refreshRuns}
              onSelect={onSelectRun}
              waitingAgent={waitingByRun.get(r.run_id)}
            />
          ))
        )}
      </div>
    </aside>
  );
}

function RunCard({
  run,
  onDeleted,
  onSelect,
  waitingAgent,
}: {
  run: RunSummary;
  onDeleted: () => Promise<void>;
  onSelect?: (runId: string) => void;
  waitingAgent?: string;
}): JSX.Element {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const isWaiting = !!waitingAgent;
  const handleDelete = async () => {
    if (busy) return;
    if (!window.confirm(t("run.delete.confirm"))) return;
    setBusy(true);
    try {
      await deleteRun(run.run_id);
      await onDeleted();
    } catch (err) {
      window.alert(`${t("run.delete.failed")}: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      onClick={() => onSelect?.(run.run_id)}
      className={`group cursor-pointer rounded border p-2 transition ${
        isWaiting
          ? "border-fuchsia-500/50 bg-fuchsia-500/10 hover:border-fuchsia-400"
          : "border-mars-border bg-mars-panel2 hover:border-mars-accent"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-slate-200">▶ {run.task}</span>
        <div className="flex shrink-0 items-center gap-1">
          {isWaiting ? (
            <span className="animate-pulse rounded bg-fuchsia-500/30 px-1.5 py-0.5 text-[9px] text-fuchsia-100">
              🔔 {t("state.waiting_review")}
            </span>
          ) : (
            <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] text-emerald-300">
              {t("state.running")}
            </span>
          )}
          <button
            type="button"
            aria-label={t("run.delete")}
            className="rounded p-0.5 text-slate-500 opacity-0 transition hover:bg-rose-500/20 hover:text-rose-200 group-hover:opacity-100 focus:opacity-100"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              void handleDelete();
            }}
            title={t("run.delete")}
          >
            🗑
          </button>
        </div>
      </div>
      <p className="mt-1 truncate text-[10px] text-slate-500">
        {run.run_id} · {run.project}
      </p>
      <div className="mt-1.5 flex gap-1 text-[10px]">
        <Link
          href={`/runs/${run.run_id}`}
          className="rounded bg-cyan-500/20 px-2 py-0.5 text-cyan-300 hover:bg-cyan-500/30"
          onClick={(e) => e.stopPropagation()}
        >
          {t("run.detail")}
        </Link>
      </div>
    </div>
  );
}

function TrashRunCard({
  run,
  onPermanentDelete,
  onRestore,
}: {
  run: TrashRunSummary;
  onPermanentDelete: () => Promise<void>;
  onRestore: () => Promise<void>;
}): JSX.Element {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const daysLeft = t("run.trash.daysLeft").replace("{days}", String(run.days_remaining));

  const handleRestore = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await restoreRun(run.run_id);
      await onRestore();
    } catch (err) {
      window.alert(`${t("run.restore.failed")}: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const handlePermanentDelete = async () => {
    if (busy) return;
    if (!window.confirm(t("run.permanentDelete.confirm"))) return;
    setBusy(true);
    try {
      await permanentlyDeleteRun(run.run_id);
      await onPermanentDelete();
    } catch (err) {
      window.alert(
        `${t("run.permanentDelete.failed")}: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded border border-rose-500/30 bg-rose-500/10 p-2">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-slate-200">🗑 {run.task}</span>
        <span className="shrink-0 rounded bg-rose-500/20 px-1.5 py-0.5 text-[9px] text-rose-200">
          {daysLeft}
        </span>
      </div>
      <p className="mt-1 truncate text-[10px] text-slate-500">
        {run.run_id} · {run.project}
      </p>
      <p className="mt-1 text-[10px] text-slate-500">{t("run.trash.retention")}</p>
      <div className="mt-1.5 flex gap-1 text-[10px]">
        <button
          type="button"
          className="rounded bg-emerald-500/20 px-2 py-0.5 text-emerald-200 hover:bg-emerald-500/30 disabled:opacity-50"
          disabled={busy}
          onClick={() => void handleRestore()}
        >
          {t("run.restore")}
        </button>
        <button
          type="button"
          className="rounded bg-rose-500/20 px-2 py-0.5 text-rose-200 hover:bg-rose-500/30 disabled:opacity-50"
          disabled={busy}
          onClick={() => void handlePermanentDelete()}
        >
          {t("run.permanentDelete")}
        </button>
      </div>
    </div>
  );
}
