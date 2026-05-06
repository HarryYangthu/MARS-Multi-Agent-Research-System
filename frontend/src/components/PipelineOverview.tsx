"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import {
  approveArtifact,
  getRun,
  listRuns,
  STAGE_ORDER,
  STAGE_TO_STEM,
  STAGE_TO_TIER,
  type RunDetail,
  type Stage,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const TIERS = [1, 2, 3, 4, 5] as const;

const STATE_BADGE: Record<string, string> = {
  pending: "bg-slate-700 text-slate-300",
  running: "bg-amber-500/30 text-amber-200",
  waiting_review: "bg-fuchsia-500/30 text-fuchsia-200",
  approved: "bg-emerald-500/30 text-emerald-200",
  done: "bg-emerald-500/40 text-emerald-100",
  failed: "bg-red-500/40 text-red-100",
  skipped: "bg-slate-800 text-slate-500",
};

const TIER_DOT: Record<number, string> = {
  1: "bg-tier-1",
  2: "bg-tier-2",
  3: "bg-tier-3",
  4: "bg-tier-4",
  5: "bg-tier-5",
};

const TIER_BORDER: Record<number, string> = {
  1: "border-tier-1/40",
  2: "border-tier-2/40",
  3: "border-tier-3/40",
  4: "border-tier-4/40",
  5: "border-tier-5/40",
};

// Per-Agent metadata derived from configs/agents.yaml (V0 schemas are fixed).
type AgentMeta = {
  schema: string;
  debateOn: boolean;
  detail?: string; // EN-friendly free-form chip
};
const AGENT_META: Record<Stage, AgentMeta> = {
  idea: { schema: "proposal.v1", debateOn: true },
  experiment: { schema: "experiment_plan.v1", debateOn: false },
  coding: {
    schema: "code_spec.v1",
    debateOn: false,
    detail: "remote_api · local_vllm",
  },
  execution: {
    schema: "run_log.v1",
    debateOn: false,
    detail: "max ≤6 concurrent",
  },
  writing: { schema: "report.v1", debateOn: true },
};

function tierStage(tier: number): Stage {
  return STAGE_ORDER[tier - 1];
}

function tierLabelKey(tier: number): string {
  return `layer.${tier}`;
}

export function PipelineOverview({
  selectedRunId,
}: {
  selectedRunId: string | null;
}): JSX.Element {
  const { t } = useI18n();
  const [details, setDetails] = useState<Record<string, RunDetail>>({});
  const [runIds, setRunIds] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const runs = await listRuns();
        if (!alive) return;
        // Newest first; cap at 12 so the panel stays readable.
        const ids = runs.reverse().map((r) => r.run_id).slice(0, 12);
        setRunIds(ids);
        const fetched = await Promise.all(ids.map((id) => getRun(id).catch(() => null)));
        if (!alive) return;
        const next: Record<string, RunDetail> = {};
        for (const d of fetched) {
          if (d) next[d.run_id] = d;
        }
        setDetails(next);
      } catch {
        /* ignore */
      }
    };
    void refresh();
    const iv = setInterval(refresh, 2500);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  // Group runs into a per-tier list of {runId, agentState}
  const tierRuns = useMemo(() => {
    const out: Record<number, { runId: string; agentState: string; project: string; task: string }[]> = {
      1: [],
      2: [],
      3: [],
      4: [],
      5: [],
    };
    for (const id of runIds) {
      const d = details[id];
      if (!d) continue;
      for (const stage of STAGE_ORDER) {
        const state = d.states[stage];
        if (!state || state === "skipped") continue;
        // Show this run on the tier corresponding to the *currently active* stage,
        // OR on every tier whose stage is in a non-pending state if you want richer coloring.
        // To match the reference (each tier shows agents currently on that tier),
        // we surface each (run, stage) combo where state is in {running, waiting_review, approved}.
        if (
          state === "running" ||
          state === "waiting_review" ||
          state === "approved"
        ) {
          out[STAGE_TO_TIER[stage]].push({
            runId: id,
            agentState: state,
            project: d.project,
            task: d.task,
          });
        }
      }
    }
    return out;
  }, [details, runIds]);

  return (
    <main className="flex h-full flex-col gap-3 overflow-auto bg-mars-bg/40 p-4">
      <SystemBar />
      {TIERS.map((tier, i) => {
        const stage = tierStage(tier);
        const cards = tierRuns[tier];
        const meta = AGENT_META[stage];
        return (
          <div
            key={tier}
            className={`relative rounded-lg border bg-mars-panel/60 p-3 ${TIER_BORDER[tier]}`}
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${TIER_DOT[tier]}`} />
                <h3 className="text-sm font-semibold text-slate-100">{t(tierLabelKey(tier))}</h3>
                <span className="hidden text-[10px] text-slate-500 sm:inline">·</span>
                <span className="text-[11px] text-slate-300">{t(`agent.${stage}`)}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Link
                  href={`/runs/new?entrypoint=${stage}`}
                  className="rounded bg-mars-subtle px-1.5 py-0.5 font-mono text-[10px] text-slate-300 hover:bg-mars-accent/30 hover:text-white"
                  title={t("layer.empty.cta")}
                >
                  {meta.schema}
                </Link>
                {meta.debateOn ? (
                  <span className="rounded bg-fuchsia-500/20 px-1.5 py-0.5 text-[10px] text-fuchsia-200">
                    debate-on
                  </span>
                ) : null}
                {meta.detail ? (
                  <span className="hidden rounded bg-mars-subtle px-1.5 py-0.5 text-[10px] text-slate-400 lg:inline">
                    {meta.detail}
                  </span>
                ) : null}
                <span className="rounded bg-mars-bg/60 px-1.5 py-0.5 text-[10px] text-slate-400">
                  {cards.length} 🦾 · {cards.length > 0 ? t("layer.active") : t("layer.idle")}
                </span>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {cards.length === 0 ? (
                <EmptySlot tier={tier} stage={stage} />
              ) : (
                cards.map((c, idx) => (
                  <AgentCard
                    key={`${c.runId}-${idx}`}
                    runId={c.runId}
                    project={c.project}
                    task={c.task}
                    state={c.agentState}
                    tier={tier}
                    selected={selectedRunId === c.runId}
                    label={`L${tier}-${String.fromCharCode(65 + idx)}`}
                    stage={stage}
                  />
                ))
              )}
            </div>
            {i < TIERS.length - 1 ? (
              <div className="absolute -bottom-3 left-1/2 -translate-x-1/2 text-slate-600">↓</div>
            ) : null}
          </div>
        );
      })}
    </main>
  );
}

function SystemBar(): JSX.Element {
  return (
    <div className="flex items-center gap-3 rounded border border-mars-border bg-mars-panel/80 px-3 py-1.5 text-[11px] text-slate-400">
      <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 font-mono text-emerald-300">📈 CPU 1%</span>
      <span className="rounded bg-cyan-500/20 px-1.5 py-0.5 font-mono text-cyan-300">MEM 4 / 63G</span>
      <span className="rounded bg-mars-subtle px-1.5 py-0.5 font-mono text-slate-300">
        4× NVIDIA L40S · GPU 0–3 (ready)
      </span>
    </div>
  );
}

function EmptySlot({ tier, stage }: { tier: number; stage: Stage }): JSX.Element {
  const { t } = useI18n();
  return (
    <Link
      href={`/runs/new?entrypoint=${stage}`}
      className={`group flex flex-col items-center justify-center gap-1 rounded border border-dashed ${TIER_BORDER[tier]} bg-mars-bg/40 px-3 py-4 text-center text-[11px] text-slate-500 transition hover:border-mars-accent hover:bg-mars-accent/10 hover:text-mars-accent`}
    >
      <span className="font-medium">{t("layer.empty.cta")}</span>
      <span className="text-[10px] text-slate-600 group-hover:text-mars-accent/80">
        {t("layer.empty.hint")}
      </span>
    </Link>
  );
}

function AgentCard({
  runId,
  project,
  task,
  state,
  tier,
  selected,
  label,
  stage,
}: {
  runId: string;
  project: string;
  task: string;
  state: string;
  tier: number;
  selected: boolean;
  label: string;
  stage: Stage;
}): JSX.Element {
  const { t } = useI18n();
  const stem = STAGE_TO_STEM[stage];
  const showApprove = state === "waiting_review";

  async function approve(e: React.MouseEvent): Promise<void> {
    e.stopPropagation();
    e.preventDefault();
    try {
      // try v1 first; if it fails the page will refresh and pick up next version
      await approveArtifact(runId, stage, stem, "v1");
    } catch {
      try {
        await approveArtifact(runId, stage, stem, "v2");
      } catch {
        /* ignore — UI will refresh */
      }
    }
  }

  return (
    <Link
      href={`/runs/${runId}`}
      className={`relative block rounded border bg-mars-panel2 p-2 transition hover:border-mars-accent ${
        selected ? "border-mars-accent" : "border-mars-border"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] text-slate-300">🦾 {label}</span>
        <span className="truncate font-mono text-[9px] text-slate-500">{project}</span>
      </div>
      <p className="mt-1 truncate text-[11px] text-slate-200">{task || runId}</p>
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[9px] uppercase ${STATE_BADGE[state] ?? "bg-slate-700"}`}>
          {t(`state.${state}`)}
        </span>
        {showApprove ? (
          <button
            onClick={approve}
            className="rounded bg-mars-accent px-2 py-0.5 text-[9px] text-white hover:bg-mars-accent2"
          >
            ✓ {t("run.approve")}
          </button>
        ) : (
          <span className="text-[9px] text-slate-500">{t(`agent.${stage}`)}</span>
        )}
      </div>
      <div className="mt-1 flex gap-0.5">
        {Array.from({ length: 12 }).map((_, i) => (
          <span
            key={i}
            className={`h-1 flex-1 rounded ${
              state === "done" || state === "approved"
                ? "bg-emerald-500/60"
                : state === "running"
                  ? i < 7
                    ? "bg-amber-500/60"
                    : "bg-mars-subtle"
                  : state === "waiting_review"
                    ? "bg-fuchsia-500/60"
                    : "bg-mars-subtle"
            }`}
          />
        ))}
      </div>
    </Link>
  );
}
