"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";

import {
  executionPlotUrl,
  listExecutionPlots,
  type ExecutionPlot,
} from "@/lib/api";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

type Curve = {
  experiment_id: string;
  metric: string;
  values: number[];
};

export default function MultiExperimentView({
  params,
}: {
  params: Promise<{ id: string }>;
}): JSX.Element {
  const { id: runId } = use(params);
  const [curves, setCurves] = useState<Curve[]>([]);
  const [plots, setPlots] = useState<ExecutionPlot[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [summary, setSummary] = useState<{
    experiments: string[];
    failures: [string, string][];
    max_concurrency?: number;
    attempt?: number;
    total: number;
  } | null>(null);

  useEffect(() => {
    let alive = true;
    async function loadAll(): Promise<void> {
      try {
        const names = await fetch(`${BASE}/api/execution/${runId}/curves`).then((r) => r.json());
        const out: Curve[] = await Promise.all(
          (names as string[]).map((n) =>
            fetch(`${BASE}/api/execution/${runId}/curves/${n}`).then((r) => r.json()),
          ),
        );
        if (!alive) return;
        setCurves(out);
        const nextPlots = await listExecutionPlots(runId).catch(() => []);
        if (alive) setPlots(nextPlots);
        const sum = await fetch(`${BASE}/api/execution/${runId}/summary`).then((r) => r.json());
        if (alive) setSummary(sum);
      } catch (e) {
        if (alive) setErr(String(e));
      }
    }
    void loadAll();
    const iv = setInterval(loadAll, 2000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [runId]);

  return (
    <main className="container mx-auto max-w-7xl px-6 py-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">多实验仿真视图</h1>
          <p className="text-sm text-slate-400">
            Run <code>{runId}</code> · {summary?.total ?? 0} 组实验
            {summary?.max_concurrency ? ` · 最大并发 ${summary.max_concurrency}` : ""}
            {summary?.attempt ? ` · 第 ${summary.attempt} 轮执行` : ""}
          </p>
        </div>
        <Link href={`/runs/${runId}`} className="text-xs text-slate-400 hover:text-slate-200">
          &larr; 返回 Run
        </Link>
      </header>

      {err ? <p className="text-sm text-red-300">{err}</p> : null}

      {plots.length > 0 ? (
        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase text-slate-400">
              实时训练图
            </h2>
            <span className="text-[11px] text-slate-500">
              自动刷新 PNG 快照
            </span>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {plots.map((plot) => (
              <LivePlotPanel key={plot.filename} plot={plot} />
            ))}
          </div>
        </section>
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {curves.map((c) => (
          <CurvePanel key={c.experiment_id} curve={c} />
        ))}
      </div>

      {summary?.failures && summary.failures.length > 0 ? (
        <section className="mt-8">
          <h2 className="text-sm font-semibold uppercase text-slate-400">失败实验</h2>
          <ul className="mt-2 space-y-1 text-sm text-red-300">
            {summary.failures.map(([id, reason]) => (
              <li key={id}>
                <span className="font-mono">{id}</span>: {reason}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </main>
  );
}

function LivePlotPanel({ plot }: { plot: ExecutionPlot }): JSX.Element {
  return (
    <figure className="overflow-hidden rounded border border-mars-border bg-mars-panel">
      <div className="flex items-center justify-between border-b border-mars-border px-4 py-2">
        <figcaption className="truncate text-sm font-medium text-slate-100">
          {plot.experiment_id}
        </figcaption>
        <span className="font-mono text-[10px] text-slate-500">
          {plot.metric} · {new Date(plot.updated_at * 1000).toLocaleTimeString()}
        </span>
      </div>
      <img
        src={executionPlotUrl(plot)}
        alt={`${plot.experiment_id} live ${plot.metric} plot`}
        className="w-full bg-white object-contain"
      />
    </figure>
  );
}

function CurvePanel({ curve }: { curve: Curve }): JSX.Element {
  const max = Math.max(...curve.values, 0.0001);
  const min = Math.min(...curve.values, 0);
  const range = max - min || 1;
  const w = 280;
  const h = 120;
  const path = curve.values
    .map((v, i) => {
      const x = (i / Math.max(1, curve.values.length - 1)) * w;
      const y = h - ((v - min) / range) * h;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <div className="rounded border border-mars-border bg-mars-panel p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{curve.experiment_id}</h3>
        <span className="text-[10px] uppercase text-slate-500">{curve.metric}</span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="mt-2 h-32 w-full">
        <path d={path} fill="none" stroke="#6366f1" strokeWidth={1.5} />
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-slate-500">
        <span>{min.toFixed(3)}</span>
        <span>n={curve.values.length}</span>
        <span>{max.toFixed(3)}</span>
      </div>
    </div>
  );
}
