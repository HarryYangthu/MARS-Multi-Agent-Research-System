"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import {
  type GraphNode,
  approveArtifact,
  getRun,
  listRuns,
  STAGE_ORDER,
  STAGE_TO_STEM,
  STAGE_TO_TIER,
  type Readiness,
  type RunDetail,
  type Stage,
  type Stats,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useProject } from "@/lib/project";

const TIERS = [1, 2, 3, 4, 5] as const;
const STAGE_SET = new Set<string>(STAGE_ORDER);

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
    detail: "远程 API / 本地 vLLM",
  },
  execution: {
    schema: "run_log.v1",
    debateOn: false,
    detail: "最多 16 路并发",
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
  stats,
  readiness,
}: {
  selectedRunId: string | null;
  stats?: Stats | null;
  readiness?: Readiness | null;
}): JSX.Element {
  const { t } = useI18n();
  const { selectedProject } = useProject();
  const [details, setDetails] = useState<Record<string, RunDetail>>({});
  const [runIds, setRunIds] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const runs = await listRuns(selectedProject);
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
  }, [selectedProject]);

  const focusRun = useMemo(() => {
    const id = selectedRunId && details[selectedRunId] ? selectedRunId : runIds[0];
    return id ? details[id] ?? null : null;
  }, [details, runIds, selectedRunId]);

  // Group runs into a per-tier list of {runId, agentState}
  const tierRuns = useMemo(() => {
    const out: Record<number, AgentRuntimeCard[]> = {
      1: [],
      2: [],
      3: [],
      4: [],
      5: [],
    };
    for (const id of runIds) {
      const d = details[id];
      if (!d) continue;
      for (const node of d.graph.nodes) {
        const stage = stageFromNode(node);
        if (!stage) continue;
        const state = stateForNode(d, node);
        if (!state || state === "skipped") continue;
        // Always show every Agent for the selected run (or newest run by default)
        // so the full state machine remains observable during joint debugging.
        const isFocusRun = selectedRunId ? id === selectedRunId : id === runIds[0];
        const isActiveState =
          state === "running" ||
          state === "waiting_review" ||
          state === "approved" ||
          state === "failed";
        if (isFocusRun || isActiveState) {
          out[STAGE_TO_TIER[stage]].push({
            runId: id,
            agentState: state,
            project: d.project,
            task: d.task,
            currentWork: currentWorkText(stage, state),
            nodeKey: node.key,
            attempt: attemptFromNode(node),
          });
        }
      }
    }
    return out;
  }, [details, runIds, selectedRunId]);

  return (
    <main className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto bg-mars-bg/40 p-4">
      <StateMachineRibbon run={focusRun} />
      <SystemBar stats={stats ?? null} readiness={readiness ?? null} />
      {TIERS.map((tier, i) => {
        const stage = tierStage(tier);
        const cards = tierRuns[tier];
        const meta = AGENT_META[stage];
        return (
          <div
            key={tier}
            className={`relative rounded border bg-mars-panel/60 p-3 ${TIER_BORDER[tier]}`}
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
                    辩论开启
                  </span>
                ) : null}
                {meta.detail ? (
                  <span className="hidden rounded bg-mars-subtle px-1.5 py-0.5 text-[10px] text-slate-400 lg:inline">
                    {meta.detail}
                  </span>
                ) : null}
                <span className="rounded bg-mars-bg/60 px-1.5 py-0.5 text-[10px] text-slate-400">
                  {cards.length} · {cards.length > 0 ? t("layer.active") : t("layer.idle")}
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
                    currentWork={c.currentWork}
                    nodeKey={c.nodeKey}
                    attempt={c.attempt}
                  />
                ))
              )}
            </div>
            {i < TIERS.length - 1 ? (
              <div className="absolute -bottom-3 left-1/2 -translate-x-1/2 text-slate-600">
                <span className="block h-3 w-px rounded bg-slate-700" />
              </div>
            ) : null}
          </div>
        );
      })}
    </main>
  );
}

function StateMachineRibbon({ run }: { run: RunDetail | null }): JSX.Element {
  const activeNode = run ? currentGraphNode(run) : null;
  const activeStage = activeNode ? stageFromNode(activeNode) : null;
  const activeState = activeNode && run ? stateForNode(run, activeNode) : "pending";
  const nextNode = run && activeNode ? nextGraphNode(run, activeNode) : null;
  const previousNode = run && activeNode ? previousGraphNode(run, activeNode) : null;
  const retryNodes = run
    ? run.graph.nodes.filter((node) => stageFromNode(node) && attemptFromNode(node) > 1)
    : [];
  const previousHop = activeNode
    ? previousNode
      ? `${nodeAgentLabel(previousNode)} → ${nodeAgentLabel(activeNode)}`
      : `Commander Agent → ${nodeAgentLabel(activeNode)}`
    : "等待启动";
  const commanderState = run ? "configured" : "pending";
  return (
    <section className="rounded border border-mars-border bg-mars-panel/80 px-3 py-3 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] tracking-[0.18em] text-slate-500">状态机</p>
          <h2 className="mt-1 truncate text-sm font-semibold text-slate-100">
            {run ? run.task || run.run_id : "等待 Run 数据"}
          </h2>
          <p className="mt-0.5 truncate font-mono text-[10px] text-slate-500">
            {run ? `${run.project} · ${run.run_id}` : "创建或选择一个 Run 后展示状态机"}
          </p>
        </div>
        <div className="grid min-w-[220px] gap-1 text-[11px] text-slate-400 sm:text-right">
          <p>
            当前节点：
            <span className="font-mono text-cyan-200">
              {activeNode ? nodeAgentLabel(activeNode) : "无"}
            </span>
          </p>
          <p>
            当前动作：
            <span className="text-slate-200">
              {activeStage ? currentWorkText(activeStage, activeState) : "暂无运行"}
            </span>
          </p>
          <p>
            观测细节：
            <span className="text-slate-200">
              {activeStage ? thinkingText(activeStage, activeState) : "等待事件、Trace、上下文清单"}
            </span>
          </p>
        </div>
      </div>

      <div className="mb-3 flex flex-col items-center">
        <div
          className={`mars-node-card w-full max-w-sm rounded border px-3 py-2 ${
            run
              ? "mars-node-active border-cyan-400/60 bg-cyan-500/10"
              : "border-mars-border bg-mars-bg/45"
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-[11px] text-slate-100">
              Commander Agent
            </span>
            <span className={`h-2 w-2 rounded-full ${stateDotClass(commanderState, Boolean(run))}`} />
          </div>
          <p className="mt-1 truncate text-[10px] text-slate-400">
            主控调度 / 状态监督 / 反馈闭环
          </p>
          <p className="mt-1 truncate text-[9px] text-slate-600">
            entry · routing · gate orchestration
          </p>
        </div>
        <div className={`h-4 w-px ${run ? "bg-cyan-400/50" : "bg-mars-subtle"}`} />
        <div className={`h-px w-full max-w-3xl ${run ? "bg-cyan-400/25" : "bg-mars-subtle"}`} />
      </div>

      <div className="grid grid-cols-5 gap-2">
        {STAGE_ORDER.map((stage, index) => {
          const node = run ? latestNodeForStage(run, stage) : null;
          const state = run && node ? stateForNode(run, node) : "pending";
          const isActive = activeNode?.key === node?.key;
          const isDone = state === "done" || state === "approved";
          const connectorActive = activeStage
            ? index === STAGE_ORDER.indexOf(activeStage) || index === STAGE_ORDER.indexOf(activeStage) - 1
            : false;
          return (
            <div key={stage} className="min-w-0">
              <div
                className={`mars-node-card ${isActive ? "mars-node-active" : ""} rounded border px-2 py-2 ${
                  isActive
                    ? "border-cyan-400/60 bg-cyan-500/10"
                    : isDone
                      ? "border-emerald-500/35 bg-emerald-500/10"
                      : state === "failed"
                        ? "border-red-500/40 bg-red-500/10"
                        : "border-mars-border bg-mars-bg/45"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[11px] text-slate-100">
                    {agentName(stage)}
                  </span>
                  <span className={`h-2 w-2 rounded-full ${stateDotClass(state, isActive)}`} />
                </div>
                <p className="mt-1 truncate text-[10px] text-slate-400">
                  {stateShortLabel(state)}
                </p>
                <p className="mt-1 truncate text-[9px] text-slate-600">
                  {node ? node.key : stage}
                </p>
              </div>
              {index < STAGE_ORDER.length - 1 ? (
                <div
                  className={`mars-flow-line mt-2 h-1 rounded bg-mars-subtle ${
                    connectorActive ? "mars-flow-active" : ""
                  }`}
                />
              ) : null}
            </div>
          );
        })}
      </div>

      {retryNodes.length > 0 ? (
        <div className="mt-3 rounded border border-amber-500/25 bg-amber-500/10 px-3 py-2">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-amber-100">
              Commander Agent 回溯链路
            </span>
            <span className="font-mono text-[10px] text-amber-200">
              {retryNodes.length} 个动态节点
            </span>
          </div>
          <div className="grid gap-1.5 md:grid-cols-2 xl:grid-cols-4">
            {retryNodes.map((node) => (
              <div key={node.key} className="min-w-0 rounded border border-amber-500/20 bg-mars-bg/50 px-2 py-1.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[10px] text-amber-100">
                    {nodeAgentLabel(node)}
                  </span>
                  <span className={`h-2 w-2 rounded-full ${stateDotClass(run ? stateForNode(run, node) : "pending", activeNode?.key === node.key)}`} />
                </div>
                <p className="mt-1 truncate text-[9px] text-slate-500">
                  {node.key} · {run ? stateShortLabel(stateForNode(run, node)) : "待处理"}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-3 grid gap-2 text-[11px] md:grid-cols-3">
        <DetailPill label="上一跳" value={previousHop} />
        <DetailPill label="当前状态" value={activeNode ? `${nodeAgentLabel(activeNode)} · ${stateShortLabel(activeState)}` : "未开始"} />
        <DetailPill label="下一步" value={nextNode && run ? `${nodeAgentLabel(nextNode)} · ${stateShortLabel(stateForNode(run, nextNode))}` : "沉淀归档"} />
      </div>
    </section>
  );
}

function DetailPill({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="min-w-0 rounded border border-mars-border bg-mars-bg/50 px-2 py-1.5">
      <p className="text-[9px] text-slate-500">{label}</p>
      <p className="mt-0.5 truncate font-mono text-[10px] text-slate-200">{value}</p>
    </div>
  );
}

function SystemBar({
  stats,
  readiness,
}: {
  stats: Stats | null;
  readiness: Readiness | null;
}): JSX.Element {
  const ready = readiness?.ready ?? false;
  const runtimeLabel = readiness
    ? `${readiness.runtime_mode}/${readiness.execution_backend}`
    : "检查中";
  const blockers =
    readiness?.checks.filter((check) => check.severity === "blocker" && !check.ready).length ?? 0;
  const running = stats?.runs_running ?? 0;
  const waiting = stats?.runs_waiting_review ?? 0;
  const failed = stats?.runs_failed ?? 0;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded border border-mars-border bg-mars-panel/80 px-3 py-2 text-[11px] text-slate-400">
      <span
        className={`rounded px-2 py-1 font-mono ${
          ready ? "bg-emerald-500/20 text-emerald-300" : "bg-amber-500/20 text-amber-200"
        }`}
      >
        运行态 {runtimeLabel}
      </span>
      <span className="rounded bg-mars-subtle px-2 py-1 font-mono text-slate-300">
        运行中 {running}
      </span>
      <span
        className={`rounded px-2 py-1 font-mono ${
          waiting > 0 ? "bg-fuchsia-500/20 text-fuchsia-200" : "bg-mars-subtle text-slate-300"
        }`}
      >
        待审核 {waiting}
      </span>
      <span
        className={`rounded px-2 py-1 font-mono ${
          failed > 0 ? "bg-red-500/20 text-red-200" : "bg-mars-subtle text-slate-300"
        }`}
      >
        异常 {failed}
      </span>
      {blockers > 0 ? (
        <span className="rounded bg-amber-500/15 px-2 py-1 font-mono text-amber-200">
          阻塞 {blockers}
        </span>
      ) : null}
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
  currentWork,
  nodeKey,
  attempt,
}: {
  runId: string;
  project: string;
  task: string;
  state: string;
  tier: number;
  selected: boolean;
  label: string;
  stage: Stage;
  currentWork: string;
  nodeKey: string;
  attempt: number;
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
        <span className="font-mono text-[10px] text-slate-300">{label}</span>
        <span className="truncate font-mono text-[9px] text-slate-500">
          {attempt > 1 ? `第 ${attempt} 轮` : project}
        </span>
      </div>
      <p className="mt-1 truncate text-[11px] text-slate-200">{task || runId}</p>
      <p className="mt-1 truncate font-mono text-[9px] text-cyan-200">{nodeKey}</p>
      <p className="mt-1 truncate text-[10px] text-slate-400">{currentWork}</p>
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

type AgentRuntimeCard = {
  runId: string;
  agentState: string;
  project: string;
  task: string;
  currentWork: string;
  nodeKey: string;
  attempt: number;
};

function graphNodeForStage(run: RunDetail, stage: Stage): GraphNode | null {
  return latestNodeForStage(run, stage);
}

function isStage(value: unknown): value is Stage {
  return typeof value === "string" && STAGE_SET.has(value);
}

function stageFromNode(node: GraphNode): Stage | null {
  const metadataStage = node.metadata.stage;
  if (isStage(metadataStage)) return metadataStage;
  if (isStage(node.key)) return node.key;
  const match = /^([a-z_]+)_attempt_[1-9][0-9]*$/.exec(node.key);
  return match && isStage(match[1]) ? match[1] : null;
}

function attemptFromNode(node: GraphNode): number {
  const raw = node.metadata.attempt;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return Math.max(1, Math.floor(raw));
  }
  if (typeof raw === "string") {
    const parsed = Number.parseInt(raw, 10);
    if (Number.isFinite(parsed)) return Math.max(1, parsed);
  }
  const match = /_attempt_([1-9][0-9]*)$/.exec(node.key);
  return match ? Number.parseInt(match[1], 10) : 1;
}

function stateForNode(run: RunDetail, node: GraphNode): string {
  const stage = stageFromNode(node);
  return run.states[node.key] ?? (stage ? run.states[stage] : undefined) ?? node.state ?? "pending";
}

function nodesForStage(run: RunDetail, stage: Stage): GraphNode[] {
  return run.graph.nodes
    .filter((node) => stageFromNode(node) === stage)
    .sort((a, b) => attemptFromNode(a) - attemptFromNode(b));
}

function latestNodeForStage(run: RunDetail, stage: Stage): GraphNode | null {
  const nodes = nodesForStage(run, stage);
  return nodes[nodes.length - 1] ?? null;
}

function currentGraphNode(run: RunDetail): GraphNode | null {
  const priority = ["running", "waiting_review", "failed", "approved", "pending"] as const;
  for (const targetState of priority) {
    const found = run.graph.nodes.find((node) => stateForNode(run, node) === targetState);
    if (found && stageFromNode(found)) return found;
  }
  return run.graph.nodes.filter((node) => stageFromNode(node)).at(-1) ?? null;
}

function previousGraphNode(run: RunDetail, node: GraphNode): GraphNode | null {
  const incoming = run.graph.edges
    .filter((edge) => edge.dst === node.key)
    .map((edge) => run.graph.nodes.find((candidate) => candidate.key === edge.src) ?? null)
    .filter((candidate): candidate is GraphNode => candidate !== null && stageFromNode(candidate) !== null);
  return incoming.at(-1) ?? null;
}

function nextGraphNode(run: RunDetail, node: GraphNode): GraphNode | null {
  const outgoing = run.graph.edges
    .filter((edge) => edge.src === node.key)
    .map((edge) => run.graph.nodes.find((candidate) => candidate.key === edge.dst) ?? null)
    .filter((candidate): candidate is GraphNode => candidate !== null && stageFromNode(candidate) !== null);
  const active = outgoing.find((candidate) => !["done", "skipped"].includes(stateForNode(run, candidate)));
  return active ?? outgoing.at(-1) ?? null;
}

function nodeAgentLabel(node: GraphNode): string {
  const stage = stageFromNode(node);
  if (!stage) return node.key;
  const attempt = attemptFromNode(node);
  return attempt > 1 ? `${agentName(stage)} · 第 ${attempt} 轮` : agentName(stage);
}

function currentWorkText(stage: Stage, state: string): string {
  if (state === "waiting_review") return "草稿已生成，等待人工审核/批准";
  if (state === "approved") return "已批准，准备交接给下游节点";
  if (state === "done") return "已完成，正在沉淀产物和记忆";
  if (state === "failed") return "执行失败，等待诊断和反馈回路";
  if (state !== "running") return "等待上游节点或人工启动";
  const running: Record<Stage, string> = {
    idea: "装载文献/记忆，生成研究假设",
    experiment: "匹配 baseline，设计实验矩阵",
    coding: "读取代码仓，生成 patch，并触发 Gate 5 检查",
    execution: "调度仿真，收集日志、曲线和指标",
    writing: "汇总全链路产物，生成研究报告",
  };
  return running[stage];
}

function thinkingText(stage: Stage, state: string): string {
  if (state === "running") {
    return stage === "idea" || stage === "writing"
      ? "辩论转录、Trace 和上下文清单实时写入详情页"
      : "Trace、工具调用、上下文清单实时写入详情页";
  }
  if (state === "waiting_review") return "打开详情页查看草稿、评审意见和 Trace";
  return "打开详情页查看历史事件、Trace 和上下文清单";
}

function currentStage(run: RunDetail): Stage {
  const priority = ["running", "waiting_review", "failed", "approved"] as const;
  for (const targetState of priority) {
    const found = STAGE_ORDER.find((stage) => run.states[stage] === targetState);
    if (found) return found;
  }
  const firstPending = STAGE_ORDER.find((stage) => run.states[stage] === "pending");
  if (firstPending) return firstPending;
  return STAGE_ORDER[STAGE_ORDER.length - 1];
}

function previousPipelineStage(stage: Stage): Stage | null {
  const index = STAGE_ORDER.indexOf(stage);
  return index > 0 ? STAGE_ORDER[index - 1] : null;
}

function nextPipelineStage(stage: Stage): Stage | null {
  const index = STAGE_ORDER.indexOf(stage);
  return index >= 0 && index < STAGE_ORDER.length - 1 ? STAGE_ORDER[index + 1] : null;
}

function agentName(stage: Stage): string {
  const labels: Record<Stage, string> = {
    idea: "Idea Agent",
    experiment: "Experiment Agent",
    coding: "Coding Agent",
    execution: "Execution Agent",
    writing: "Writing Agent",
  };
  return labels[stage];
}

function stateDotClass(state: string, isActive: boolean): string {
  if (isActive) return "bg-cyan-300 shadow-[0_0_12px_rgba(103,232,249,0.75)]";
  if (state === "done" || state === "approved") return "bg-emerald-300";
  if (state === "running") return "bg-amber-300";
  if (state === "waiting_review") return "bg-fuchsia-300";
  if (state === "failed") return "bg-red-300";
  return "bg-slate-600";
}

function stateShortLabel(state: string): string {
  const labels: Record<string, string> = {
    pending: "待处理",
    running: "运行中",
    waiting_review: "待审",
    approved: "已批",
    done: "完成",
    failed: "失败",
    skipped: "跳过",
    configured: "已配置",
  };
  return labels[state] ?? state;
}
