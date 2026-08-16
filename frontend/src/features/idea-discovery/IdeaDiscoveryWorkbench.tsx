"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createHypothesis,
  editHypothesis,
  getDiscoverySystemVersion,
  getIdeaDiscovery,
  isIdeaDiscoveryUnavailable,
  rejectHypothesis,
  selectHypothesis,
  systemSupportsIdeaDiscovery,
} from "./api";
import { AddHypothesisForm } from "./AddHypothesisForm";
import {
  DISCOVERY_STAGES,
  DiscoveryStagePanel,
} from "./DiscoveryStagePanels";
import { HypothesisReviewPanel } from "./HypothesisReviewPanel";
import type {
  DiscoveryHypothesis,
  DiscoveryStage,
  HypothesisCreateAuditRecord,
  HypothesisCreateInput,
  IdeaDiscoverySnapshot,
} from "./types";

type ViewMode = "loading" | "ready" | "compatibility" | "error";

export function IdeaDiscoveryWorkbench({ runId }: { runId: string }): JSX.Element {
  const [viewMode, setViewMode] = useState<ViewMode>("loading");
  const [snapshot, setSnapshot] = useState<IdeaDiscoverySnapshot | null>(null);
  const [compatibilityReason, setCompatibilityReason] = useState("");
  const [error, setError] = useState("");
  const [activeStage, setActiveStage] = useState<DiscoveryStage>("generation");
  const [selectedId, setSelectedId] = useState("");
  const [statement, setStatement] = useState("");
  const [statementHypothesisId, setStatementHypothesisId] = useState("");
  const [statementDirty, setStatementDirty] = useState(false);
  const [actor, setActor] = useState("ui-researcher");
  const [reason, setReason] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [lastUpdated, setLastUpdated] = useState("");
  const mountedRef = useRef(false);
  const refreshSequenceRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      refreshSequenceRef.current += 1;
    };
  }, []);

  const refresh = useCallback(async (signal?: AbortSignal): Promise<void> => {
    const requestSequence = ++refreshSequenceRef.current;
    const [versionResult, discoveryResult] = await Promise.allSettled([
      getDiscoverySystemVersion(signal),
      getIdeaDiscovery(runId, signal),
    ]);
    if (
      !mountedRef.current ||
      signal?.aborted ||
      requestSequence !== refreshSequenceRef.current
    ) {
      return;
    }
    if (versionResult.status === "rejected") {
      if (isIdeaDiscoveryUnavailable(versionResult.reason)) {
        setCompatibilityReason("系统没有 capability API；该 run 使用 V3.0 兼容工作台。 ");
        setViewMode("compatibility");
      } else {
        setError(String(versionResult.reason));
        setViewMode("error");
      }
      return;
    }
    if (!systemSupportsIdeaDiscovery(versionResult.value)) {
      setCompatibilityReason("当前 distribution 未声明 idea_deep_discovery capability。 ");
      setViewMode("compatibility");
      return;
    }
    if (discoveryResult.status === "rejected") {
      if (isIdeaDiscoveryUnavailable(discoveryResult.reason)) {
        setCompatibilityReason("Idea discovery REST 尚不可用；保留 V3.0 run 查看入口。 ");
        setViewMode("compatibility");
      } else {
        setError(String(discoveryResult.reason));
        setViewMode("error");
      }
      return;
    }
    setSnapshot(discoveryResult.value);
    setLastUpdated(new Date().toLocaleTimeString());
    setError("");
    setViewMode("ready");
  }, [runId]);

  useEffect(() => {
    let disposed = false;
    let timer: number | null = null;
    const controller = new AbortController();
    const poll = async (): Promise<void> => {
      try {
        await refresh(controller.signal);
      } finally {
        if (!disposed && !controller.signal.aborted) {
          timer = window.setTimeout(() => void poll(), 5000);
        }
      }
    };
    void poll();
    return () => {
      disposed = true;
      controller.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [refresh]);

  useEffect(() => {
    if (!snapshot?.hypotheses.length) return;
    const available = new Set(snapshot.hypotheses.map((item) => item.hypothesis_id));
    if (selectedId && available.has(selectedId)) return;
    setSelectedId(
      snapshot.selected_id ||
        snapshot.finalist_ids[0] ||
        snapshot.hypotheses.find((item) => !item.blocked)?.hypothesis_id ||
        snapshot.hypotheses[0].hypothesis_id,
    );
  }, [selectedId, snapshot]);

  const selected = useMemo(
    () => snapshot?.hypotheses.find((item) => item.hypothesis_id === selectedId) ?? null,
    [selectedId, snapshot],
  );
  useEffect(() => {
    if (!selected) {
      setStatement("");
      setStatementHypothesisId("");
      setStatementDirty(false);
      return;
    }
    if (statementHypothesisId !== selected.hypothesis_id) {
      setStatement(selected.statement);
      setStatementHypothesisId(selected.hypothesis_id);
      setStatementDirty(false);
      setReason("");
      setActionMessage("");
      return;
    }
    if (!statementDirty && statement !== selected.statement) {
      setStatement(selected.statement);
    }
  }, [selected, statement, statementDirty, statementHypothesisId]);

  async function mutate(
    action: "edit" | "reject" | "select",
    operation: () => Promise<void>,
  ): Promise<void> {
    setBusyAction(action);
    setActionMessage("");
    try {
      await operation();
      await refresh();
      setStatementDirty(false);
      setActionMessage(`${action} 已提交；当前视图已从 REST 恢复。`);
    } catch (nextError) {
      if (isIdeaDiscoveryUnavailable(nextError)) {
        setActionMessage(
          `${action} REST 端点尚未实现；权威快照未改变，请继续使用当前只读发现视图。`,
        );
      } else {
        setActionMessage(String(nextError));
      }
    } finally {
      setBusyAction("");
    }
  }

  async function addHypothesis(
    input: HypothesisCreateInput,
  ): Promise<HypothesisCreateAuditRecord> {
    const audit = await createHypothesis(runId, input);
    await refresh();
    return audit;
  }

  if (viewMode === "loading") return <LoadingView runId={runId} />;
  if (viewMode === "compatibility") {
    return <CompatibilityView runId={runId} reason={compatibilityReason} onRetry={() => { setViewMode("loading"); void refresh(); }} />;
  }
  if (viewMode === "error" || !snapshot) {
    return <ErrorView runId={runId} message={error || "Idea discovery response is unavailable."} onRetry={() => { setViewMode("loading"); void refresh(); }} />;
  }

  return (
    <main className="min-h-screen bg-mars-bg px-5 py-6 text-slate-100 md:px-8">
      <div className="mx-auto max-w-[1500px]">
        <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2 text-xs"><span className="font-semibold uppercase tracking-[0.2em] text-indigo-300">Co-Scientist</span><StatusBadge status={snapshot.status} /></div>
            <h1 className="mt-2 text-2xl font-semibold">Idea discovery · {runId}</h1>
            <p className="mt-1 text-sm text-slate-500">REST snapshot · round {snapshot.round_index} · updated {lastUpdated || "—"}</p>
          </div>
          <div className="flex gap-2"><button type="button" onClick={() => void refresh()} className="rounded-lg border border-mars-border bg-mars-panel px-3 py-2 text-xs text-slate-300 hover:border-slate-500">Refresh REST</button><Link href={`/runs/${encodeURIComponent(runId)}?agent=idea`} className="rounded-lg border border-mars-border bg-mars-panel px-3 py-2 text-xs text-slate-300 hover:border-slate-500">Classic run</Link></div>
        </header>

        <section className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <Metric label="Hypotheses" value={snapshot.hypotheses.length} />
          <Metric label="Legal" value={snapshot.hypotheses.filter((item) => !item.blocked).length} />
          <Metric label="Matches" value={snapshot.matches.length} />
          <Metric label="Top-K" value={snapshot.finalist_ids.length} />
          <Metric label="Budget" value={snapshot.config?.budget_profile ?? "—"} />
        </section>

        <nav aria-label="Discovery stages" className="mb-5 grid grid-cols-2 gap-2 rounded-2xl border border-mars-border bg-mars-panel p-2 sm:grid-cols-4 xl:grid-cols-7">
          {DISCOVERY_STAGES.map((stage, index) => (
            <button key={stage.id} type="button" onClick={() => setActiveStage(stage.id)} className={`rounded-xl px-3 py-2.5 text-left transition ${activeStage === stage.id ? "bg-indigo-500 text-white shadow-lg shadow-indigo-950/30" : "text-slate-400 hover:bg-mars-subtle hover:text-slate-100"}`}><span className="block font-mono text-[10px] opacity-60">0{index + 1}</span><span className="text-xs font-medium">{stage.short}</span></button>
          ))}
        </nav>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="min-w-0 space-y-5">
            <HypothesisPool
              defaultActor={actor}
              onCreate={addHypothesis}
              onSelect={setSelectedId}
              selectedId={selectedId}
              snapshot={snapshot}
            />
            <DiscoveryStagePanel stage={activeStage} snapshot={snapshot} />
          </div>
          <HypothesisReviewPanel
            hypothesis={selected}
            isFinalist={selected ? snapshot.finalist_ids.includes(selected.hypothesis_id) : false}
            actor={actor}
            reason={reason}
            statement={statement}
            busyAction={busyAction}
            message={actionMessage}
            onActorChange={setActor}
            onReasonChange={setReason}
            onStatementChange={(value) => {
              setStatement(value);
              setStatementDirty(true);
            }}
            onEdit={() => selected ? void mutate("edit", () => editHypothesis(runId, selected.hypothesis_id, { actor, reason, statement })) : undefined}
            onReject={() => selected ? void mutate("reject", () => rejectHypothesis(runId, selected.hypothesis_id, { actor, reason })) : undefined}
            onSelect={() => selected ? void mutate("select", () => selectHypothesis(runId, selected.hypothesis_id, { actor, reason })) : undefined}
          />
        </div>
      </div>
    </main>
  );
}

function HypothesisPool({
  defaultActor,
  onCreate,
  onSelect,
  selectedId,
  snapshot,
}: {
  defaultActor: string;
  onCreate: (input: HypothesisCreateInput) => Promise<HypothesisCreateAuditRecord>;
  onSelect: (id: string) => void;
  selectedId: string;
  snapshot: IdeaDiscoverySnapshot;
}): JSX.Element {
  const [adding, setAdding] = useState(false);
  const finalistIds = new Set(snapshot.finalist_ids);
  const hypotheses = [...snapshot.hypotheses].sort((left, right) => Number(finalistIds.has(right.hypothesis_id)) - Number(finalistIds.has(left.hypothesis_id)) || right.elo - left.elo);
  return <section className="rounded-2xl border border-mars-border bg-mars-panel p-5"><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-base font-semibold">Hypothesis pool</h2><p className="mt-1 text-xs text-slate-500">Top-K first; blocked candidates remain auditable but cannot be selected.</p></div><div className="flex items-center gap-2"><span className="font-mono text-xs text-slate-500">{hypotheses.length}</span><button type="button" onClick={() => setAdding((value) => !value)} className="rounded-lg border border-indigo-400/30 bg-indigo-500/10 px-3 py-2 text-xs font-medium text-indigo-200 transition hover:bg-indigo-500/20">{adding ? "Close add" : "Add hypothesis"}</button></div></div>{adding ? <AddHypothesisForm defaultActor={defaultActor} onCancel={() => setAdding(false)} onSubmit={onCreate} /> : null}<div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">{hypotheses.map((item) => <HypothesisCard key={item.hypothesis_id} item={item} selected={selectedId === item.hypothesis_id} finalist={finalistIds.has(item.hypothesis_id)} onClick={() => onSelect(item.hypothesis_id)} />)}</div></section>;
}

function HypothesisCard({ item, selected, finalist, onClick }: { item: DiscoveryHypothesis; selected: boolean; finalist: boolean; onClick: () => void }): JSX.Element {
  return <button type="button" onClick={onClick} className={`rounded-xl border p-4 text-left transition ${selected ? "border-indigo-400 bg-indigo-500/10 ring-2 ring-indigo-500/15" : item.blocked ? "border-rose-500/25 bg-rose-500/5" : "border-mars-border bg-mars-bg/55 hover:border-slate-500"}`}><div className="flex items-start justify-between gap-3"><span className="text-sm font-semibold text-slate-200">{item.mechanism}</span><span className="font-mono text-xs text-indigo-200">{item.elo.toFixed(1)}</span></div><p className="mt-2 line-clamp-4 text-xs leading-5 text-slate-400">{item.statement}</p><div className="mt-3 flex flex-wrap gap-1.5 text-[10px]"><span className="rounded bg-mars-panel px-2 py-0.5 text-slate-500">R{item.round_index}</span>{finalist ? <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-emerald-300">Top-K</span> : null}{item.blocked ? <span className="rounded bg-rose-500/15 px-2 py-0.5 text-rose-300">Blocked</span> : null}{item.parent_ids.length ? <span className="rounded bg-violet-500/15 px-2 py-0.5 text-violet-300">{item.operator ?? "evolve"}</span> : null}</div></button>;
}

function Metric({ label, value }: { label: string; value: string | number }): JSX.Element {
  return <div className="rounded-xl border border-mars-border bg-mars-panel px-4 py-3"><p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</p><p className="mt-1 font-mono text-lg text-slate-100">{value}</p></div>;
}

function StatusBadge({ status }: { status: string }): JSX.Element {
  return <span className="rounded-full bg-indigo-500/15 px-2.5 py-1 font-mono text-[10px] text-indigo-200">{status}</span>;
}

function LoadingView({ runId }: { runId: string }): JSX.Element {
  return <main className="grid min-h-screen place-items-center bg-mars-bg px-6 text-slate-300"><div className="text-center"><p className="text-sm font-semibold">Restoring Co-Scientist state</p><p className="mt-2 font-mono text-xs text-slate-600">{runId}</p></div></main>;
}

function CompatibilityView({ runId, reason, onRetry }: { runId: string; reason: string; onRetry: () => void }): JSX.Element {
  return <main className="grid min-h-screen place-items-center bg-mars-bg px-6 text-slate-100"><section className="w-full max-w-xl rounded-2xl border border-amber-400/35 bg-mars-panel p-6"><p className="text-xs font-semibold uppercase tracking-[0.2em] text-amber-300">V3.0 compatibility mode</p><h1 className="mt-3 text-xl font-semibold">Deep discovery view is unavailable</h1><p className="mt-3 text-sm leading-6 text-slate-400">{reason}</p><div className="mt-5 flex gap-2"><Link href={`/runs/${encodeURIComponent(runId)}?agent=idea`} className="rounded-lg bg-amber-400 px-4 py-2 text-sm font-semibold text-slate-950">Open classic Idea run</Link><button type="button" onClick={onRetry} className="rounded-lg border border-mars-border px-4 py-2 text-sm text-slate-300">Retry capability check</button></div></section></main>;
}

function ErrorView({ runId, message, onRetry }: { runId: string; message: string; onRetry: () => void }): JSX.Element {
  return <main className="grid min-h-screen place-items-center bg-mars-bg px-6 text-slate-100"><section className="w-full max-w-xl rounded-2xl border border-rose-500/35 bg-mars-panel p-6"><p className="text-xs font-semibold uppercase tracking-[0.2em] text-rose-300">REST recovery failed</p><h1 className="mt-3 text-xl font-semibold">Unable to restore {runId}</h1><p className="mt-3 break-words text-sm leading-6 text-slate-400">{message}</p><button type="button" onClick={onRetry} className="mt-5 rounded-lg border border-mars-border px-4 py-2 text-sm text-slate-300">Retry</button></section></main>;
}
