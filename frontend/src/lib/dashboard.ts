"use client";

import { useEffect, useMemo, useState } from "react";

import {
  getReadiness,
  getStats,
  listRuns,
  type Readiness,
  type RunSummary,
  type Stats,
} from "@/lib/api";

export type DashboardSnapshot = {
  stats: Stats | null;
  readiness: Readiness | null;
  runs: RunSummary[];
  waitingRuns: { run_id: string; task: string; agent: string }[];
  loading: boolean;
  error: string;
  lastUpdated: Date | null;
};

export type RuntimeSnapshot = Omit<DashboardSnapshot, "runs" | "waitingRuns"> & {
  waitingRuns: { run_id: string; task: string; agent: string }[];
};

export function useRuntimeSnapshot(project: string, refreshMs = 4000): RuntimeSnapshot {
  const [stats, setStats] = useState<Stats | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    let alive = true;

    const refresh = async (): Promise<void> => {
      try {
        const [nextStats, nextReadiness] = await Promise.all([
          getStats(),
          getReadiness(project),
        ]);
        if (!alive) return;
        setStats(nextStats);
        setReadiness(nextReadiness);
        setError("");
        setLastUpdated(new Date());
      } catch (caught) {
        if (!alive) return;
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        if (alive) setLoading(false);
      }
    };

    void refresh();
    const interval = setInterval(refresh, refreshMs);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [project, refreshMs]);

  return useMemo(
    () => ({
      stats,
      readiness,
      waitingRuns: stats?.waiting_review_runs ?? [],
      loading,
      error,
      lastUpdated,
    }),
    [error, lastUpdated, loading, readiness, stats],
  );
}

export function useDashboardSnapshot(
  project: string,
  refreshMs = 4000,
): DashboardSnapshot {
  const runtime = useRuntimeSnapshot(project, refreshMs);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState("");

  useEffect(() => {
    let alive = true;

    const refresh = async (): Promise<void> => {
      try {
        const nextRuns = await listRuns(project);
        if (!alive) return;
        setRuns([...nextRuns].reverse());
        setRunsError("");
      } catch (caught) {
        if (!alive) return;
        setRunsError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        if (alive) setRunsLoading(false);
      }
    };

    void refresh();
    const interval = setInterval(refresh, refreshMs);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [project, refreshMs]);

  return useMemo(
    () => ({
      ...runtime,
      runs,
      loading: runtime.loading || runsLoading,
      error: runtime.error || runsError,
    }),
    [runs, runsError, runsLoading, runtime],
  );
}
