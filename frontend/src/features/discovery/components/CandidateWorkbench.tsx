"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  DiscoveryApiError,
  isDiscoveryAbortError,
  loadDiscoverySnapshot,
} from "../api";
import { candidateRows, eventsForCandidate, formatMetric, shortRef } from "../selectors";
import type { CandidateRecord, DiscoverySnapshot } from "../types";
import { Badge, EmptyState, KeyValue, Panel } from "./Primitives";

function json(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function changedEntries(
  parent: Record<string, unknown>,
  candidate: Record<string, unknown>,
): Array<{ key: string; before: unknown; after: unknown }> {
  const keys = Array.from(new Set([...Object.keys(parent), ...Object.keys(candidate)])).sort();
  return keys
    .filter((key) => json(parent[key]) !== json(candidate[key]))
    .map((key) => ({ key, before: parent[key], after: candidate[key] }));
}

export function CandidateWorkbench({
  candidateId,
  runId,
}: {
  candidateId: string;
  runId: string;
}): JSX.Element {
  const [snapshot, setSnapshot] = useState<DiscoverySnapshot | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!runId) return;
    let active = true;
    let timer: number | null = null;
    const controller = new AbortController();
    const refresh = async (): Promise<void> => {
      try {
        const next = await loadDiscoverySnapshot(runId, controller.signal);
        if (!active || controller.signal.aborted) return;
        setSnapshot(next);
        setError(null);
      } catch (caught: unknown) {
        if (!active || controller.signal.aborted || isDiscoveryAbortError(caught)) return;
        setError(caught instanceof Error ? caught : new Error("Unable to load candidate replay"));
      } finally {
        if (active && !controller.signal.aborted) {
          timer = window.setTimeout(() => void refresh(), 5000);
        }
      }
    };
    void refresh();
    return () => {
      active = false;
      controller.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [runId]);

  const row = useMemo(
    () =>
      snapshot
        ? candidateRows(snapshot.replay).find(
            (candidateRow) => candidateRow.candidate.candidate_id === candidateId,
          ) ?? null
        : null,
    [candidateId, snapshot],
  );

  if (!runId) return <MissingRun candidateId={candidateId} />;
  if (error && !snapshot) return <CandidateError error={error} runId={runId} />;
  if (!snapshot) return <CandidateLoading candidateId={candidateId} />;
  if (!row) return <CandidateMissing candidateId={candidateId} runId={runId} />;

  const { replay, source } = snapshot;
  const { candidate, evaluation } = row;
  const events = eventsForCandidate(replay, candidateId);
  const parent = replay.candidates.find((item) => item.candidate_id === candidate.parent_ids[0]);
  const structureDiff = changedEntries(parent?.genome.structure ?? {}, candidate.genome.structure);
  const parameterDiff = changedEntries(
    parent?.genome.hyperparameters ?? {},
    candidate.genome.hyperparameters,
  );
  const selected = replay.run.selected_candidate_id === candidateId;

  return (
    <main className="min-h-screen bg-[#090c12] text-slate-100">
      <div className="mx-auto max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8">
        {source === "contract_fixture" ? (
          <div className="mb-5 rounded-xl border border-amber-300/20 bg-amber-300/[0.06] px-4 py-3 text-xs text-amber-100">
            Contract preview · deterministic synthetic data · not a live research result
          </div>
        ) : null}
        {error ? (
          <div className="mb-5 rounded-xl border border-rose-400/20 bg-rose-400/[0.06] px-4 py-3 text-xs text-rose-200">
            Refresh failed; showing the last REST snapshot. {error.message}
          </div>
        ) : null}
        <Link
          className="mb-4 inline-flex items-center gap-2 text-xs text-slate-500 transition hover:text-cyan-200"
          href={`/discovery/${encodeURIComponent(runId)}`}
        >
          <span aria-hidden>←</span> Discovery run
        </Link>

        <header className="mb-6 rounded-2xl border border-white/10 bg-gradient-to-br from-[#141a24] to-[#0f131b] p-6">
          <div className="flex flex-col justify-between gap-5 xl:flex-row xl:items-start">
            <div className="min-w-0">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-300/70">
                  Candidate dossier
                </span>
                <Badge value={candidate.status} />
                {row.pareto ? <Badge value="elite" label="Pareto" /> : null}
                {selected ? <Badge value="promoted" label="Selected" /> : null}
              </div>
              <h1 className="break-all font-mono text-xl font-semibold text-white">{candidateId}</h1>
              <p className="mt-2 text-sm text-slate-400">
                {candidate.genome.family} · generation {candidate.generation} · iteration {candidate.iteration}
              </p>
            </div>
            <button
              className="cursor-not-allowed rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-500"
              disabled
              title="Candidate-specific selection is not part of the current W5 REST contract"
              type="button"
            >
              Candidate selection endpoint pending
            </button>
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
            <KeyValue label="Preflight" value={<Badge value={row.preflight} />} />
            <KeyValue label="Hard gate" value={<Badge value={row.gate} />} />
            <KeyValue label="Fidelity" value={row.fidelity ?? "—"} />
            <KeyValue label="Parents" value={candidate.parent_ids.length} />
            <KeyValue label="Operator" value={candidate.operator} />
            <KeyValue label="Seed" value={evaluation?.seed ?? "—"} />
          </div>
        </header>

        {candidate.status === "quarantined" || candidate.status === "failed" ? (
          <div className="mb-5 rounded-2xl border border-rose-400/20 bg-rose-400/[0.05] p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-rose-100">
                {candidate.status === "quarantined" ? "Quarantine record" : "Failure record"}
              </h2>
              <Badge value={candidate.status} />
            </div>
            <p className="mt-2 text-xs leading-5 text-rose-100/65">
              {candidate.failure_reason || "No machine-readable reason was recorded."}
            </p>
          </div>
        ) : null}

        <div className="grid gap-5 xl:grid-cols-12">
          <Panel className="xl:col-span-7" eyebrow="artifact + structured delta" title="Diff">
            <div className="mb-4 rounded-xl border border-white/8 bg-black/20 px-4 py-3">
              <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Artifact reference</p>
              <p className="mt-1 break-all font-mono text-xs text-cyan-300">
                {candidate.artifact_refs.diff ?? "No diff artifact reference"}
              </p>
            </div>
            <DiffGroup label="Structure" entries={structureDiff} />
            <div className="mt-3">
              <DiffGroup label="Hyperparameters" entries={parameterDiff} />
            </div>
          </Panel>

          <Panel className="xl:col-span-5" eyebrow="canonical evaluator output" title="Metrics">
            {evaluation ? (
              <div className="space-y-3">
                {Object.entries(evaluation.canonical_metrics).map(([name, metric]) => (
                  <div
                    className="flex items-center justify-between gap-3 rounded-xl border border-white/8 px-4 py-3"
                    key={name}
                  >
                    <div>
                      <p className="text-xs text-slate-300">{name.replaceAll("_", " ")}</p>
                      <p className="mt-1 text-[10px] uppercase text-slate-600">{metric.direction}</p>
                    </div>
                    <span className="font-mono text-sm text-cyan-200">{formatMetric(metric)}</span>
                  </div>
                ))}
                <div className="grid grid-cols-2 gap-3 pt-2">
                  <KeyValue label="Evaluator" value={shortRef(evaluation.evaluator_hash, 20)} />
                  <KeyValue label="Dataset" value={shortRef(evaluation.dataset_hash, 20)} />
                </div>
              </div>
            ) : (
              <EmptyState title="No evaluation" detail="This candidate has no persisted evaluation record." />
            )}
          </Panel>

          <Panel className="xl:col-span-7" eyebrow="durable event replay" title="Logs">
            {events.length > 0 ? (
              <div className="space-y-2">
                {events.map((event) => (
                  <div
                    className="grid gap-2 rounded-xl border border-white/8 bg-black/10 px-4 py-3 md:grid-cols-[auto_minmax(0,1fr)_auto]"
                    key={event.event_id}
                  >
                    <span className="font-mono text-[10px] text-slate-600">#{event.sequence}</span>
                    <div className="min-w-0">
                      <p className="truncate text-xs text-slate-300">{event.name}</p>
                      {Object.keys(event.payload).length > 0 ? (
                        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-4 text-slate-500">
                          {json(event.payload)}
                        </pre>
                      ) : null}
                    </div>
                    <span className="text-[10px] text-slate-600">
                      {new Date(event.created_at).toLocaleTimeString()}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState title="No candidate events" detail="The replay contains no event for this candidate." />
            )}
          </Panel>

          <Panel className="xl:col-span-5" eyebrow="provenance" title="Evidence">
            <ReferenceList
              empty="No evidence references were persisted."
              refs={evaluation?.evidence_refs ?? []}
            />
            {evaluation?.findings.length ? (
              <div className="mt-5 border-t border-white/8 pt-4">
                <p className="mb-2 text-[10px] uppercase tracking-[0.12em] text-slate-500">Findings</p>
                <ul className="space-y-2 text-xs text-slate-400">
                  {evaluation.findings.map((finding) => (
                    <li className="flex gap-2" key={finding}>
                      <span className="text-cyan-300">·</span>
                      <span>{finding}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </Panel>

          <Panel className="xl:col-span-12" eyebrow="reproducibility" title="Parent · LLM · operator audit">
            <div className="grid gap-4 md:grid-cols-3">
              <AuditCard
                label="Parent selection"
                rows={[
                  ["parents", candidate.parent_ids.length ? candidate.parent_ids.join(", ") : "root"],
                  ["generation", String(candidate.generation)],
                  ["lineage ref", replay.run.latest_archive?.lineage_refs.find((ref) => ref.includes(candidateId)) ?? "—"],
                ]}
              />
              <AuditCard
                label="LLM attribution"
                rows={[
                  ["creator", candidate.creator],
                  ["provider", candidate.model_provider || "—"],
                  ["model", candidate.model_name || "—"],
                  ["prompt hash", candidate.prompt_hash || "—"],
                ]}
              />
              <AuditCard
                label="Operator trace"
                rows={[
                  ["operator", candidate.operator],
                  ["idempotency", candidate.idempotency_key],
                  ["context", candidate.context_manifest_ref || "—"],
                  ["fingerprint", candidate.fingerprints.exact || "—"],
                ]}
              />
            </div>
          </Panel>
        </div>
      </div>
    </main>
  );
}

function DiffGroup({
  label,
  entries,
}: {
  label: string;
  entries: Array<{ key: string; before: unknown; after: unknown }>;
}): JSX.Element {
  return (
    <div className="overflow-hidden rounded-xl border border-white/8">
      <p className="border-b border-white/8 bg-white/[0.025] px-4 py-2 text-[10px] uppercase tracking-[0.12em] text-slate-500">
        {label}
      </p>
      {entries.length > 0 ? (
        entries.map((entry) => (
          <div className="grid gap-1 border-b border-white/5 px-4 py-3 last:border-b-0 md:grid-cols-[150px_1fr_1fr]" key={entry.key}>
            <span className="font-mono text-xs text-slate-300">{entry.key}</span>
            <code className="break-all rounded bg-rose-400/[0.06] px-2 py-1 text-[11px] text-rose-200/70">
              − {json(entry.before)}
            </code>
            <code className="break-all rounded bg-emerald-400/[0.06] px-2 py-1 text-[11px] text-emerald-200/80">
              + {json(entry.after)}
            </code>
          </div>
        ))
      ) : (
        <p className="px-4 py-5 text-xs text-slate-500">No structured changes recorded.</p>
      )}
    </div>
  );
}

function ReferenceList({ refs, empty }: { refs: string[]; empty: string }): JSX.Element {
  if (refs.length === 0) return <p className="text-xs text-slate-500">{empty}</p>;
  return (
    <ul className="space-y-2">
      {refs.map((ref) => (
        <li className="break-all rounded-xl border border-white/8 px-3 py-2.5 font-mono text-[11px] text-cyan-300" key={ref}>
          {ref}
        </li>
      ))}
    </ul>
  );
}

function AuditCard({ label, rows }: { label: string; rows: string[][] }): JSX.Element {
  return (
    <div className="rounded-xl border border-white/8 bg-white/[0.02] p-4">
      <h3 className="text-xs font-medium text-slate-200">{label}</h3>
      <dl className="mt-4 space-y-3">
        {rows.map(([key, value]) => (
          <div key={key}>
            <dt className="text-[10px] uppercase tracking-[0.1em] text-slate-600">{key}</dt>
            <dd className="mt-1 break-all font-mono text-[11px] leading-4 text-slate-400">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function CandidateLoading({ candidateId }: { candidateId: string }): JSX.Element {
  return (
    <main className="grid min-h-screen place-items-center bg-[#090c12] px-6 text-center text-slate-100">
      <div>
        <span className="mx-auto block h-8 w-8 animate-spin rounded-full border-2 border-cyan-300/20 border-t-cyan-300" />
        <p className="mt-4 text-sm text-slate-400">Loading candidate dossier</p>
        <p className="mt-2 font-mono text-[10px] text-slate-600">{candidateId}</p>
      </div>
    </main>
  );
}

function MissingRun({ candidateId }: { candidateId: string }): JSX.Element {
  return (
    <main className="grid min-h-screen place-items-center bg-[#090c12] px-6 text-slate-100">
      <div className="max-w-lg rounded-2xl border border-white/10 bg-[#11151d] p-7">
        <h1 className="text-xl font-semibold">Run context required</h1>
        <p className="mt-3 text-sm leading-6 text-slate-400">
          Candidate details are replayed from their parent run. Add the run query parameter to this deep link.
        </p>
        <p className="mt-4 font-mono text-xs text-slate-600">{candidateId}</p>
      </div>
    </main>
  );
}

function CandidateMissing({ candidateId, runId }: { candidateId: string; runId: string }): JSX.Element {
  return (
    <main className="grid min-h-screen place-items-center bg-[#090c12] px-6 text-slate-100">
      <div className="max-w-lg rounded-2xl border border-white/10 bg-[#11151d] p-7">
        <h1 className="text-xl font-semibold">Candidate not found</h1>
        <p className="mt-3 font-mono text-xs text-slate-500">{candidateId}</p>
        <Link className="mt-6 inline-block text-xs text-cyan-300" href={`/discovery/${encodeURIComponent(runId)}`}>
          Return to discovery run
        </Link>
      </div>
    </main>
  );
}

function CandidateError({ error, runId }: { error: Error; runId: string }): JSX.Element {
  const missing = error instanceof DiscoveryApiError && error.isCapabilityMissing;
  return (
    <main className="grid min-h-screen place-items-center bg-[#090c12] px-6 text-slate-100">
      <div className="max-w-lg rounded-2xl border border-white/10 bg-[#11151d] p-7">
        <Badge value={missing ? "pending" : "failed"} label={missing ? "V3.0 compatible" : "REST error"} />
        <h1 className="mt-4 text-xl font-semibold">Candidate replay unavailable</h1>
        <p className="mt-3 text-sm leading-6 text-slate-400">
          {missing
            ? "The active backend does not expose the V3.1 Discovery replay route."
            : error.message}
        </p>
        <Link className="mt-6 inline-block text-xs text-cyan-300" href={`/discovery/${encodeURIComponent(runId)}`}>
          Return to run
        </Link>
      </div>
    </main>
  );
}
