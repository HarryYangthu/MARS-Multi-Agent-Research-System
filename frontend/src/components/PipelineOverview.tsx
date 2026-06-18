"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
        const fetchIds =
          selectedRunId && !ids.includes(selectedRunId)
            ? [selectedRunId, ...ids]
            : ids;
        setRunIds(ids);
        const fetched = await Promise.all(fetchIds.map((id) => getRun(id).catch(() => null)));
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
  }, [selectedProject, selectedRunId]);

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
    const visibleRunIds =
      selectedRunId && !runIds.includes(selectedRunId)
        ? [selectedRunId, ...runIds]
        : runIds;
    for (const id of visibleRunIds) {
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
    <div className="flex h-full min-h-0 flex-1 flex-col gap-3 overflow-auto bg-mars-bg/40 p-4">
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
    </div>
  );
}

function StateMachineRibbon({ run }: { run: RunDetail | null }): JSX.Element {
  const activeNode = run ? currentGraphNode(run) : null;
  const activeStage = activeNode ? stageFromNode(activeNode) : null;
  const activeState = activeNode && run ? stateForNode(run, activeNode) : "pending";
  const communication = activeNode ? commanderCommunicationForNode(activeNode, activeState) : null;
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
            通信对象：
            <span className="font-mono text-cyan-200">
              {communication ? communication.routeLabel : "无"}
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
        {(() => {
          const commanderCard = (
            <div
              className={`mars-node-card w-full max-w-md rounded-lg border-2 px-4 py-3 transition ${
                run
                  ? "mars-node-active border-cyan-400/80 bg-cyan-500/15 shadow-[0_0_26px_-6px_rgba(34,211,238,0.65)] hover:border-cyan-300"
                  : "border-cyan-500/30 bg-cyan-500/5"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2">
                  <span className="font-semibold text-cyan-50">Commander Agent</span>
                  <span className="rounded bg-cyan-500/25 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-cyan-100">
                    主控
                  </span>
                </span>
                <span className={`h-2.5 w-2.5 rounded-full ${stateDotClass(commanderState, Boolean(run))}`} />
              </div>
              <p className="mt-1.5 truncate text-[11px] text-cyan-100/80">
                {communication ? `正在通信：${nodeAgentLabel(communication.targetNode)}` : "主控调度 · 状态监督 · 诊断与反馈回路"}
              </p>
              <p className="mt-0.5 truncate text-[9px] text-cyan-200/50">
                {communication ? communication.detail : run ? "点击进入主控页 · entry / routing / gate orchestration" : "entry · routing · gate orchestration"}
              </p>
            </div>
          );
          return run ? (
            <Link href={`/runs/${run.run_id}?agent=commander`} className="w-full max-w-md">
              {commanderCard}
            </Link>
          ) : (
            commanderCard
          );
        })()}
        <CommanderConnectionField communication={communication} />
      </div>

      <div className="mars-agent-map">
        <AgentConnectionField run={run} activeNode={activeNode} previousNode={previousNode} />
        <div className="mars-agent-grid grid grid-cols-5 gap-4">
          {STAGE_ORDER.map((stage) => {
            const node = run ? latestNodeForStage(run, stage) : null;
            const state = run && node ? stateForNode(run, node) : "pending";
            const isActive = activeNode?.key === node?.key;
            const isCommunicationTarget = Boolean(
              communication && node && communication.targetNode.key === node.key,
            );
            const isWorking = isActive || isCommunicationTarget || state === "running";
            const isDone = state === "done" || state === "approved";
            return (
              <div key={stage} className="min-w-0">
                <div
                  className={`mars-node-card ${isActive ? "mars-node-active" : ""} ${isWorking ? "mars-node-working" : ""} ${isCommunicationTarget ? "mars-node-comm-target" : ""} rounded border px-2 py-2 ${
                    isActive
                      ? "border-cyan-400/60 bg-cyan-500/10"
                      : isDone
                        ? "border-emerald-500/35 bg-emerald-500/10"
                        : state === "failed"
                          ? "border-red-500/40 bg-red-500/10"
                          : "border-mars-border bg-mars-bg/45"
                  }`}
                >
                  {isWorking ? <NodeBorderFlow /> : null}
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[11px] text-slate-100">
                      {agentName(stage)}
                    </span>
                    <span className="flex items-center gap-1">
                      {isCommunicationTarget ? (
                        <span className="rounded bg-cyan-500/20 px-1 py-0.5 text-[8px] text-cyan-100">
                          通信中
                        </span>
                      ) : null}
                      <span className={`h-2 w-2 rounded-full ${stateDotClass(state, isActive || isCommunicationTarget)}`} />
                    </span>
                  </div>
                  <p className="mt-1 truncate text-[10px] text-slate-400">
                    {stateShortLabel(state)}
                  </p>
                  <p className="mt-1 truncate text-[9px] text-slate-600">
                    {node ? node.key : stage}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
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
            {retryNodes.map((node, index) => (
              <div key={`${node.key}-${index}`} className="min-w-0 rounded border border-amber-500/20 bg-mars-bg/50 px-2 py-1.5">
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

function NodeBorderFlow(): JSX.Element {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [box, setBox] = useState({ width: 100, height: 64 });

  useEffect(() => {
    const parent = svgRef.current?.parentElement;
    if (!parent) return;

    const syncBox = (): void => {
      const rect = parent.getBoundingClientRect();
      setBox({
        width: Math.max(1, rect.width),
        height: Math.max(1, rect.height),
      });
    };

    syncBox();
    const observer = new ResizeObserver(syncBox);
    observer.observe(parent);
    return () => observer.disconnect();
  }, []);

  const inset = 1;
  const width = Math.max(inset * 2, box.width);
  const height = Math.max(inset * 2, box.height);
  const radius = 4;

  return (
    <svg
      ref={svgRef}
      className="mars-node-border-flow"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <rect
        className="mars-node-border-flow-base"
        x={inset}
        y={inset}
        width={width - inset * 2}
        height={height - inset * 2}
        rx={radius}
        ry={radius}
        pathLength={100}
      />
      <rect
        className="mars-node-border-flow-tail"
        x={inset}
        y={inset}
        width={width - inset * 2}
        height={height - inset * 2}
        rx={radius}
        ry={radius}
        pathLength={100}
      />
      <rect
        className="mars-node-border-flow-mid"
        x={inset}
        y={inset}
        width={width - inset * 2}
        height={height - inset * 2}
        rx={radius}
        ry={radius}
        pathLength={100}
      />
      <rect
        className="mars-node-border-flow-head"
        x={inset}
        y={inset}
        width={width - inset * 2}
        height={height - inset * 2}
        rx={radius}
        ry={radius}
        pathLength={100}
      />
    </svg>
  );
}

type AgentEdge = {
  source: Stage;
  target: Stage;
};

type CommanderCommunication = {
  targetNode: GraphNode;
  targetStage: Stage;
  routeLabel: string;
  detail: string;
};

function CommanderConnectionField({
  communication,
}: {
  communication: CommanderCommunication | null;
}): JSX.Element {
  return (
    <div className="mars-comm-links w-full" aria-hidden="true">
      <svg className="mars-comm-links-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
      {STAGE_ORDER.map((stage, index) => {
        const isActive = communication?.targetStage === stage;
        return (
          <g
            key={stage}
            className={`mars-comm-link ${isActive ? "mars-comm-link-active" : ""}`}
          >
            <path className="mars-comm-link-base" d={commanderConnectionPath(index)} pathLength={100} />
            {isActive ? (
              <path className="mars-comm-link-flow" d={commanderConnectionPath(index)} pathLength={100} />
            ) : null}
          </g>
        );
      })}
      </svg>
    </div>
  );
}

function AgentConnectionField({
  run,
  activeNode,
  previousNode,
}: {
  run: RunDetail | null;
  activeNode: GraphNode | null;
  previousNode: GraphNode | null;
}): JSX.Element {
  const edges = agentConnectionEdges(run);
  const activeSource = previousNode ? stageFromNode(previousNode) : null;
  const activeTarget = activeNode ? stageFromNode(activeNode) : null;
  return (
    <div className="mars-agent-links" aria-hidden="true">
      <svg className="mars-agent-links-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
        {edges.map((edge) => {
          const active = activeSource === edge.source && activeTarget === edge.target;
          const key = `${edge.source}-${edge.target}`;
          return (
            <g key={key} className={`mars-agent-link ${active ? "mars-agent-link-active" : ""}`}>
              <path className="mars-agent-link-base" d={agentConnectionPath(edge)} pathLength={100} />
              {active ? (
                <path className="mars-agent-link-flow" d={agentConnectionPath(edge)} pathLength={100} />
              ) : null}
            </g>
          );
        })}
      </svg>
    </div>
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
      href={`/runs/${runId}?agent=${stage}`}
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
  const terminalKeys = new Set(run.graph.nodes.map((node) => node.key));
  for (const edge of run.graph.edges) {
    terminalKeys.delete(edge.src);
  }
  const terminalNode = run.graph.nodes.find(
    (node) => terminalKeys.has(node.key) && stageFromNode(node) !== null,
  );
  return terminalNode ?? run.graph.nodes.filter((node) => stageFromNode(node)).at(-1) ?? null;
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

function agentConnectionEdges(run: RunDetail | null): AgentEdge[] {
  const edges = new Map<string, AgentEdge>();
  for (let i = 0; i < STAGE_ORDER.length - 1; i += 1) {
    const edge = { source: STAGE_ORDER[i], target: STAGE_ORDER[i + 1] };
    edges.set(`${edge.source}-${edge.target}`, edge);
  }
  if (!run) return [...edges.values()];
  for (const graphEdge of run.graph.edges) {
    const sourceNode = run.graph.nodes.find((node) => node.key === graphEdge.src);
    const targetNode = run.graph.nodes.find((node) => node.key === graphEdge.dst);
    if (!sourceNode || !targetNode) continue;
    const source = stageFromNode(sourceNode);
    const target = stageFromNode(targetNode);
    if (!source || !target || source === target) continue;
    edges.set(`${source}-${target}`, { source, target });
  }
  return [...edges.values()].sort((a, b) => {
    const ai = STAGE_ORDER.indexOf(a.source);
    const bi = STAGE_ORDER.indexOf(b.source);
    if (ai !== bi) return ai - bi;
    return STAGE_ORDER.indexOf(a.target) - STAGE_ORDER.indexOf(b.target);
  });
}

function agentConnectionPath(edge: AgentEdge): string {
  const sourceIndex = STAGE_ORDER.indexOf(edge.source);
  const targetIndex = STAGE_ORDER.indexOf(edge.target);
  const forward = targetIndex > sourceIndex;
  const sourceX = agentCardEdgeX(sourceIndex, forward ? "right" : "left");
  const targetX = agentCardEdgeX(targetIndex, forward ? "left" : "right");
  if (Math.abs(targetIndex - sourceIndex) === 1) {
    return `M ${sourceX} 50 L ${targetX} 50`;
  }
  const controlY = forward ? 14 : 86;
  return `M ${sourceX} 50 C ${sourceX} ${controlY} ${targetX} ${controlY} ${targetX} 50`;
}

function agentCardEdgeX(index: number, side: "left" | "right"): number {
  const slot = 100 / STAGE_ORDER.length;
  const gapInset = 1.35;
  return side === "left" ? index * slot + gapInset : (index + 1) * slot - gapInset;
}

function commanderCommunicationForNode(node: GraphNode, state: string): CommanderCommunication | null {
  const stage = stageFromNode(node);
  if (!stage) return null;
  return {
    targetNode: node,
    targetStage: stage,
    routeLabel: `Commander Agent → ${nodeAgentLabel(node)}`,
    detail: commanderCommunicationDetail(stage, state),
  };
}

function commanderConnectionPath(index: number): string {
  const targetX = ((index + 0.5) / STAGE_ORDER.length) * 100;
  const midY = targetX === 50 ? 54 : 42;
  return `M 50 0 C 50 24 ${targetX} ${midY} ${targetX} 100`;
}

function commanderCommunicationDetail(stage: Stage, state: string): string {
  if (state === "running") return `下发任务 · 接收 ${agentName(stage)} 事件流`;
  if (state === "waiting_review") return `等待人工审核 · 同步 ${agentName(stage)} 草稿`;
  if (state === "failed") return `拉取失败证据 · 准备反馈给 ${agentName(stage)}`;
  if (state === "done" || state === "approved") return `同步完成状态 · 沉淀 ${agentName(stage)} 产物`;
  return `等待 ${agentName(stage)} 接入状态机`;
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
