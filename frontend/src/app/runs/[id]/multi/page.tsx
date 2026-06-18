"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import {
  executionPlotUrl,
  listExecutionPlots,
  type ExecutionPlot,
} from "@/lib/api";
import { openRunSocket } from "@/lib/socket";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

// RES (residual, dB) gate from projects/moe-pimc/diagnostics.yaml — lower is
// better. A batch whose mean RES is above this missed the target.
const RES_GATE_DB = -26.0;

type Curve = {
  experiment_id: string;
  metric: string;
  values: number[];
};

type LiveCurve = {
  experiment_id: string;
  attempt: number;
  values: number[];
  done: boolean;
};

function resFromLoss(loss: number): number {
  return 10 * Math.log10(Math.max(loss, 1e-12));
}

function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}

export default function MultiExperimentView({
  params,
}: {
  params: Promise<{ id: string }>;
}): JSX.Element {
  const { id: runId } = use(params);
  const [restCurves, setRestCurves] = useState<Curve[]>([]);
  const [plots, setPlots] = useState<ExecutionPlot[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [summary, setSummary] = useState<{
    experiments: string[];
    failures: [string, string][];
    max_concurrency?: number;
    attempt?: number;
    total: number;
  } | null>(null);

  // Live, per-(attempt,experiment) curves accumulated from the WS stream.
  const liveRef = useRef<Map<string, LiveCurve>>(new Map());
  const [liveCurves, setLiveCurves] = useState<LiveCurve[]>([]);

  // --- live stream over the consolidated run.{id}.execution channel ---
  useEffect(() => {
    const close = openRunSocket(runId, (msg) => {
      if (msg.channel !== `run.${runId}.execution`) return;
      const p = msg.payload as Record<string, unknown>;
      const event = String(p.event ?? "");
      const expId = String(p.experiment_id ?? "");
      if (!expId) return;
      const attempt = Number(p.attempt ?? 1) || 1;
      const key = `${attempt}::${expId}`;
      const map = liveRef.current;
      let cur = map.get(key);
      if (!cur) {
        cur = { experiment_id: expId, attempt, values: [], done: false };
        map.set(key, cur);
      }
      if (event === "execution.curve_point" && p.metric === "loss") {
        cur.values.push(Number(p.value ?? 0));
      } else if (event === "execution.completed") {
        cur.done = true;
      }
    });
    // Throttle re-renders to ~7fps regardless of message rate.
    const flush = setInterval(() => {
      setLiveCurves(Array.from(liveRef.current.values()));
    }, 140);
    return () => {
      close();
      clearInterval(flush);
    };
  }, [runId]);

  // --- durable REST load (final curves after each batch + plots + summary) ---
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
        setRestCurves(out);
        const nextPlots = await listExecutionPlots(runId).catch(() => []);
        if (alive) setPlots(nextPlots);
        const sum = await fetch(`${BASE}/api/execution/${runId}/summary`).then((r) => r.json());
        if (alive) setSummary(sum);
      } catch (e) {
        if (alive) setErr(String(e));
      }
    }
    void loadAll();
    const iv = setInterval(loadAll, 2500);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [runId]);

  // Group live curves by attempt; fall back to REST curves (attempt 1) when no
  // stream has arrived yet (e.g. opening the page after the batch finished).
  const attempts = useMemo(() => {
    const byAttempt = new Map<number, LiveCurve[]>();
    for (const c of liveCurves) {
      const list = byAttempt.get(c.attempt) ?? [];
      list.push(c);
      byAttempt.set(c.attempt, list);
    }
    if (byAttempt.size === 0 && restCurves.length > 0) {
      byAttempt.set(summary?.attempt ?? 1, restCurves.map((c) => ({
        experiment_id: c.experiment_id,
        attempt: summary?.attempt ?? 1,
        values: c.values,
        done: true,
      })));
    }
    return [...byAttempt.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([attempt, curves]) => {
        const sorted = [...curves].sort((a, b) =>
          a.experiment_id.localeCompare(b.experiment_id),
        );
        const finals = sorted
          .filter((c) => c.values.length > 0)
          .map((c) => resFromLoss(c.values[c.values.length - 1]));
        const meanRes = mean(finals);
        const settled = sorted.every((c) => c.done) && sorted.length > 0;
        return { attempt, curves: sorted, meanRes, settled, count: sorted.length };
      });
  }, [liveCurves, restCurves, summary]);

  return (
    <main className="container mx-auto max-w-7xl px-6 py-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">多实验仿真视图 · 16 路并发</h1>
          <p className="text-sm text-slate-400">
            Run <code>{runId}</code> · {summary?.total ?? 0} 组实验
            {summary?.max_concurrency ? ` · 最大并发 ${summary.max_concurrency}` : ""}
            {" · RES 门槛 "}
            <span className="font-mono">{RES_GATE_DB} dB</span>
            （越低越好）
          </p>
        </div>
        <Link href={`/runs/${runId}`} className="text-xs text-slate-400 hover:text-slate-200">
          &larr; 返回 Run
        </Link>
      </header>

      {err ? <p className="mb-4 text-sm text-red-300">{err}</p> : null}

      {attempts.map(({ attempt, curves, meanRes, settled, count }) => {
        const passed = settled && meanRes <= RES_GATE_DB;
        const failed = settled && meanRes > RES_GATE_DB;
        return (
          <section key={attempt} className="mb-8">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h2 className="text-sm font-semibold uppercase text-slate-300">
                第 {attempt} 轮 · {count} 路并发仿真
              </h2>
              <span
                className={`rounded px-2 py-0.5 text-[11px] font-medium ${
                  passed
                    ? "bg-emerald-500/15 text-emerald-300"
                    : failed
                      ? "bg-rose-500/15 text-rose-300"
                      : "bg-slate-500/15 text-slate-300"
                }`}
              >
                {settled ? `均值 RES ${meanRes.toFixed(1)} dB` : "运行中…"}
                {passed ? " · 达标 ✓" : failed ? " · 未达标 ✗ 触发主控回溯" : ""}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
              {curves.map((c) => (
                <CurvePanel key={`${attempt}-${c.experiment_id}`} curve={c} />
              ))}
            </div>
          </section>
        );
      })}

      {attempts.length === 0 ? (
        <p className="text-sm text-slate-500">等待执行批次启动…</p>
      ) : null}

      {plots.length > 0 ? (
        <section className="mb-8">
          <h2 className="mb-3 text-sm font-semibold uppercase text-slate-400">实时训练图（PNG 快照）</h2>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {plots.map((plot) => (
              <LivePlotPanel key={plot.filename} plot={plot} />
            ))}
          </div>
        </section>
      ) : null}

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

function CurvePanel({ curve }: { curve: LiveCurve }): JSX.Element {
  const w = 280;
  const h = 110;
  const vals = curve.values.length ? curve.values : [1];
  // Plot in dB so the residual floor is visible and curves are comparable.
  const db = vals.map((v) => resFromLoss(v));
  const max = Math.max(...db, 0);
  const min = Math.min(...db, RES_GATE_DB - 4);
  const range = max - min || 1;
  const path = db
    .map((v, i) => {
      const x = (i / Math.max(1, db.length - 1)) * w;
      const y = h - ((v - min) / range) * h;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  const finalRes = db[db.length - 1];
  const passed = curve.done && finalRes <= RES_GATE_DB;
  const stroke = !curve.done ? "#38bdf8" : passed ? "#34d399" : "#fb7185";
  const gateY = h - ((RES_GATE_DB - min) / range) * h;
  return (
    <div className="rounded border border-mars-border bg-mars-panel p-3">
      <div className="flex items-center justify-between">
        <h3 className="truncate text-[11px] font-medium text-slate-200">{curve.experiment_id}</h3>
        <span
          className={`font-mono text-[10px] ${
            !curve.done ? "text-sky-300" : passed ? "text-emerald-300" : "text-rose-300"
          }`}
        >
          {finalRes.toFixed(1)} dB
        </span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="mt-2 h-24 w-full">
        {gateY > 0 && gateY < h ? (
          <line
            x1={0}
            x2={w}
            y1={gateY}
            y2={gateY}
            stroke="#64748b"
            strokeWidth={0.8}
            strokeDasharray="4 3"
          />
        ) : null}
        <path d={path} fill="none" stroke={stroke} strokeWidth={1.6} />
      </svg>
      <div className="mt-1 flex justify-between text-[9px] text-slate-500">
        <span>{curve.done ? (passed ? "达标" : "未达标") : "运行中"}</span>
        <span>n={curve.values.length}</span>
      </div>
    </div>
  );
}
