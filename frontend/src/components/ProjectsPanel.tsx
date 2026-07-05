"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import {
  getStats,
  listRuns,
  type RunSummary,
  type Stats,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useProject } from "@/lib/project";

export function ProjectsPanel({ onSelectRun }: { onSelectRun?: (runId: string) => void }): JSX.Element {
  const { t } = useI18n();
  const { selectedProject } = useProject();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const [r, s] = await Promise.all([listRuns(selectedProject), getStats()]);
        if (alive) {
          setRuns(r.reverse());
          setStats(s);
        }
      } catch {
        /* ignore */
      }
    };
    void refresh();
    const iv = setInterval(refresh, 4000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [selectedProject]);

  const waitingByRun = new Map<string, string>(
    (stats?.waiting_review_runs ?? []).map((w) => [w.run_id, w.agent]),
  );

  return (
    <aside className="flex h-full min-h-0 flex-col gap-2 overflow-hidden border-r border-mars-border bg-mars-panel/60 p-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">📋 {t("sidebar.projects")}</h2>
        <span className="text-[10px] text-slate-500">
          {selectedProject} · {runs.length} · {stats?.runs_running ?? 0} {t("sidebar.running")}
        </span>
      </div>

      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
        {runs.length === 0 ? (
          <p className="rounded border border-dashed border-mars-border p-2 text-center text-[10px] text-slate-500">
            {t("sidebar.no_runs")}
          </p>
        ) : (
          runs.map((r, index) => (
            <RunCard
              key={`${r.run_id}-${index}`}
              run={r}
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
  onSelect,
  waitingAgent,
}: {
  run: RunSummary;
  onSelect?: (runId: string) => void;
  waitingAgent?: string;
}): JSX.Element {
  const { t } = useI18n();
  const isWaiting = !!waitingAgent;
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
        {isWaiting ? (
          <span className="animate-pulse rounded bg-fuchsia-500/30 px-1.5 py-0.5 text-[9px] text-fuchsia-100">
            🔔 {t("state.waiting_review")}
          </span>
        ) : (
          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] text-emerald-300">
            {t("state.running")}
          </span>
        )}
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
