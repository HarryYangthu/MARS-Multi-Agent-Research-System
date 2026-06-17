"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import {
  getContextManifest,
  getContextRaw,
  getContextRun,
  listRuns,
  previewContext,
  type ContextManifestSummary,
  type ContextManifestV2,
  type ContextRawView,
  type ContextRunView,
  type ContextSegment,
  type RunSummary,
} from "@/lib/api";
import {
  buildManifestDiff as buildManifestDiffView,
  filterAndSortSegments,
  filterManifestSummaries,
  formatRawContent as formatRawContentView,
  formatTokenDelta as formatTokenDeltaView,
  manifestAgentOptions as getManifestAgentOptions,
  manifestPurposeOptions as getManifestPurposeOptions,
  riskTotal as getRiskTotal,
  segmentKindOptions as getSegmentKindOptions,
  segmentRiskOptions as getSegmentRiskOptions,
  summaryBudgetUsed as getSummaryBudgetUsed,
  summaryOverBudget as getSummaryOverBudget,
  type ChangedSegmentDiff,
  type ManifestDiff,
  type SegmentDiffItem,
  type SortKey,
} from "@/lib/contextWorkbench";
import { useProject } from "@/lib/project";

const AGENTS = ["idea", "experiment", "coding", "execution", "writing"] as const;

const WARNING_COPY: Record<string, string> = {
  kb_segment_count_high: "KB segment count is high; selector may be adding confusion.",
  tool_count_high: "Tool count is high; tool selection should stay active.",
  upstream_tokens_high: "Upstream context is large enough to distract the model.",
  multiple_versions_in_context: "Multiple upstream versions are present in the same prompt.",
  unverified_memory: "A memory segment lacks clear provenance.",
};

const RISK_COPY: Record<string, string> = {
  poisoning: "Potentially unsupported or source-less context.",
  distraction: "Large context may pull attention away from the task.",
  confusion: "Too many similar sources or tools may blur the instruction.",
  clash: "Multiple versions of the same source may conflict.",
  lost_in_middle: "A critical segment sits in the middle of a long prompt.",
};

const ACTION_COPY: Record<string, string> = {
  keep_critical_over_target: "Kept critical segment even though it exceeded target budget.",
  drop_low_priority_over_target: "Dropped low-priority segment after target budget was reached.",
  trim_to_fit: "Trimmed segment to fit inside max budget.",
  include_over_target: "Included segment over target because it still fit max budget.",
  drop_over_max_budget: "Dropped segment because it could not fit max budget after trim.",
};

const WARNING_ACTION_COPY: Record<string, string> = {
  kb_segment_count_high: "Tighten KB top-k or raise the relevance threshold for this agent.",
  tool_count_high: "Enable tool selection or split rarely used tools behind a narrower mode.",
  upstream_tokens_high: "Replace full upstream artifacts with handoff summaries and raw_refs.",
  multiple_versions_in_context: "Keep only the approved artifact version in downstream context.",
  unverified_memory: "Attach a source_ref/raw_ref or drop the memory segment before the next call.",
};

const RISK_ACTION_COPY: Record<string, string> = {
  poisoning: "Require provenance before reuse; unsupported memory should be rewritten as a cited segment.",
  distraction: "Compress or reference large upstream/tool content before packing.",
  confusion: "Lower top-k or select tools by the current task intent.",
  clash: "Choose a canonical source version and exclude stale variants.",
  lost_in_middle: "Move critical constraints to the opening or closing render band.",
};

export default function ContextWorkbench(): JSX.Element {
  const { selectedProject } = useProject();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState("");
  const [runView, setRunView] = useState<ContextRunView | null>(null);
  const [manifestId, setManifestId] = useState("");
  const [manifest, setManifest] = useState<ContextManifestV2 | null>(null);
  const [compareManifestId, setCompareManifestId] = useState("");
  const [compareManifest, setCompareManifest] = useState<ContextManifestV2 | null>(null);
  const [selectedSegmentId, setSelectedSegmentId] = useState("");
  const [raw, setRaw] = useState<ContextRawView | null>(null);
  const [rawRefInput, setRawRefInput] = useState("");
  const [previewTask, setPreviewTask] = useState("");
  const [previewAgent, setPreviewAgent] = useState<(typeof AGENTS)[number]>("idea");
  const [error, setError] = useState("");
  const [manifestAgentFilter, setManifestAgentFilter] = useState("all");
  const [manifestPurposeFilter, setManifestPurposeFilter] = useState("all");
  const [manifestRiskOnly, setManifestRiskOnly] = useState(false);
  const [manifestOverBudgetOnly, setManifestOverBudgetOnly] = useState(false);
  const [kindFilter, setKindFilter] = useState("all");
  const [riskFilter, setRiskFilter] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("render");

  useEffect(() => {
    let alive = true;
    void listRuns(selectedProject)
      .then((items) => {
        if (!alive) return;
        setRuns(items);
        setRunId((prev) =>
          items.some((item) => item.run_id === prev) ? prev : items[0]?.run_id || "",
        );
      })
      .catch((err: unknown) => setError(String(err)));
    return () => {
      alive = false;
    };
  }, [selectedProject]);

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    setError("");
    void getContextRun(runId)
      .then((view) => {
        if (!alive) return;
        setRunView(view);
        setManifestId((prev) => prev || view.manifests[0]?.manifest_id || "");
      })
      .catch((err: unknown) => setError(String(err)));
    return () => {
      alive = false;
    };
  }, [runId]);

  useEffect(() => {
    if (!runId || !manifestId) return;
    let alive = true;
    setRaw(null);
    setError("");
    void getContextManifest(runId, manifestId)
      .then((item) => {
        if (!alive) return;
        setManifest(item);
        setSelectedSegmentId((prev) =>
          item.segments.some((segment) => segment.id === prev) ? prev : item.segments[0]?.id || "",
        );
        setRawRefInput((prev) =>
          prev && item.raw_refs.includes(prev) ? prev : item.raw_refs[0] || "",
        );
      })
      .catch((err: unknown) => setError(String(err)));
    return () => {
      alive = false;
    };
  }, [runId, manifestId]);

  useEffect(() => {
    if (!runId || !compareManifestId) {
      setCompareManifest(null);
      return;
    }
    let alive = true;
    setError("");
    void getContextManifest(runId, compareManifestId)
      .then((item) => {
        if (!alive) return;
        setCompareManifest(item);
      })
      .catch((err: unknown) => setError(String(err)));
    return () => {
      alive = false;
    };
  }, [compareManifestId, runId]);

  useEffect(() => {
    if (compareManifestId && compareManifestId === manifestId) {
      setCompareManifestId("");
      setCompareManifest(null);
    }
  }, [compareManifestId, manifestId]);

  const selectedSegment = useMemo(() => {
    return manifest?.segments.find((item) => item.id === selectedSegmentId) ?? null;
  }, [manifest, selectedSegmentId]);

  const manifestAgentOptions = useMemo(() => {
    return getManifestAgentOptions(runView?.manifests ?? []);
  }, [runView]);

  const manifestPurposeOptions = useMemo(() => {
    return getManifestPurposeOptions(runView?.manifests ?? []);
  }, [runView]);

  const visibleManifests = useMemo(() => {
    return filterManifestSummaries(runView?.manifests ?? [], {
      agent: manifestAgentFilter,
      purpose: manifestPurposeFilter,
      riskOnly: manifestRiskOnly,
      overBudgetOnly: manifestOverBudgetOnly,
    });
  }, [
    manifestAgentFilter,
    manifestOverBudgetOnly,
    manifestPurposeFilter,
    manifestRiskOnly,
    runView,
  ]);

  const compareOptions = useMemo(() => {
    return (runView?.manifests ?? []).filter((item) => item.manifest_id !== manifestId);
  }, [manifestId, runView]);

  const manifestDiff = useMemo(() => {
    if (!manifest || !compareManifest) return null;
    return buildManifestDiffView(manifest, compareManifest);
  }, [compareManifest, manifest]);

  const kindOptions = useMemo(() => {
    return getSegmentKindOptions(manifest?.segments ?? []);
  }, [manifest]);

  const riskOptions = useMemo(() => {
    return getSegmentRiskOptions(manifest?.segments ?? []);
  }, [manifest]);

  const visibleSegments = useMemo(() => {
    return filterAndSortSegments(manifest?.segments ?? [], manifest?.render_order ?? [], {
      kind: kindFilter,
      risk: riskFilter,
      sortKey,
    });
  }, [kindFilter, manifest, riskFilter, sortKey]);

  async function openRaw(ref: string): Promise<void> {
    if (!runId) return;
    setError("");
    try {
      const view = await getContextRaw(runId, ref);
      setRaw(view);
      setRawRefInput(ref);
    } catch (err) {
      setError(String(err));
    }
  }

  async function runPreview(): Promise<void> {
    setError("");
    try {
      const item = await previewContext({
        agent: previewAgent,
        project: selectedProject,
        task: previewTask || "Investigate ATK-MoE routing under 8L config.",
      });
      setManifest(item);
      setManifestId("");
      setCompareManifestId("");
      setCompareManifest(null);
      setSelectedSegmentId(item.segments[0]?.id || "");
      setRawRefInput(item.raw_refs[0] || "");
      setRaw(null);
    } catch (err) {
      setError(String(err));
    }
  }

  const budget = manifest?.budget;
  const budgetPct =
    budget && budget.max > 0 ? Math.min(100, Math.round((budget.used / budget.max) * 100)) : 0;

  return (
    <main className="grid h-screen grid-rows-[auto_1fr] bg-mars-bg text-slate-100">
      <header className="flex items-center justify-between border-b border-mars-border bg-mars-panel px-5 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-sm text-slate-400 hover:text-white">
            MARS
          </Link>
          <span className="text-slate-600">/</span>
          <h1 className="text-base font-semibold">Context Workbench</h1>
          <span className="rounded border border-mars-border bg-mars-panel2 px-2 py-0.5 text-[11px] text-slate-400">
            Manifest V2
          </span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={runId}
            onChange={(event) => {
              setRunId(event.target.value);
              setManifestId("");
              setManifest(null);
              setCompareManifestId("");
              setCompareManifest(null);
              setRaw(null);
              setRawRefInput("");
            }}
            className="h-8 rounded border border-mars-border bg-mars-panel2 px-2 text-xs"
          >
            {runs.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.run_id}
              </option>
            ))}
          </select>
          <Link
            href={runId ? `/runs/${runId}` : "/runs"}
            className="rounded border border-mars-border bg-mars-panel2 px-3 py-1.5 text-xs hover:bg-mars-subtle"
          >
            Open Run
          </Link>
        </div>
      </header>

      <div className="grid min-h-0 grid-cols-[300px_minmax(0,1fr)_360px]">
        <aside className="min-h-0 overflow-auto border-r border-mars-border bg-mars-panel/70">
          <section className="border-b border-mars-border p-4">
            <h2 className="text-sm font-semibold">Preview</h2>
            <div className="mt-3 grid gap-2">
              <select
                value={previewAgent}
                onChange={(event) => setPreviewAgent(event.target.value as (typeof AGENTS)[number])}
                className="h-8 rounded border border-mars-border bg-mars-panel2 px-2 text-xs"
              >
                {AGENTS.map((agent) => (
                  <option key={agent} value={agent}>
                    {agent}
                  </option>
                ))}
              </select>
              <textarea
                value={previewTask}
                onChange={(event) => setPreviewTask(event.target.value)}
                className="h-24 resize-none rounded border border-mars-border bg-mars-panel2 p-2 text-xs outline-none focus:border-mars-accent"
                placeholder="Task"
              />
              <button
                onClick={() => void runPreview()}
                className="h-8 rounded bg-mars-accent px-3 text-xs font-medium text-white hover:bg-indigo-500"
              >
                Compile Preview
              </button>
            </div>
          </section>

          <section className="p-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">Manifests</h2>
              <span className="font-mono text-[11px] text-slate-500">
                {visibleManifests.length} / {runView?.budget_summary.manifest_count ?? 0}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
              <select
                value={manifestAgentFilter}
                onChange={(event) => setManifestAgentFilter(event.target.value)}
                className="h-8 rounded border border-mars-border bg-mars-panel2 px-2"
              >
                {manifestAgentOptions.map((agent) => (
                  <option key={agent} value={agent}>
                    agent: {agent}
                  </option>
                ))}
              </select>
              <select
                value={manifestPurposeFilter}
                onChange={(event) => setManifestPurposeFilter(event.target.value)}
                className="h-8 rounded border border-mars-border bg-mars-panel2 px-2"
              >
                {manifestPurposeOptions.map((purpose) => (
                  <option key={purpose} value={purpose}>
                    purpose: {purpose}
                  </option>
                ))}
              </select>
              <label className="flex h-8 items-center gap-2 rounded border border-mars-border bg-mars-panel2 px-2 text-[11px] text-slate-300">
                <input
                  type="checkbox"
                  checked={manifestRiskOnly}
                  onChange={(event) => setManifestRiskOnly(event.target.checked)}
                  className="accent-mars-accent"
                />
                risk only
              </label>
              <label className="flex h-8 items-center gap-2 rounded border border-mars-border bg-mars-panel2 px-2 text-[11px] text-slate-300">
                <input
                  type="checkbox"
                  checked={manifestOverBudgetOnly}
                  onChange={(event) => setManifestOverBudgetOnly(event.target.checked)}
                  className="accent-mars-accent"
                />
                over budget
              </label>
            </div>
            <div className="mt-3 grid gap-2">
              {visibleManifests.map((item) => (
                <ManifestButton
                  key={item.manifest_id}
                  item={item}
                  active={item.manifest_id === manifestId}
                  onClick={() => {
                    setManifestId(item.manifest_id);
                    if (compareManifestId === item.manifest_id) {
                      setCompareManifestId("");
                      setCompareManifest(null);
                    }
                    setSelectedSegmentId("");
                  }}
                />
              ))}
              {runView && runView.manifests.length === 0 ? (
                <div className="rounded border border-dashed border-mars-border p-3 text-xs text-slate-500">
                  No v2 manifests yet.
                </div>
              ) : null}
              {runView && runView.manifests.length > 0 && visibleManifests.length === 0 ? (
                <div className="rounded border border-dashed border-mars-border p-3 text-xs text-slate-500">
                  No manifests match the current filters.
                </div>
              ) : null}
            </div>
          </section>
        </aside>

        <section className="min-h-0 overflow-auto bg-mars-bg">
          <div className="sticky top-0 z-10 border-b border-mars-border bg-mars-bg/95 p-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <h2 className="text-sm font-semibold">
                  {manifest ? `${manifest.agent} / ${manifest.purpose}` : "No manifest selected"}
                </h2>
                <p className="mt-1 font-mono text-[11px] text-slate-500">
                  {manifest?.manifest_id || "context_manifest.v2"}
                </p>
              </div>
              <div className="w-60">
                <div className="mb-1 flex justify-between text-[11px] text-slate-400">
                  <span>Token budget</span>
                  <span>
                    {budget?.used ?? 0} / {budget?.max ?? 0}
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded bg-mars-panel2">
                  <div
                    className={`h-full ${budget?.over_budget ? "bg-red-400" : "bg-emerald-400"}`}
                    style={{ width: `${budgetPct}%` }}
                  />
                </div>
              </div>
            </div>
            {error ? <p className="mt-2 text-xs text-red-300">{error}</p> : null}
            {manifest ? (
              <div className="mt-3 flex items-center gap-2 text-xs">
                <span className="text-slate-500">Compare</span>
                <select
                  value={compareManifestId}
                  onChange={(event) => setCompareManifestId(event.target.value)}
                  className="h-8 min-w-72 rounded border border-mars-border bg-mars-panel2 px-2"
                >
                  <option value="">none</option>
                  {compareOptions.map((item) => (
                    <option key={item.manifest_id} value={item.manifest_id}>
                      {item.agent} / {item.purpose} / {item.manifest_id}
                    </option>
                  ))}
                </select>
                {compareManifest ? (
                  <span className="font-mono text-[11px] text-slate-500">
                    delta {manifestDiff?.tokenDelta ?? 0} tokens
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
              <select
                value={kindFilter}
                onChange={(event) => setKindFilter(event.target.value)}
                className="h-8 rounded border border-mars-border bg-mars-panel2 px-2"
              >
                {kindOptions.map((kind) => (
                  <option key={kind} value={kind}>
                    kind: {kind}
                  </option>
                ))}
              </select>
              <select
                value={riskFilter}
                onChange={(event) => setRiskFilter(event.target.value)}
                className="h-8 rounded border border-mars-border bg-mars-panel2 px-2"
              >
                {riskOptions.map((risk) => (
                  <option key={risk} value={risk}>
                    risk: {risk}
                  </option>
                ))}
              </select>
              <select
                value={sortKey}
                onChange={(event) => setSortKey(event.target.value as SortKey)}
                className="h-8 rounded border border-mars-border bg-mars-panel2 px-2"
              >
                <option value="render">render order</option>
                <option value="tokens_desc">tokens desc</option>
                <option value="priority">priority</option>
                <option value="risk">risk count</option>
              </select>
              <span className="ml-auto font-mono text-[11px] text-slate-500">
                {visibleSegments.length} / {manifest?.segments.length ?? 0} segments
              </span>
            </div>
            <div className="overflow-hidden rounded border border-mars-border">
              <table className="w-full border-collapse text-left text-xs">
                <thead className="bg-mars-panel text-slate-400">
                  <tr>
                    <th className="px-3 py-2">Kind</th>
                    <th className="px-3 py-2">Segment</th>
                    <th className="px-3 py-2">Tokens</th>
                    <th className="px-3 py-2">Compression</th>
                    <th className="px-3 py-2">Risk</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleSegments.map((segment) => (
                    <SegmentRow
                      key={segment.id}
                      segment={segment}
                      active={segment.id === selectedSegmentId}
                      onClick={() => setSelectedSegmentId(segment.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-4">
              <Panel title="Render Order">
                <ol className="space-y-1 font-mono text-[11px] text-slate-300">
                  {(manifest?.render_order ?? []).map((id, index) => (
                    <li key={`${id}-${index}`} className="truncate">
                      {index + 1}. {id}
                    </li>
                  ))}
                </ol>
              </Panel>
              <Panel title="Messages Preview">
                <div className="space-y-2">
                  {(manifest?.messages_preview ?? []).map((msg, index) => (
                    <div key={`${msg.role}-${index}`} className="rounded bg-mars-panel2 p-2">
                      <div className="mb-1 text-[10px] uppercase text-slate-500">{msg.role}</div>
                      <pre className="whitespace-pre-wrap text-[11px] text-slate-300">{msg.content}</pre>
                    </div>
                  ))}
                </div>
              </Panel>
            </div>
            {manifestDiff && compareManifest ? (
              <Panel title="Manifest Diff">
                <ManifestDiffPanel diff={manifestDiff} compareManifest={compareManifest} />
              </Panel>
            ) : null}
          </div>
        </section>

        <aside className="min-h-0 overflow-auto border-l border-mars-border bg-mars-panel/70 p-4">
          <Panel title="Selected Segment">
            {selectedSegment ? (
              <div className="space-y-3">
                <div>
                  <div className="text-sm font-semibold">{selectedSegment.title}</div>
                  <div className="mt-1 font-mono text-[11px] text-slate-500">
                    {selectedSegment.source_ref}
                  </div>
                </div>
                <MetricGrid segment={selectedSegment} />
                <p className="text-xs text-slate-400">{selectedSegment.selection_reason}</p>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-mars-bg p-3 text-[11px] text-slate-300">
                  {selectedSegment.text_preview}
                </pre>
                {selectedSegment.raw_ref ? (
                  <button
                    onClick={() => void openRaw(selectedSegment.raw_ref || "")}
                    className="h-8 rounded border border-mars-border bg-mars-panel2 px-3 text-xs hover:bg-mars-subtle"
                  >
                    Open Raw
                  </button>
                ) : null}
              </div>
            ) : (
              <p className="text-xs text-slate-500">Select a segment.</p>
            )}
          </Panel>

          <Panel title="Diagnostics">
            <DiagnosticsPanel
              diagnostics={manifest?.diagnostics ?? {}}
              fallbackRisk={runView?.risk_summary}
            />
          </Panel>

          <Panel title="Raw Reference">
            <RawReferencePanel
              raw={raw}
              rawRefs={manifest?.raw_refs ?? []}
              rawRefInput={rawRefInput}
              onRawRefInput={setRawRefInput}
              onOpen={(ref) => void openRaw(ref)}
            />
          </Panel>
        </aside>
      </div>
    </main>
  );
}

function ManifestButton({
  item,
  active,
  onClick,
}: {
  item: ContextManifestSummary;
  active: boolean;
  onClick: () => void;
}): JSX.Element {
  const risks = getRiskTotal(item.risk_counts);
  const overBudget = getSummaryOverBudget(item);
  return (
    <button
      onClick={onClick}
      className={`rounded border px-3 py-2 text-left text-xs ${
        active
          ? "border-mars-accent bg-mars-accent/15"
          : "border-mars-border bg-mars-panel2 hover:bg-mars-subtle"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-slate-100">{item.agent}</span>
        <span className="font-mono text-[10px] text-slate-500">{item.purpose}</span>
      </div>
      <div className="mt-1 truncate font-mono text-[10px] text-slate-500">
        {item.manifest_id}
      </div>
      <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-slate-400">
        <span>{item.segment_count} segments</span>
        <span className={overBudget ? "text-red-300" : ""}>{getSummaryBudgetUsed(item)} tokens</span>
        {risks > 0 ? <span className="text-amber-200">risk {risks}</span> : null}
      </div>
    </button>
  );
}

function SegmentRow({
  segment,
  active,
  onClick,
}: {
  segment: ContextSegment;
  active: boolean;
  onClick: () => void;
}): JSX.Element {
  return (
    <tr
      onClick={onClick}
      className={`cursor-pointer border-t border-mars-border ${
        active ? "bg-mars-accent/15" : "hover:bg-mars-panel/70"
      }`}
    >
      <td className="px-3 py-2 font-mono text-[11px] text-slate-300">{segment.kind}</td>
      <td className="px-3 py-2">
        <div className="font-medium text-slate-100">{segment.title}</div>
        <div className="truncate font-mono text-[10px] text-slate-500">
          {segment.source_ref}
        </div>
      </td>
      <td className="px-3 py-2 font-mono text-slate-300">{segment.tokens_estimated}</td>
      <td className="px-3 py-2 text-slate-300">{segment.compression}</td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {segment.risk_flags.length > 0 ? (
            segment.risk_flags.map((risk) => (
              <span key={risk} className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-200">
                {risk}
              </span>
            ))
          ) : (
            <span className="text-[10px] text-slate-600">none</span>
          )}
        </div>
      </td>
    </tr>
  );
}

function ManifestDiffPanel({
  diff,
  compareManifest,
}: {
  diff: ManifestDiff;
  compareManifest: ContextManifestV2;
}): JSX.Element {
  return (
    <div className="space-y-4 text-xs">
      <div className="flex flex-wrap gap-2">
        <DiffMetric label="token delta" value={formatTokenDeltaView(diff.tokenDelta)} />
        <DiffMetric label="added" value={String(diff.added.length)} />
        <DiffMetric label="removed" value={String(diff.removed.length)} />
        <DiffMetric label="changed" value={String(diff.changed.length)} />
        <DiffMetric label="compare" value={`${compareManifest.agent}/${compareManifest.purpose}`} />
      </div>
      <div className="grid grid-cols-3 gap-3">
        <DiffList title="Added" items={diff.added} empty="No added segments." />
        <DiffList title="Removed" items={diff.removed} empty="No removed segments." />
        <ChangedDiffList items={diff.changed} />
      </div>
    </div>
  );
}

function DiffMetric({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="min-w-28 rounded border border-mars-border bg-mars-bg px-2 py-1.5">
      <div className="text-[10px] uppercase text-slate-500">{label}</div>
      <div className="mt-1 truncate font-mono text-[11px] text-slate-200">{value}</div>
    </div>
  );
}

function DiffList({
  title,
  items,
  empty,
}: {
  title: string;
  items: SegmentDiffItem[];
  empty: string;
}): JSX.Element {
  return (
    <div>
      <div className="mb-2 text-[10px] uppercase text-slate-500">{title}</div>
      <div className="space-y-2">
        {items.slice(0, 8).map((item) => (
          <div key={item.id} className="rounded bg-mars-bg p-2">
            <div className="truncate font-medium text-slate-200">{item.title}</div>
            <div className="mt-1 flex gap-2 font-mono text-[10px] text-slate-500">
              <span>{item.kind}</span>
              <span>{item.tokens} tokens</span>
            </div>
          </div>
        ))}
        {items.length === 0 ? <p className="text-[11px] text-slate-500">{empty}</p> : null}
        {items.length > 8 ? (
          <p className="text-[11px] text-slate-500">{items.length - 8} more segments.</p>
        ) : null}
      </div>
    </div>
  );
}

function ChangedDiffList({ items }: { items: ChangedSegmentDiff[] }): JSX.Element {
  return (
    <div>
      <div className="mb-2 text-[10px] uppercase text-slate-500">Changed</div>
      <div className="space-y-2">
        {items.slice(0, 8).map((item) => (
          <div key={item.id} className="rounded bg-mars-bg p-2">
            <div className="truncate font-medium text-slate-200">{item.title}</div>
            <div className="mt-1 font-mono text-[10px] text-slate-500">
              {item.compareTokens} -&gt; {item.currentTokens} ({formatTokenDeltaView(item.tokenDelta)})
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {item.changes.map((change) => (
                <span key={change} className="rounded bg-slate-700/60 px-1.5 py-0.5 text-[10px] text-slate-300">
                  {change}
                </span>
              ))}
            </div>
          </div>
        ))}
        {items.length === 0 ? <p className="text-[11px] text-slate-500">No changed segments.</p> : null}
        {items.length > 8 ? (
          <p className="text-[11px] text-slate-500">{items.length - 8} more segments.</p>
        ) : null}
      </div>
    </div>
  );
}

function RawReferencePanel({
  raw,
  rawRefs,
  rawRefInput,
  onRawRefInput,
  onOpen,
}: {
  raw: ContextRawView | null;
  rawRefs: string[];
  rawRefInput: string;
  onRawRefInput: (value: string) => void;
  onOpen: (ref: string) => void;
}): JSX.Element {
  const cleanedRef = rawRefInput.trim();
  const formattedContent = raw ? formatRawContentView(raw.content) : "";
  return (
    <div className="space-y-3">
      {rawRefs.length > 0 ? (
        <div className="grid gap-2">
          <select
            value={rawRefInput}
            onChange={(event) => onRawRefInput(event.target.value)}
            className="h-8 rounded border border-mars-border bg-mars-panel2 px-2 text-xs"
          >
            {rawRefs.map((ref) => (
              <option key={ref} value={ref}>
                {ref}
              </option>
            ))}
          </select>
          <button
            onClick={() => {
              if (cleanedRef) onOpen(cleanedRef);
            }}
            disabled={!cleanedRef}
            className="h-8 rounded border border-mars-border bg-mars-panel2 px-3 text-xs hover:bg-mars-subtle disabled:cursor-not-allowed disabled:opacity-50"
          >
            Open Ref
          </button>
        </div>
      ) : (
        <p className="text-xs text-slate-500">No raw refs in this manifest.</p>
      )}
      {raw ? (
        <div className="space-y-2">
          <div className="font-mono text-[11px] text-slate-400">{raw.raw_ref}</div>
          <div className="font-mono text-[11px] text-slate-500">{raw.path}</div>
          <div className="text-xs text-slate-400">
            {raw.size_chars} chars {raw.truncated ? "/ truncated preview" : ""}
          </div>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-mars-bg p-3 text-[11px] text-slate-300">
            {formattedContent}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

function Panel({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="mb-4">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
        {title}
      </h3>
      <div className="rounded border border-mars-border bg-mars-panel/70 p-3">{children}</div>
    </section>
  );
}

function DiagnosticsPanel({
  diagnostics,
  fallbackRisk,
}: {
  diagnostics: Record<string, unknown>;
  fallbackRisk?: Record<string, number>;
}): JSX.Element {
  const warnings = asStringArray(diagnostics.warnings);
  const riskRecord = asRecord(diagnostics.risk_counts);
  const fallbackRecord = fallbackRisk ?? {};
  const riskCounts = numberEntries(
    Object.keys(riskRecord).length > 0 ? riskRecord : fallbackRecord,
  );
  const compression = asRecord(diagnostics.compression);
  const compressionCounts = numberEntries(
    asRecord(compression.counts ?? diagnostics.compression_counts),
  );
  const compressionDecisions = asRecordArray(compression.decisions);
  const packing = asRecord(diagnostics.packing);
  const packingDecisions = asRecordArray(packing.decisions);
  const dropped = asNumber(packing.dropped) ?? 0;
  const trimmed = asNumber(packing.trimmed) ?? 0;
  const hasSummary =
    warnings.length > 0 ||
    riskCounts.length > 0 ||
    compressionCounts.length > 0 ||
    packingDecisions.length > 0;

  if (!hasSummary) {
    return <p className="text-xs text-slate-500">No diagnostics recorded.</p>;
  }

  return (
    <div className="space-y-4 text-xs">
      {warnings.length > 0 ? (
        <div>
          <div className="mb-2 text-[10px] uppercase text-slate-500">Warnings</div>
          <div className="grid gap-2">
            {warnings.map((warning) => (
              <DiagnosticRow
                key={warning}
                label={warning}
                detail={WARNING_COPY[warning] ?? "Context engine recorded this warning."}
                action={WARNING_ACTION_COPY[warning] ?? "Inspect the affected segments before reusing this prompt."}
              />
            ))}
          </div>
        </div>
      ) : null}

      {riskCounts.length > 0 ? (
        <div>
          <div className="mb-2 text-[10px] uppercase text-slate-500">Risk Summary</div>
          <div className="grid gap-2">
            {riskCounts.map(([risk, count]) => (
              <DiagnosticRow
                key={risk}
                label={`${risk} x${count}`}
                detail={RISK_COPY[risk] ?? "Risk flag emitted by context diagnostics."}
                action={RISK_ACTION_COPY[risk] ?? "Review segment provenance and packing placement."}
              />
            ))}
          </div>
        </div>
      ) : null}

      {compressionCounts.length > 0 ? (
        <div>
          <div className="mb-2 text-[10px] uppercase text-slate-500">Compression</div>
          <div className="mb-2 flex flex-wrap gap-1">
            {compressionCounts.map(([strategy, count]) => (
              <span key={strategy} className="rounded bg-sky-500/10 px-2 py-1 text-sky-200">
                {strategy} x{count}
              </span>
            ))}
          </div>
          <DecisionList decisions={compressionDecisions} mode="compression" />
        </div>
      ) : null}

      {packingDecisions.length > 0 ? (
        <div>
          <div className="mb-2 text-[10px] uppercase text-slate-500">
            Packing Decisions
          </div>
          <div className="mb-2 flex gap-2 text-[11px] text-slate-400">
            <span>dropped {dropped}</span>
            <span>trimmed {trimmed}</span>
          </div>
          <DecisionList decisions={packingDecisions} mode="packing" />
        </div>
      ) : null}

      <details className="rounded bg-mars-bg p-2">
        <summary className="cursor-pointer text-[11px] text-slate-400">Raw diagnostics</summary>
        <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-[11px] text-slate-300">
          {JSON.stringify(diagnostics, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function DiagnosticRow({
  label,
  detail,
  action,
}: {
  label: string;
  detail: string;
  action?: string;
}): JSX.Element {
  return (
    <div className="rounded bg-mars-bg p-2">
      <div className="font-mono text-[11px] text-slate-200">{label}</div>
      <div className="mt-1 text-[11px] text-slate-500">{detail}</div>
      {action ? <div className="mt-1 text-[11px] text-emerald-200">Action: {action}</div> : null}
    </div>
  );
}

function DecisionList({
  decisions,
  mode,
}: {
  decisions: Record<string, unknown>[];
  mode: "compression" | "packing";
}): JSX.Element {
  if (decisions.length === 0) {
    return <p className="text-[11px] text-slate-500">No segment-level decisions.</p>;
  }
  return (
    <div className="grid gap-2">
      {decisions.slice(0, 8).map((decision, index) => {
        const title = String(decision.title ?? decision.segment_id ?? `decision-${index}`);
        const action = String(decision.action ?? decision.strategy ?? mode);
        const before = asNumber(decision.before_tokens);
        const after = asNumber(decision.after_tokens);
        const detail =
          mode === "packing"
            ? ACTION_COPY[action] ?? String(decision.reason ?? "Packing decision recorded.")
            : `Applied ${action} compression.`;
        return (
          <div key={`${title}-${index}`} className="rounded bg-mars-bg p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-medium text-slate-200">{title}</span>
              <span className="font-mono text-[10px] text-slate-500">{action}</span>
            </div>
            <div className="mt-1 text-[11px] text-slate-500">{detail}</div>
            {before !== null || after !== null ? (
              <div className="mt-1 font-mono text-[10px] text-slate-500">
                {before ?? "?"} -&gt; {after ?? "?"} tokens
              </div>
            ) : null}
          </div>
        );
      })}
      {decisions.length > 8 ? (
        <p className="text-[11px] text-slate-500">
          {decisions.length - 8} more decisions in raw diagnostics.
        </p>
      ) : null}
    </div>
  );
}

function MetricGrid({ segment }: { segment: ContextSegment }): JSX.Element {
  const items = [
    ["priority", segment.priority],
    ["tokens", String(segment.tokens_estimated)],
    ["hash", segment.content_hash],
    ["compression", segment.compression],
  ];
  return (
    <div className="grid grid-cols-2 gap-2">
      {items.map(([label, value]) => (
        <div key={label} className="rounded bg-mars-bg p-2">
          <div className="text-[10px] uppercase text-slate-500">{label}</div>
          <div className="mt-1 truncate font-mono text-[11px] text-slate-300">{value}</div>
        </div>
      ))}
    </div>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function numberEntries(value: Record<string, unknown>): [string, number][] {
  return Object.entries(value)
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .sort((left, right) => right[1] - left[1]);
}
