"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ArtifactPanel } from "@/components/ArtifactPanel";
import { ContextManifestPanel } from "@/components/ContextManifestPanel";
import { CurveWall, type ExpCurve } from "@/components/CurveWall";
import { ThinkingStream } from "@/components/ThinkingStream";
import {
  cancelRun,
  getExecutionMetrics,
  getPlannedExperiments,
  getRun,
  getThinking,
  pauseRun,
  retryRun,
  sendFeedback,
  STAGE_ORDER,
  STAGE_TO_TIER,
  type PlannedExperiment,
  type RunDetail,
  type Stage,
} from "@/lib/api";
import { openRunSocket, type WSMessage } from "@/lib/socket";
import { useI18n } from "@/lib/i18n";

const STATE_BADGE: Record<string, string> = {
  pending: "bg-slate-700 text-slate-300",
  running: "bg-amber-500/30 text-amber-200",
  waiting_review: "bg-fuchsia-500/30 text-fuchsia-200",
  approved: "bg-emerald-500/30 text-emerald-200",
  done: "bg-emerald-500/40 text-emerald-100",
  failed: "bg-red-500/40 text-red-100",
  skipped: "bg-slate-800 text-slate-500",
};
const RUN_STATUS_BADGE: Record<string, string> = {
  created: "bg-slate-600 text-slate-200",
  running: "bg-amber-500/30 text-amber-200",
  waiting_human: "bg-fuchsia-500/30 text-fuchsia-200",
  repairing: "bg-orange-500/30 text-orange-200",
  completed: "bg-emerald-500/40 text-emerald-100",
  failed: "bg-red-500/40 text-red-100",
  cancelled: "bg-slate-700 text-slate-400",
};

type Thinking = { reasoning: string; content: string; active: boolean };
type Tab = "artifact" | "thinking" | "context" | "sim";

export default function RunDetailPage(): JSX.Element {
  const { t } = useI18n();
  const params = useParams<{ runId: string }>();
  const runId = params.runId;

  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [states, setStates] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Stage>("idea");
  const [tab, setTab] = useState<Tab>("artifact");
  const [err, setErr] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const [live, setLive] = useState(false);

  const [thinking, setThinking] = useState<Record<string, Thinking>>({});
  const [curves, setCurves] = useState<Record<string, ExpCurve>>({});
  const [curveOrder, setCurveOrder] = useState<string[]>([]);
  const [planned, setPlanned] = useState<PlannedExperiment[]>([]);
  const selectedRef = useRef<Stage>("idea");
  selectedRef.current = selected;

  const refresh = useCallback(async () => {
    try {
      const d = await getRun(runId);
      setDetail(d);
      setStates(d.states);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [runId]);

  useEffect(() => {
    void refresh();
    const iv = setInterval(refresh, 3000);
    return () => clearInterval(iv);
  }, [refresh]);

  // planned experiments (poll lightly — appears once execution drafts)
  useEffect(() => {
    let alive = true;
    const tick = () =>
      getPlannedExperiments(runId)
        .then((d) => alive && setPlanned(d.experiments))
        .catch(() => {});
    void tick();
    const iv = setInterval(tick, 4000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [runId]);

  // Live WebSocket — thinking + execution + state.
  useEffect(() => {
    const close = openRunSocket(runId, (msg: WSMessage) => {
      setLive(true);
      const p = msg.payload as Record<string, unknown>;
      const ch = msg.channel;
      if (ch.endsWith(".agent_state")) {
        const agent = String(p.agent ?? "");
        const to = String(p.to_state ?? "");
        if (agent && to) setStates((s) => ({ ...s, [agent]: to }));
      } else if (ch.endsWith(".thinking")) {
        const agent = String(p.agent ?? "");
        const ev = String(p.event ?? "");
        if (!agent) return;
        setThinking((prev) => {
          const cur = prev[agent] ?? { reasoning: "", content: "", active: false };
          if (ev === "thinking.start") return { ...prev, [agent]: { reasoning: "", content: "", active: true } };
          if (ev === "thinking.end") return { ...prev, [agent]: { ...cur, active: false } };
          if (ev === "thinking.delta") {
            const txt = String(p.text ?? "");
            if (p.kind === "reasoning") return { ...prev, [agent]: { ...cur, reasoning: cur.reasoning + txt, active: true } };
            return { ...prev, [agent]: { ...cur, content: cur.content + txt, active: true } };
          }
          return prev;
        });
        // Auto-focus thinking of the running agent for the demo.
        if (ev === "thinking.start" && agent === selectedRef.current) setTab("thinking");
      } else if (ch.endsWith(".execution")) {
        const ev = String(p.event ?? "");
        if (ev === "execution.batch_started") {
          const exps = (p.experiments as string[]) ?? [];
          setCurveOrder(exps);
          setCurves(() => {
            const next: Record<string, ExpCurve> = {};
            for (const id of exps) next[id] = { values: [], status: "running" };
            return next;
          });
          if (selectedRef.current === "execution") setTab("sim");
        } else if (ev === "execution.curve_point") {
          const id = String(p.experiment_id ?? "");
          const v = Number(p.value);
          setCurves((prev) => {
            const cur = prev[id] ?? { values: [], status: "running" as const };
            return { ...prev, [id]: { ...cur, values: [...cur.values, v] } };
          });
        } else if (ev === "execution.completed") {
          const id = String(p.experiment_id ?? "");
          setCurves((prev) => {
            const cur = prev[id] ?? { values: [], status: "running" as const };
            return { ...prev, [id]: { ...cur, status: "done" } };
          });
        }
      } else if (ch === "run.lifecycle" || ch.endsWith(".failure")) {
        void refresh();
      }
    });
    return close;
  }, [runId, refresh]);

  // Load persisted thinking when switching agent (replay on reload).
  useEffect(() => {
    if (thinking[selected]?.reasoning || thinking[selected]?.content) return;
    getThinking(runId, selected)
      .then((d) => {
        if (!d.exists) return;
        // crude split of the persisted "# 思考过程 / # 输出" file
        const m = d.text.split(/# 输出 \(content\)/);
        const reasoning = (m[0] || "").replace(/# 思考过程 \(reasoning\)/, "").trim();
        const content = (m[1] || "").trim();
        setThinking((prev) => ({ ...prev, [selected]: { reasoning, content, active: false } }));
      })
      .catch(() => {});
  }, [runId, selected, thinking]);

  // When execution finishes, pull final metrics into the wall (reload case).
  useEffect(() => {
    if (states.execution !== "done") return;
    if (Object.keys(curves).length > 0) return;
    getExecutionMetrics(runId)
      .then((rows) => {
        if (!rows.length) return;
        const next: Record<string, ExpCurve> = {};
        const order: string[] = [];
        for (const r of rows) {
          const id = r.run_id.split("_").slice(-1)[0] || r.run_id;
          order.push(id);
          next[id] = { values: [r.metrics.loss ?? 0], status: "done", metrics: r.metrics };
        }
        setCurveOrder(order);
        setCurves(next);
      })
      .catch(() => {});
  }, [states.execution, runId, curves]);

  useEffect(() => {
    setTab((cur) => (cur === "sim" && selected !== "execution" ? "artifact" : cur));
  }, [selected]);

  const runStatus = detail?.run_status ?? "created";
  const stageList = useMemo(() => STAGE_ORDER.filter((s) => s in states), [states]);

  // Default-select the first stage present in the graph (e.g. standalone runs
  // may not include "idea").
  useEffect(() => {
    if (stageList.length && !stageList.includes(selected)) setSelected(stageList[0]);
  }, [stageList, selected]);

  async function cmd(fn: (id: string) => Promise<unknown>): Promise<void> {
    try {
      await fn(runId);
      await refresh();
    } catch (e) {
      setErr(String(e));
    }
  }
  async function submitFeedback(): Promise<void> {
    if (!feedback.trim()) return;
    try {
      await sendFeedback(runId, feedback, selected);
      setFeedback("");
    } catch (e) {
      setErr(String(e));
    }
  }

  const th = thinking[selected] ?? { reasoning: "", content: "", active: false };
  const tabs: { id: Tab; label: string }[] = [
    { id: "artifact", label: "📦 产物" },
    { id: "thinking", label: "🧠 思考过程" },
    { id: "context", label: "🗂 上下文配置" },
  ];
  if (selected === "execution") tabs.push({ id: "sim", label: "🔬 仿真墙" });

  return (
    <div className="flex h-screen flex-col bg-mars-bg text-slate-200">
      <header className="flex flex-wrap items-center gap-3 border-b border-mars-border bg-mars-panel/70 px-4 py-2">
        <Link href="/" className="text-xs text-slate-400 hover:text-white">← {t("topbar.lab")}</Link>
        <h1 className="truncate text-sm font-semibold text-slate-100">{detail?.task || runId}</h1>
        <span className={`rounded px-2 py-0.5 text-[10px] uppercase ${RUN_STATUS_BADGE[runStatus] ?? "bg-slate-700"}`}>
          {t(`run_status.${runStatus}`)}
        </span>
        <span className="font-mono text-[10px] text-slate-500">{runId}</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${live ? "bg-emerald-500/20 text-emerald-300" : "bg-slate-700 text-slate-400"}`}>
          {live ? "● live" : "○ polling"}
        </span>
        <div className="ml-auto flex gap-1.5 text-[11px]">
          <button onClick={() => void cmd(pauseRun)} className="ctl bg-amber-500/20 text-amber-300">⏸ {t("run.pause")}</button>
          <button onClick={() => void cmd(retryRun)} className="ctl bg-cyan-500/20 text-cyan-300">↻ {t("run.retry")}</button>
          <button onClick={() => void cmd(cancelRun)} className="ctl bg-rose-500/20 text-rose-300">✕ {t("run.cancel")}</button>
        </div>
      </header>

      {err ? <p className="bg-rose-500/10 px-4 py-1 text-[11px] text-rose-300">{err}</p> : null}

      <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)]">
        <nav className="flex flex-col gap-1 overflow-auto border-r border-mars-border bg-mars-panel/40 p-2">
          {stageList.map((stage) => {
            const st = states[stage] ?? "pending";
            const isThinking = thinking[stage]?.active;
            return (
              <button
                key={stage}
                onClick={() => setSelected(stage)}
                className={`rounded border px-2 py-2 text-left text-xs transition ${
                  selected === stage ? "border-mars-accent bg-mars-accent/10" : "border-mars-border bg-mars-panel2 hover:border-mars-accent/50"
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="font-medium text-slate-200">L{STAGE_TO_TIER[stage]} · {t(`agent.${stage}`)}</span>
                  {isThinking ? <span className="animate-pulse text-[9px] text-amber-300">🧠</span> : null}
                </div>
                <span className={`mt-1 inline-block rounded px-1.5 py-0.5 text-[9px] uppercase ${STATE_BADGE[st] ?? "bg-slate-700"}`}>
                  {t(`state.${st}`)}
                </span>
              </button>
            );
          })}
        </nav>

        <section className="flex min-h-0 flex-col">
          <div className="flex items-center gap-1 border-b border-mars-border bg-mars-panel/30 px-2 py-1">
            {tabs.map((tb) => (
              <button
                key={tb.id}
                onClick={() => setTab(tb.id)}
                className={`rounded px-2.5 py-1 text-[11px] transition ${
                  tab === tb.id ? "bg-mars-accent/30 text-white" : "text-slate-400 hover:bg-mars-subtle"
                }`}
              >
                {tb.label}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-hidden">
            {tab === "artifact" ? (
              <ArtifactPanel key={`${selected}-${states[selected]}`} runId={runId} stage={selected} state={states[selected] ?? "pending"} onChanged={refresh} />
            ) : tab === "thinking" ? (
              <ThinkingStream reasoning={th.reasoning} content={th.content} active={!!th.active} />
            ) : tab === "context" ? (
              <ContextManifestPanel runId={runId} stage={selected} refreshKey={states[selected]} />
            ) : (
              <ExecutionSim
                planned={planned}
                curves={curves}
                order={curveOrder}
                state={states.execution ?? "pending"}
              />
            )}
          </div>

          <div className="flex items-center gap-2 border-t border-mars-border bg-mars-panel/80 px-3 py-2 text-xs">
            <span className="text-slate-400">💬 {t("feedback.title")}</span>
            <input
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void submitFeedback()}
              placeholder={t("feedback.placeholder")}
              className="flex-1 rounded border border-mars-border bg-mars-bg/60 px-2 py-1 text-xs text-slate-200 outline-none focus:border-mars-accent"
            />
            <button onClick={() => void submitFeedback()} className="rounded bg-mars-accent px-3 py-1 text-white hover:bg-mars-accent2">{t("feedback.send")}</button>
          </div>
        </section>
      </div>

      <style jsx>{`.ctl { border-radius: 0.3rem; padding: 0.25rem 0.6rem; }`}</style>
    </div>
  );
}

function ExecutionSim({
  planned,
  curves,
  order,
  state,
}: {
  planned: PlannedExperiment[];
  curves: Record<string, ExpCurve>;
  order: string[];
  state: string;
}): JSX.Element {
  const running = Object.values(curves).filter((c) => c.status === "running").length;
  const done = Object.values(curves).filter((c) => c.status === "done").length;
  const hasCurves = Object.keys(curves).length > 0;
  return (
    <div className="flex h-full flex-col gap-3 overflow-auto p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded bg-mars-accent/20 px-2 py-0.5 text-mars-accent">
          计划实验 {planned.length}
        </span>
        {hasCurves ? (
          <>
            <span className="rounded bg-amber-500/20 px-2 py-0.5 text-amber-300">运行中 {running}</span>
            <span className="rounded bg-emerald-500/20 px-2 py-0.5 text-emerald-300">完成 {done}</span>
          </>
        ) : (
          <span className="text-slate-500">
            {state === "waiting_review"
              ? "审批 Execution 产物后开始并发仿真"
              : "等待仿真启动…"}
          </span>
        )}
      </div>

      {!hasCurves && planned.length ? (
        <div className="rounded-lg border border-mars-border bg-mars-panel2 p-2">
          <div className="mb-1 text-[11px] text-slate-400">将执行的实验矩阵(审批后并发运行):</div>
          <div className="grid grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-4">
            {planned.map((e) => (
              <div key={e.experiment_id} className="rounded border border-mars-border bg-mars-bg/40 px-2 py-1 font-mono text-[10px] text-slate-300">
                {e.label}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {hasCurves ? <CurveWall curves={curves} order={order} /> : null}
    </div>
  );
}
