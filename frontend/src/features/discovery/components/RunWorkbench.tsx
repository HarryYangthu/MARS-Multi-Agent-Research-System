"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  DiscoveryApiError,
  loadDiscoverySnapshot,
  mutateDiscoveryRun,
} from "../api";
import { candidateRows, shortRef } from "../selectors";
import type { DiscoverySnapshot, RunMutation } from "../types";
import { AuditPanel } from "./AuditPanel";
import { BudgetPanel } from "./BudgetPanel";
import { CandidateTable } from "./CandidateTable";
import { LineagePanel } from "./LineagePanel";
import { ParetoPanel } from "./ParetoPanel";
import { Badge, EmptyState, KeyValue, Panel } from "./Primitives";

type RunTab = "overview" | "candidates" | "lineage" | "audit";

const TABS: Array<{ id: RunTab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "candidates", label: "Candidates" },
  { id: "lineage", label: "Lineage" },
  { id: "audit", label: "Audit" },
];

function mutationActions(lifecycle: string): RunMutation[] {
  if (lifecycle === "created") return ["start"];
  if (lifecycle === "running") return ["pause", "stop"];
  if (lifecycle === "paused" || lifecycle === "waiting_hitl") return ["resume", "stop"];
  return [];
}
function actionLabel(action: RunMutation, waiting: boolean): string {
  if (action === "resume" && waiting) return "Approve archive & resume";
  return action[0].toUpperCase() + action.slice(1);
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

export function RunWorkbench({ runId }: { runId: string }): JSX.Element {
  const [snapshot, setSnapshot] = useState<DiscoverySnapshot | null>(null);
  const [tab, setTab] = useState<RunTab>("overview");
  const [loading, setLoading] = useState(true);
  const [mutation, setMutation] = useState<RunMutation | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = async (showLoading: boolean): Promise<void> => {
      if (showLoading) setLoading(true);
      try {
        const next = await loadDiscoverySnapshot(runId);
        if (!active) return;
        setSnapshot(next);
        setError(null);
      } catch (caught: unknown) {
        if (!active) return;
        setError(caught instanceof Error ? caught : new Error("Unable to load discovery run"));
      } finally {
        if (active && showLoading) setLoading(false);
      }
    };
    void refresh(true);
    const interval = window.setInterval(() => void refresh(false), 5000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [runId]);

  const rows = useMemo(
    () => (snapshot ? candidateRows(snapshot.replay) : []),
    [snapshot],
  );

  const runAction = async (action: RunMutation): Promise<void> => {
    if (!snapshot || snapshot.source !== "rest") return;
    setMutation(action);
    try {
      await mutateDiscoveryRun(runId, action);
      setSnapshot(await loadDiscoverySnapshot(runId));
      setError(null);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught : new Error("Run action failed"));
    } finally {
      setMutation(null);
    }
  };

  if (loading && !snapshot) {
    return <LoadingScreen runId={runId} />;
  }
  if (error && !snapshot) {
    return <LoadError runId={runId} error={error} />;
  }
  if (!snapshot) return <LoadingScreen runId={runId} />;

  const { replay, source } = snapshot;
  const { run } = replay;
  const actions = mutationActions(run.lifecycle);
  const quarantined = rows.filter((row) => row.candidate.status === "quarantined");
  const blocked = rows.filter((row) => row.gate === "blocked");

  return (
    <main className="min-h-screen bg-[#090c12] text-slate-100">
      <div className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8">
        {source === "contract_fixture" ? (
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-300/20 bg-amber-300/[0.06] px-4 py-3 text-xs text-amber-100">
            <span>
              Contract preview · deterministic synthetic data · not a live research result
            </span>
            <Link className="text-amber-200 underline underline-offset-4" href="/discovery/live-run-id">
              Open a REST-backed run
            </Link>
          </div>
        ) : null}
        {error ? (
          <div className="mb-5 rounded-xl border border-rose-400/20 bg-rose-400/[0.06] px-4 py-3 text-xs text-rose-200">
            Refresh failed; the last REST snapshot remains visible. {error.message}
          </div>
        ) : null}

        <header className="mb-6 rounded-2xl border border-white/10 bg-gradient-to-br from-[#141a24] to-[#0f131b] p-6">
          <div className="flex flex-col justify-between gap-5 xl:flex-row xl:items-start">
            <div className="min-w-0">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-300/70">
                  Model Discovery
                </span>
                <Badge value={run.lifecycle} />
                {run.hitl_pending ? <Badge value="waiting_hitl" label="HITL pending" /> : null}
              </div>
              <h1 className="truncate text-2xl font-semibold tracking-tight text-white">
                {run.project}
              </h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">{run.objective}</p>
              <p className="mt-3 font-mono text-xs text-slate-600">{run.run_id}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {actions.map((action) => (
                <button
                  className={`rounded-lg border px-3 py-2 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${action === "stop" ? "border-rose-400/20 text-rose-200 hover:bg-rose-400/10" : "border-cyan-400/25 bg-cyan-400/10 text-cyan-100 hover:bg-cyan-400/15"}`}
                  disabled={mutation !== null || source !== "rest"}
                  key={action}
                  onClick={() => void runAction(action)}
                  type="button"
                >
                  {mutation === action
                    ? "Working…"
                    : actionLabel(action, run.lifecycle === "waiting_hitl")}
                </button>
              ))}
            </div>
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
            <KeyValue label="Candidates" value={run.candidate_count} />
            <KeyValue label="Evaluated" value={run.evaluated_count} />
            <KeyValue label="Pareto" value={run.latest_archive?.pareto_candidate_ids.length ?? 0} />
            <KeyValue label="Quarantined" value={run.quarantined_count} />
            <KeyValue label="Iteration" value={run.next_iteration} />
            <KeyValue label="Checkpoint" value={`#${run.checkpoint_sequence}`} />
          </div>
        </header>

        <nav aria-label="Discovery views" className="mb-5 flex gap-1 overflow-x-auto border-b border-white/10">
          {TABS.map((item) => (
            <button
              className={`border-b-2 px-4 py-3 text-xs transition ${tab === item.id ? "border-cyan-300 text-cyan-200" : "border-transparent text-slate-500 hover:text-slate-300"}`}
              key={item.id}
              onClick={() => setTab(item.id)}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>

        {tab === "overview" ? (
          <div className="grid gap-5 xl:grid-cols-12">
            <Panel className="xl:col-span-8" eyebrow="objective space" title="Pareto frontier">
              <ParetoPanel replay={replay} />
            </Panel>
            <Panel className="xl:col-span-4" eyebrow="ledger" title="Budget">
              <BudgetPanel budget={run.budget} />
            </Panel>
            <Panel className="xl:col-span-7" eyebrow="durable replay" title="Lifecycle & checkpoints">
              <div className="space-y-2">
                {replay.checkpoints.slice(-8).reverse().map((checkpoint) => (
                  <div
                    className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-xl border border-white/8 bg-white/[0.02] px-3 py-3"
                    key={checkpoint.checkpoint_hash}
                  >
                    <span className="grid h-7 w-7 place-items-center rounded-full bg-white/5 font-mono text-[10px] text-slate-400">
                      {checkpoint.sequence}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-xs text-slate-200">{checkpoint.phase.replaceAll("_", " ")}</p>
                      <p className="mt-1 text-[10px] text-slate-600">{formatTime(checkpoint.created_at)}</p>
                    </div>
                    <Badge value={checkpoint.status} />
                  </div>
                ))}
              </div>
            </Panel>
            <Panel className="xl:col-span-5" eyebrow="review queue" title="Quarantine & HITL">
              <div className="mb-4 grid grid-cols-2 gap-3">
                <KeyValue label="Preflight quarantine" value={quarantined.length} />
                <KeyValue label="Hard gate blocked" value={blocked.length} />
              </div>
              {run.hitl_pending ? (
                <div className="mb-4 rounded-xl border border-amber-300/20 bg-amber-300/[0.05] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium text-amber-100">Archive review required</p>
                    <Badge value="waiting_hitl" />
                  </div>
                  <p className="mt-2 text-xs leading-5 text-amber-100/60">
                    W5 resolves archive-level review through resume. Candidate-specific selection is not exposed by the current REST contract.
                  </p>
                </div>
              ) : null}
              <div className="space-y-2">
                {quarantined.length === 0 && blocked.length === 0 ? (
                  <EmptyState title="Review queue clear" detail="No quarantined or gate-blocked candidates." />
                ) : (
                  [...quarantined, ...blocked]
                    .filter(
                      (row, index, all) =>
                        all.findIndex(
                          (item) => item.candidate.candidate_id === row.candidate.candidate_id,
                        ) === index,
                    )
                    .map((row) => (
                      <Link
                        className="flex items-center justify-between gap-3 rounded-xl border border-white/8 px-3 py-2.5 hover:border-rose-400/25"
                        href={`/discovery/candidates/${encodeURIComponent(row.candidate.candidate_id)}?run=${encodeURIComponent(run.run_id)}`}
                        key={row.candidate.candidate_id}
                      >
                        <span className="font-mono text-xs text-slate-300">
                          {shortRef(row.candidate.candidate_id, 17)}
                        </span>
                        <Badge value={row.candidate.status === "quarantined" ? "quarantined" : "blocked"} />
                      </Link>
                    ))
                )}
              </div>
            </Panel>
            <Panel className="xl:col-span-12" eyebrow="archive" title="Niche elites">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {Object.entries(run.latest_archive?.niche_elites ?? {}).map(([niche, candidateId]) => (
                  <Link
                    className="rounded-xl border border-violet-400/15 bg-violet-400/[0.04] p-4 hover:border-violet-400/30"
                    href={`/discovery/candidates/${encodeURIComponent(candidateId)}?run=${encodeURIComponent(run.run_id)}`}
                    key={niche}
                  >
                    <p className="text-[10px] uppercase tracking-[0.13em] text-violet-300/60">{niche}</p>
                    <p className="mt-2 font-mono text-xs text-violet-200">{shortRef(candidateId, 18)}</p>
                  </Link>
                ))}
              </div>
            </Panel>
          </div>
        ) : null}

        {tab === "candidates" ? (
          <Panel
            action={<span className="font-mono text-[10px] text-slate-500">{rows.length} records</span>}
            eyebrow="preflight · gate · fidelity"
            title="Candidate matrix"
          >
            <CandidateTable replay={replay} />
          </Panel>
        ) : null}
        {tab === "lineage" ? (
          <Panel eyebrow="parent graph" title="Candidate lineage">
            <LineagePanel replay={replay} />
          </Panel>
        ) : null}
        {tab === "audit" ? (
          <Panel eyebrow="reproducibility" title="Parent · LLM · operator audit">
            <AuditPanel replay={replay} />
          </Panel>
        ) : null}

        <footer className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-white/8 py-4 text-[10px] text-slate-600">
          <span>REST replay is the truth source · loaded {formatTime(snapshot.loaded_at)}</span>
          <span className="font-mono">{source}</span>
        </footer>
      </div>
    </main>
  );
}

function LoadingScreen({ runId }: { runId: string }): JSX.Element {
  return (
    <main className="grid min-h-screen place-items-center bg-[#090c12] px-6 text-slate-100">
      <div className="text-center">
        <span className="mx-auto block h-8 w-8 animate-spin rounded-full border-2 border-cyan-300/20 border-t-cyan-300" />
        <p className="mt-4 text-sm text-slate-400">Loading discovery replay</p>
        <p className="mt-2 font-mono text-[10px] text-slate-600">{runId}</p>
      </div>
    </main>
  );
}

function LoadError({ runId, error }: { runId: string; error: Error }): JSX.Element {
  const missing = error instanceof DiscoveryApiError && error.isCapabilityMissing;
  return (
    <main className="grid min-h-screen place-items-center bg-[#090c12] px-6 text-slate-100">
      <div className="w-full max-w-xl rounded-2xl border border-white/10 bg-[#11151d] p-7">
        <Badge value={missing ? "pending" : "failed"} label={missing ? "V3.0 compatible" : "REST error"} />
        <h1 className="mt-4 text-xl font-semibold">
          {missing ? "Model Discovery capability is not registered" : "Discovery run unavailable"}
        </h1>
        <p className="mt-3 text-sm leading-6 text-slate-400">
          {missing
            ? "The active backend answered without the V3.1 Discovery route. Existing V3.0 workflows remain usable; register the W5 router to enable this workspace."
            : error.message}
        </p>
        <p className="mt-4 font-mono text-xs text-slate-600">{runId}</p>
        <div className="mt-6 flex flex-wrap gap-2">
          <Link
            className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-3 py-2 text-xs text-cyan-100"
            href="/discovery/synthetic-preview"
          >
            Open contract preview
          </Link>
          <Link className="rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-300" href="/runs">
            Back to V3.0 runs
          </Link>
        </div>
      </div>
    </main>
  );
}
