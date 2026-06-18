"use client";

/* eslint-disable react/jsx-no-undef */

import { Suspense, use, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { AgentContextPanel } from "@/components/AgentContextPanel";
import { CodingWorkspacePanel } from "@/components/CodingWorkspacePanel";
import {
  approveToolCall,
  approveArtifact,
  approveMemoryCandidate,
  approveSelfEvolutionMutation,
  approvePatch,
  createPostTrainingExport,
  createSelfEvolutionMutation,
  editArtifact,
  getArtifact,
  getArtifactEvaluationSummary,
  getCommanderAttributionEval,
  getCommanderObservability,
  getDebateTranscript,
  getEvaluationScorecard,
  getPostTrainingExport,
  getPatch,
  getRun,
  getRunObservability,
  getSelfEvolutionLevers,
  getTrace,
  executionPlotUrl,
  listArtifactEvaluations,
  listDiagnoses,
  listEpisodeMemory,
  listExecutionPlots,
  listFeedbackPackets,
  listMemoryCandidates,
  listRunToolCalls,
  listSelfEvolutionMutations,
  listToolAdapters,
  listToolApprovals,
  markMemoryCandidateStale,
  pendingReviews,
  rejectArtifact,
  rejectSelfEvolutionMutation,
  rejectToolCall,
  rejectMemoryCandidate,
  rejectPatch,
  rollbackToolCall,
  startFeedbackLoop,
  supersedeMemoryCandidate,
  type ArtifactEvaluationReport,
  type ArtifactEvaluationSummary,
  type ArtifactView,
  type CommanderAttributionEvalView,
  type CommanderObservabilityView,
  type DebateTranscript,
  type DiagnosisView,
  type EvaluationDecision,
  type EvaluationFinding,
  type EvaluationPolicyDecision,
  type EvaluationReportItem,
  type EvaluationScorecard,
  type ExecutionPlot,
  type FeedbackLoopStartResult,
  type FeedbackPacketView,
  type McpAdapterStatus,
  type PatchView,
  type PostTrainingExportManifest,
  type RunMemoryEventView,
  type RunObservabilityView,
  type SelfEvolutionLeversView,
  type SelfEvolutionLeverItem,
  type RunDetail,
  type ToolAuditEntry,
  type ToolApprovalRecord,
  type TraceManifest,
  type TraceSpan,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { openRunSocket, type WSMessage } from "@/lib/socket";

const STAGE_TO_STEM: Record<string, { stem: string; agentDir: string }> = {
  commander: { stem: "diagnosis", agentDir: "diagnosis" },
  idea: { stem: "idea_proposal", agentDir: "idea" },
  experiment: { stem: "experiment_plan", agentDir: "experiment" },
  coding: { stem: "code_spec", agentDir: "coding" },
  execution: { stem: "run_log", agentDir: "execution" },
  writing: { stem: "research_report", agentDir: "writing" },
};
const PIPELINE_STAGES = ["idea", "experiment", "coding", "execution", "writing"] as const;
const AGENT_NAV = ["commander", ...PIPELINE_STAGES] as const;

function splitFrontmatter(text: string): { frontmatter: string; body: string } {
  const match = /^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/.exec(text);
  if (!match) {
    return { frontmatter: "", body: text };
  }
  return { frontmatter: match[1], body: match[2] };
}

function RunDetailPageInner({ params }: { params: Promise<{ id: string }> }): JSX.Element {
  const { id: runId } = use(params);
  const { t } = useI18n();
  // Deep-link support: /runs/<id>?agent=<stage> opens straight to that agent.
  // Clicking an agent card on the dashboard now lands on THAT agent, not always
  // the Commander; an explicit ?agent=commander opens the Commander.
  const searchParams = useSearchParams();
  const agentParam = searchParams?.get("agent");
  const initialAgent =
    agentParam && (AGENT_NAV as readonly string[]).includes(agentParam)
      ? agentParam
      : "commander";
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<WSMessage[]>([]);
  const [activeAgent, setActiveAgent] = useState<string>(initialAgent);
  const [artifact, setArtifact] = useState<ArtifactView | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [debate, setDebate] = useState<DebateTranscript | null>(null);
  const [debateOpen, setDebateOpen] = useState(false);
  const [patch, setPatch] = useState<PatchView | null>(null);
  const [trace, setTrace] = useState<TraceManifest | null>(null);
  const [viewMode, setViewMode] = useState<"artifact" | "context" | "workspace">("artifact");
  const [artifactEvaluations, setArtifactEvaluations] = useState<ArtifactEvaluationReport[]>([]);
  const [scorecard, setScorecard] = useState<EvaluationScorecard | null>(null);
  const [postTrainingExport, setPostTrainingExport] =
    useState<PostTrainingExportManifest | null>(null);
  const [postTrainingExportMessage, setPostTrainingExportMessage] = useState<string>("");
  const [liveEvaluationSummaries, setLiveEvaluationSummaries] =
    useState<Record<string, ArtifactEvaluationSummary>>({});
  const [diagnoses, setDiagnoses] = useState<DiagnosisView[]>([]);
  const [feedbackPackets, setFeedbackPackets] = useState<FeedbackPacketView[]>([]);
  const [memoryCandidates, setMemoryCandidates] = useState<RunMemoryEventView | null>(null);
  const [episodeMemory, setEpisodeMemory] = useState<RunMemoryEventView | null>(null);
  const [selfEvolutionLevers, setSelfEvolutionLevers] =
    useState<SelfEvolutionLeversView | null>(null);
  const [selfEvolutionMutations, setSelfEvolutionMutations] =
    useState<RunMemoryEventView | null>(null);
  const [feedbackAction, setFeedbackAction] = useState<FeedbackLoopStartResult | null>(null);
  const [commanderObservability, setCommanderObservability] =
    useState<CommanderObservabilityView | null>(null);
  const [commanderEval, setCommanderEval] = useState<CommanderAttributionEvalView | null>(null);
  const [runObservability, setRunObservability] = useState<RunObservabilityView | null>(null);
  const [toolCalls, setToolCalls] = useState<ToolAuditEntry[]>([]);
  const [toolApprovals, setToolApprovals] = useState<ToolApprovalRecord[]>([]);
  const [toolAdapters, setToolAdapters] = useState<McpAdapterStatus[]>([]);
  const [toolActionMessage, setToolActionMessage] = useState<string>("");
  const [toolFilter, setToolFilter] = useState<string>("");
  const [toolStatusFilter, setToolStatusFilter] = useState<string>("");
  const [toolEventFilter, setToolEventFilter] = useState<string>("");
  const [toolCallIdFilter, setToolCallIdFilter] = useState<string>("");
  const [toolLimit, setToolLimit] = useState<number>(80);

  useEffect(() => {
    let alive = true;
    const refreshCommander = (): void => {
      void Promise.all([
        listDiagnoses(runId).catch(() => []),
        listFeedbackPackets(runId).catch(() => []),
        listMemoryCandidates(runId).catch(() => null),
        listEpisodeMemory(runId).catch(() => null),
        getSelfEvolutionLevers(runId).catch(() => null),
        listSelfEvolutionMutations(runId).catch(() => null),
        getCommanderObservability(runId).catch(() => null),
        getRunObservability(runId).catch(() => null),
      ]).then(([nextDiagnoses, nextPackets, nextCandidates, nextEpisode, nextLevers, nextMutations, nextObservability, nextRunObservability]) => {
        if (!alive) return;
        setDiagnoses(nextDiagnoses);
        setFeedbackPackets(nextPackets);
        setMemoryCandidates(nextCandidates);
        setEpisodeMemory(nextEpisode);
        setSelfEvolutionLevers(nextLevers);
        setSelfEvolutionMutations(nextMutations);
        setCommanderObservability(nextObservability);
        setRunObservability(nextRunObservability);
      });
    };
    const refreshTools = (): void => {
      void Promise.all([
        listRunToolCalls(runId, {
          tool: toolFilter || undefined,
          status: toolStatusFilter || undefined,
          event: toolEventFilter || undefined,
          callId: toolCallIdFilter || undefined,
          limit: toolLimit,
        }).catch(() => []),
        listToolApprovals(runId).catch(() => []),
        listToolAdapters().catch(() => []),
      ]).then(([nextCalls, nextApprovals, nextAdapters]) => {
        if (!alive) return;
        setToolCalls(nextCalls);
        setToolApprovals(nextApprovals);
        setToolAdapters(nextAdapters);
      });
    };
    void getRun(runId).then((r) => {
      if (alive) setRun(r);
    });
    void getTrace(runId)
      .then((tr) => {
        if (alive) setTrace(tr);
      })
      .catch(() => undefined);
    void getCommanderAttributionEval(run?.project ?? "moe-pimc")
      .then((result) => {
        if (alive) setCommanderEval(result);
      })
      .catch(() => undefined);
    const refreshScorecard = (): void => {
      void getEvaluationScorecard(runId)
        .then((result) => {
          if (alive) setScorecard(result);
        })
        .catch(() => undefined);
    };
    const refreshPostTrainingExport = (): void => {
      void getPostTrainingExport(runId)
        .then((result) => {
          if (alive) setPostTrainingExport(result);
        })
        .catch(() => undefined);
    };
    refreshCommander();
    refreshTools();
    refreshScorecard();
    refreshPostTrainingExport();

    const closeWS = openRunSocket(runId, (msg) => {
      setEvents((es) => [...es.slice(-100), msg]);
      if (typeof msg.payload?.agent === "string" && typeof msg.payload?.to_state === "string") {
        setRun((prev) => {
          if (!prev) return prev;
          return { ...prev, states: { ...prev.states, [String(msg.payload.agent)]: String(msg.payload.to_state) } };
        });
      }
      if (msg.payload?.event === "hitl.review_required") {
        const summary = parseEvaluationSummary(msg.payload?.evaluation_summary);
        if (summary) {
          setLiveEvaluationSummaries((prev) => ({
            ...prev,
            [summary.artifact_ref]: summary,
          }));
        }
        void pendingReviews(runId);
      }
      if (msg.payload?.event === "evaluation.artifact_evaluated") {
        const summary = parseEvaluationSummary(msg.payload);
        if (summary) {
          setLiveEvaluationSummaries((prev) => ({
            ...prev,
            [summary.artifact_ref]: summary,
          }));
        }
      }
      if (msg.payload?.event === "evaluation.scorecard_written") {
        refreshScorecard();
      }
      if (msg.payload?.event === "evaluation.post_training_export_written") {
        refreshPostTrainingExport();
      }
      if (
        msg.payload?.event === "feedback_loop.review_required" ||
        msg.payload?.event === "feedback_loop.appended"
      ) {
        refreshCommander();
      }
      if (
        msg.payload?.event === "tool.started" ||
        msg.payload?.event === "tool.completed"
      ) {
        refreshTools();
      }
    });

    const poll = setInterval(() => {
      void getRun(runId).then((r) => {
        if (alive) setRun(r);
      });
      void getTrace(runId)
        .then((tr) => {
          if (alive) setTrace(tr);
        })
        .catch(() => undefined);
      refreshCommander();
      refreshTools();
      refreshScorecard();
      refreshPostTrainingExport();
    }, 2500);

    return () => {
      alive = false;
      closeWS();
      clearInterval(poll);
    };
  }, [runId, toolFilter, toolStatusFilter, toolEventFilter, toolCallIdFilter, toolLimit]);

  useEffect(() => {
    const stem = STAGE_TO_STEM[activeAgent];
    if (!stem) return;
    void loadLatest(stem.agentDir, stem.stem);
  }, [activeAgent, run?.states[activeAgent], runId]);

  useEffect(() => {
    if (!run) return;
    if (activeAgent === "commander") return;
    if ((run.states[activeAgent] ?? "pending") !== "skipped") return;
    const next = PIPELINE_STAGES.find((stage) => {
      const state = run.states[stage];
      return state !== undefined && state !== "skipped";
    });
    if (next && next !== activeAgent) {
      setActiveAgent(next);
    }
  }, [activeAgent, run]);

  const artifactAgentDir = artifact?.agent_dir ?? "";
  const artifactStem = artifact?.stem ?? "";
  const artifactVersion = artifact?.version ?? "";

  useEffect(() => {
    if (artifactAgentDir !== "coding" || !artifactVersion) {
      setPatch(null);
      return;
    }
    let alive = true;
    void getPatch(runId, artifactVersion)
      .then((next) => {
        if (alive) setPatch(next);
      })
      .catch(() => {
        if (alive) setPatch(null);
      });
    return () => {
      alive = false;
    };
  }, [artifactAgentDir, artifactVersion, runId]);

  useEffect(() => {
    if (!artifactAgentDir || !artifactStem || !artifactVersion) {
      setArtifactEvaluations([]);
      return;
    }
    let alive = true;
    void Promise.all([
      listArtifactEvaluations(
        runId,
        artifactAgentDir,
        artifactStem,
        artifactVersion,
      ).catch(() => []),
      getArtifactEvaluationSummary(
        runId,
        artifactAgentDir,
        artifactStem,
        artifactVersion,
      ).catch(() => null),
    ])
      .then(([reports, summary]) => {
        if (!alive) return;
        setArtifactEvaluations(reports);
        if (summary) {
          setLiveEvaluationSummaries((prev) => ({
            ...prev,
            [summary.artifact_ref]: summary,
          }));
        }
      })
      .catch(() => {
        if (alive) setArtifactEvaluations([]);
      });
    return () => {
      alive = false;
    };
  }, [artifactAgentDir, artifactStem, artifactVersion, runId]);

  // Poll the debate transcript while the active agent is running so the
  // streaming-into-disk transcript shows up live in the UI.
  useEffect(() => {
    const stem = STAGE_TO_STEM[activeAgent];
    if (!stem) return;
    let alive = true;
    const fetchDebate = (): void => {
      void getDebateTranscript(runId, stem.agentDir)
        .then((d) => {
          if (!alive) return;
          setDebate(d);
          // auto-open whenever transcript exists during running/waiting_review
          if (d.exists && (run?.states[activeAgent] === "running")) {
            setDebateOpen(true);
          }
        })
        .catch(() => alive && setDebate(null));
    };
    fetchDebate();
    // Aggressive polling while running (1.5s); slower otherwise (5s).
    const interval = run?.states[activeAgent] === "running" ? 1500 : 5000;
    const iv = setInterval(fetchDebate, interval);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [activeAgent, run?.states[activeAgent], runId]);

  async function loadLatest(agentDir: string, stem: string): Promise<void> {
    try {
      const versions = await fetch(
        `${process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000"}/api/artifacts/${runId}/${agentDir}/${stem}/versions`,
      ).then((r) => r.json());
      if (!Array.isArray(versions) || versions.length === 0) {
        setArtifact(null);
        return;
      }
      const last = versions[versions.length - 1];
      const view = await getArtifact(runId, agentDir, stem, last.version);
      setArtifact(view);
      setEditing(splitFrontmatter(view.text).body);
    } catch {
      setArtifact(null);
    }
  }

  async function approve(): Promise<void> {
    if (!artifact) return;
    const next =
      artifact.agent_dir === "coding" && patch
        ? await approvePatch(runId, artifact.version)
        : await approveArtifact(runId, artifact.agent_dir, artifact.stem, artifact.version);
    setArtifact(next);
  }

  async function reject(): Promise<void> {
    if (!artifact) return;
    if (artifact.agent_dir === "coding" && patch) {
      await rejectPatch(runId, artifact.version, "rejected from UI");
      return;
    }
    await rejectArtifact(runId, artifact.agent_dir, artifact.stem, "rejected from UI");
  }

  async function save(): Promise<void> {
    if (!artifact || editing === null) return;
    // Accept both body-only editing (default UI) and full markdown documents.
    const m = /^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$/.exec(editing);
    const body = m ? m[2] : editing;
    const next = await editArtifact(runId, artifact.agent_dir, artifact.stem, artifact.version, {
      body,
      // metadata_patch left empty: schema metadata is preserved from the base version.
    });
    setArtifact(next);
    setEditing(splitFrontmatter(next.text).body);
  }

  async function startLatestFeedbackLoop(): Promise<void> {
    const latest = diagnoses.length > 0 ? diagnoses[diagnoses.length - 1] : null;
    if (!latest) return;
    const result = await startFeedbackLoop(runId, latest.version);
    setFeedbackAction(result);
    const [nextRun, nextDiagnoses, nextPackets] = await Promise.all([
      getRun(runId).catch(() => null),
      listDiagnoses(runId).catch(() => diagnoses),
      listFeedbackPackets(runId).catch(() => feedbackPackets),
    ]);
    if (nextRun) setRun(nextRun);
    setDiagnoses(nextDiagnoses);
    setFeedbackPackets(nextPackets);
    const nextObservability = await getCommanderObservability(runId).catch(() => null);
    setCommanderObservability(nextObservability);
    const nextRunObservability = await getRunObservability(runId).catch(() => null);
    setRunObservability(nextRunObservability);
  }

  async function exportPostTrainingData(): Promise<void> {
    try {
      setPostTrainingExportMessage("exporting");
      const manifest = await createPostTrainingExport(runId);
      setPostTrainingExport(manifest);
      setPostTrainingExportMessage(`exported ${manifest.eligible_count}/${manifest.record_count}`);
    } catch (error) {
      setPostTrainingExportMessage(error instanceof Error ? error.message : "export failed");
    }
  }

  async function rollbackTool(callId: string): Promise<void> {
    try {
      await rollbackToolCall(runId, callId);
      setToolActionMessage(`rolled back ${callId.slice(0, 8)}`);
      const nextCalls = await listRunToolCalls(runId, {
        tool: toolFilter || undefined,
        status: toolStatusFilter || undefined,
        event: toolEventFilter || undefined,
        callId: toolCallIdFilter || undefined,
        limit: toolLimit,
      }).catch(() => toolCalls);
      setToolCalls(nextCalls);
    } catch (error) {
      setToolActionMessage(error instanceof Error ? error.message : "rollback failed");
    }
  }

  async function approveTool(approvalId: string): Promise<void> {
    try {
      await approveToolCall(runId, approvalId);
      setToolActionMessage(`approved ${approvalId.slice(0, 8)}`);
      const [nextCalls, nextApprovals] = await Promise.all([
        listRunToolCalls(runId, {
          tool: toolFilter || undefined,
          status: toolStatusFilter || undefined,
          event: toolEventFilter || undefined,
          callId: toolCallIdFilter || undefined,
          limit: toolLimit,
        }).catch(() => toolCalls),
        listToolApprovals(runId).catch(() => toolApprovals),
      ]);
      setToolCalls(nextCalls);
      setToolApprovals(nextApprovals);
    } catch (error) {
      setToolActionMessage(error instanceof Error ? error.message : "approval failed");
    }
  }

  async function rejectTool(approvalId: string): Promise<void> {
    try {
      await rejectToolCall(runId, approvalId);
      setToolActionMessage(`rejected ${approvalId.slice(0, 8)}`);
      const nextApprovals = await listToolApprovals(runId).catch(() => toolApprovals);
      setToolApprovals(nextApprovals);
    } catch (error) {
      setToolActionMessage(error instanceof Error ? error.message : "reject failed");
    }
  }

  async function approveCandidate(candidateId: string): Promise<void> {
    await approveMemoryCandidate(runId, candidateId);
    await refreshMemoryGovernance();
  }

  async function rejectCandidate(candidateId: string): Promise<void> {
    await rejectMemoryCandidate(runId, candidateId);
    await refreshMemoryGovernance();
  }

  async function markCandidateStale(candidateId: string): Promise<void> {
    await markMemoryCandidateStale(runId, candidateId);
    await refreshMemoryGovernance();
  }

  async function supersedeCandidate(candidateId: string): Promise<void> {
    await supersedeMemoryCandidate(runId, candidateId);
    await refreshMemoryGovernance();
  }

  async function createMutationProposal(
    lever: SelfEvolutionLeverItem,
    proposedContent: string,
    rationale: string,
  ): Promise<void> {
    try {
      await createSelfEvolutionMutation(runId, {
        lever_id: lever.id,
        agent: lever.agent,
        path: lever.title,
        proposed_content: proposedContent,
        rationale,
      });
      setToolActionMessage(`created mutation for ${lever.agent}/${lever.title}`);
      await refreshMemoryGovernance();
    } catch (error) {
      setToolActionMessage(error instanceof Error ? error.message : "mutation proposal failed");
      throw error;
    }
  }

  async function approveMutation(mutationId: string): Promise<void> {
    try {
      await approveSelfEvolutionMutation(runId, mutationId);
      setToolActionMessage(`applied mutation ${mutationId.slice(0, 8)}`);
      await refreshMemoryGovernance();
    } catch (error) {
      setToolActionMessage(error instanceof Error ? error.message : "mutation approval failed");
    }
  }

  async function rejectMutation(mutationId: string): Promise<void> {
    try {
      await rejectSelfEvolutionMutation(runId, mutationId);
      setToolActionMessage(`rejected mutation ${mutationId.slice(0, 8)}`);
      await refreshMemoryGovernance();
    } catch (error) {
      setToolActionMessage(error instanceof Error ? error.message : "mutation rejection failed");
    }
  }

  async function refreshMemoryGovernance(): Promise<void> {
    const [next, nextLevers, nextMutations, nextObservability, nextRunObservability] = await Promise.all([
      listMemoryCandidates(runId).catch(() => memoryCandidates),
      getSelfEvolutionLevers(runId).catch(() => selfEvolutionLevers),
      listSelfEvolutionMutations(runId).catch(() => selfEvolutionMutations),
      getCommanderObservability(runId).catch(() => null),
      getRunObservability(runId).catch(() => null),
    ]);
    setMemoryCandidates(next);
    setSelfEvolutionLevers(nextLevers);
    setSelfEvolutionMutations(nextMutations);
    setCommanderObservability(nextObservability);
    setRunObservability(nextRunObservability);
  }

  function selectAgent(agent: string): void {
    setActiveAgent(agent);
    setViewMode("artifact");
  }

  const isContextView = viewMode === "context";
  const isWorkspaceView = viewMode === "workspace";
  const activeArtifactEvaluation = artifact
    ? liveEvaluationSummaries[relativeArtifactRef(artifact)]
      ?? buildSummaryFromReports(artifact, artifactEvaluations)
    : null;

  return (
    <main className="grid h-screen grid-cols-[260px,1fr] gap-0">
      <aside className="border-r border-mars-border bg-mars-panel/60 p-4">
        <div className="flex items-center justify-between">
          <Link href="/" className="text-xs text-slate-500 hover:text-slate-300">
            &larr; 实验台
          </Link>
          <Link
            href={`/runs/${runId}/multi`}
            className="text-xs text-mars-accent hover:underline"
          >
            多实验视图 &rarr;
          </Link>
        </div>
        <h2 className="mt-3 text-sm font-semibold uppercase tracking-wider text-slate-400">
          {run?.task ?? "loading…"}
        </h2>
        <p className="mt-1 text-xs text-slate-500">{runId}</p>

        {/* Commander gets the C位: a prominent hub card above the 5-stage list. */}
        <button
          onClick={() => selectAgent("commander")}
          className={`mt-6 flex w-full items-center justify-between rounded-lg border px-3 py-2.5 text-left transition ${
            activeAgent === "commander"
              ? "border-cyan-400/70 bg-cyan-500/15 shadow-[0_0_18px_-4px_rgba(34,211,238,0.6)]"
              : "border-cyan-500/30 bg-cyan-500/5 hover:border-cyan-400/60 hover:bg-cyan-500/10"
          }`}
        >
          <span className="flex items-center gap-2">
            <span className="text-base">🛰️</span>
            <span className="flex flex-col">
              <span className="text-sm font-semibold text-cyan-50">{agentLabel("commander")}</span>
              <span className="text-[10px] text-cyan-200/70">主控调度 · 诊断 · 反馈回路</span>
            </span>
          </span>
          <span className="rounded bg-cyan-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-cyan-200">
            主控
          </span>
        </button>

        <div className="mb-1 mt-4 flex items-center gap-2 px-1">
          <span className="h-px flex-1 bg-mars-border" />
          <span className="text-[10px] uppercase tracking-wider text-slate-500">流水线 · 5 Agent</span>
          <span className="h-px flex-1 bg-mars-border" />
        </div>

        <ul className="space-y-1">
          {PIPELINE_STAGES.map((s) => {
            const state = run?.states[s] ?? "pending";
            const isActive = activeAgent === s;
            const stageEvaluation = stageEvaluationBadge(s, scorecard, liveEvaluationSummaries);
            return (
              <li key={s}>
                <button
                  onClick={() => selectAgent(s)}
                  className={`flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm ${
                    isActive ? "bg-mars-accent/20 text-white" : "hover:bg-mars-panel"
                  }`}
                >
                  <span className="capitalize">{agentLabel(s)}</span>
                  <span className="flex items-center gap-1.5">
                    {stageEvaluation ? <EvaluationMiniBadge summary={stageEvaluation} /> : null}
                    <StateBadge state={state} />
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        <h3 className="mt-8 text-xs font-semibold tracking-wider text-slate-500">事件流</h3>
        <ol className="mt-2 max-h-72 space-y-1 overflow-auto text-xs text-slate-400">
          {events.slice(-20).map((e, i) => (
            <li key={i} className="rounded border border-mars-border bg-mars-bg px-2 py-1">
              <span className="text-slate-500">{e.channel.slice(0, 40)}</span>{" "}
              <span className="font-mono text-slate-300">{JSON.stringify(e.payload).slice(0, 120)}</span>
            </li>
          ))}
        </ol>
      </aside>

      <section className="flex flex-col">
        {(() => {
          const isWaiting =
            run?.states[activeAgent] === "waiting_review" && !isContextView && !isWorkspaceView;
          return (
            <>
              {isWaiting ? (
                <div className="border-b border-fuchsia-500/40 bg-fuchsia-500/15 px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-fuchsia-100">
                        {t("hitl.banner.title")}
                      </p>
                      <p className="mt-0.5 max-w-3xl text-[11px] text-fuchsia-200/80">
                        {t("hitl.banner.body")}
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <button
                        onClick={reject}
                        disabled={!artifact}
                        className="rounded border border-red-500/50 bg-red-500/15 px-3 py-1.5 text-sm font-medium text-red-100 hover:bg-red-500/25 disabled:opacity-50"
                      >
                        ✗ {t("run.reject")}
                      </button>
                      <button
                        onClick={approve}
                        disabled={!artifact}
                        className="rounded bg-emerald-500/80 px-4 py-1.5 text-sm font-bold text-white shadow hover:bg-emerald-500 disabled:opacity-50"
                      >
                        ✓ {t("run.approve")}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}
              <header className="flex flex-wrap items-start justify-between gap-3 border-b border-mars-border p-4">
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-3">
                    <h1 className="min-w-0 break-words text-xl font-semibold leading-tight">
                      {agentLabel(activeAgent)}
                    </h1>
                    {STAGE_TO_STEM[activeAgent] ? (
                      <div className="flex shrink-0 rounded border border-mars-border bg-mars-bg p-0.5 text-xs">
                        {activeAgent === "coding" ? (
                          <button
                            onClick={() => setViewMode("workspace")}
                            className={`rounded px-2.5 py-1 ${
                              viewMode === "workspace" ? "bg-mars-accent text-white" : "text-slate-400 hover:text-slate-200"
                            }`}
                          >
                            工作台
                          </button>
                        ) : null}
                        <button
                          onClick={() => setViewMode("artifact")}
                          className={`rounded px-2.5 py-1 ${
                            viewMode === "artifact" ? "bg-mars-accent text-white" : "text-slate-400 hover:text-slate-200"
                          }`}
                        >
                          产物
                        </button>
                        <button
                          onClick={() => setViewMode("context")}
                          className={`rounded px-2.5 py-1 ${
                            viewMode === "context" ? "bg-mars-accent text-white" : "text-slate-400 hover:text-slate-200"
                          }`}
                        >
                          上下文配置
                        </button>
                      </div>
                    ) : null}
                  </div>
                  <p className="mt-1 max-w-full truncate text-xs text-slate-500">
                    {isWorkspaceView
                      ? "编码工作台"
                      : isContextView
                        ? "Agent 级上下文、上传材料和研究来源"
                        : artifact
                          ? `${artifact.path}`
                          : "尚无产物"}
                  </p>
                </div>
                <div className={`shrink-0 gap-2 ${isContextView || isWorkspaceView ? "hidden" : "flex"}`}>
                  <button
                    onClick={save}
                    disabled={!artifact}
                    className="rounded border border-mars-border px-3 py-1.5 text-sm hover:bg-mars-panel disabled:opacity-50"
                  >
                    {t("run.editor.save")}
                  </button>
                  {!isWaiting ? (
                    <>
                      <button
                        onClick={reject}
                        disabled={!artifact}
                        className="rounded border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-sm text-red-200 hover:bg-red-500/20 disabled:opacity-50"
                      >
                        {t("run.reject")}
                      </button>
                      <button
                        onClick={approve}
                        disabled={!artifact}
                        className="rounded bg-mars-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                      >
                        {t("run.approve")}
                      </button>
                    </>
                  ) : null}
                </div>
              </header>
            </>
          );
        })()}

        <div className="flex flex-1 min-h-0 flex-col gap-3 overflow-auto p-4">
          {scorecard ? (
            <RunEvaluationScorecard
              scorecard={scorecard}
              postTrainingExport={postTrainingExport}
              exportMessage={postTrainingExportMessage}
              onExportPostTraining={exportPostTrainingData}
            />
          ) : null}
          {activeAgent === "commander" ? (
            <>
              <CommanderFeedbackPanel
                diagnoses={diagnoses}
                feedbackPackets={feedbackPackets}
                memoryCandidates={memoryCandidates}
                episodeMemory={episodeMemory}
                selfEvolutionLevers={selfEvolutionLevers}
                selfEvolutionMutations={selfEvolutionMutations}
                observability={commanderObservability}
                evalResult={commanderEval}
                actionResult={feedbackAction}
                toolCalls={toolCalls}
                toolApprovals={toolApprovals}
                toolAdapters={toolAdapters}
                toolActionMessage={toolActionMessage}
                toolFilter={toolFilter}
                toolStatusFilter={toolStatusFilter}
                toolEventFilter={toolEventFilter}
                toolCallIdFilter={toolCallIdFilter}
                toolLimit={toolLimit}
                onStartFeedback={startLatestFeedbackLoop}
                onRollbackTool={rollbackTool}
                onApproveTool={approveTool}
                onRejectTool={rejectTool}
                onToolFilterChange={setToolFilter}
                onToolStatusFilterChange={setToolStatusFilter}
                onToolEventFilterChange={setToolEventFilter}
                onToolCallIdFilterChange={setToolCallIdFilter}
                onToolLimitChange={setToolLimit}
                onApproveCandidate={approveCandidate}
                onRejectCandidate={rejectCandidate}
                onMarkCandidateStale={markCandidateStale}
                onSupersedeCandidate={supersedeCandidate}
                onCreateMutation={createMutationProposal}
                onApproveMutation={approveMutation}
                onRejectMutation={rejectMutation}
              />
              <RunTimelinePanel observability={runObservability} />
            </>
          ) : null}
          {isWorkspaceView && activeAgent === "coding" ? (
            <CodingWorkspacePanel runId={runId} project={run?.project ?? "moe-pimc"} />
          ) : isContextView ? (
            <AgentContextPanel agent={activeAgent} />
          ) : (
            <>
              {activeAgent === "execution" ? (
                <ExecutionLivePanel runId={runId} />
              ) : null}
              {artifact ? (
                <>
                  <ValidationBadge view={artifact} />
                  <ArtifactEvaluationPanel
                    summary={activeArtifactEvaluation}
                    reports={artifactEvaluations}
                  />
                  {patch ? <PatchPanel patch={patch} /> : null}
                  <DebatePanel
                    debate={debate}
                    open={debateOpen}
                    onToggle={() => setDebateOpen((o) => !o)}
                    modeFromMeta={String(artifact.metadata?.debate_mode ?? "")}
                  />
                  <ArtifactBodyEditor
                    text={editing ?? splitFrontmatter(artifact.text).body}
                    onChange={setEditing}
                    frontmatter={splitFrontmatter(artifact.text).frontmatter}
                  />
                </>
              ) : (
                <RunningOrEmpty
                  agentState={run?.states[activeAgent] ?? "pending"}
                  debate={debate}
                  open={debateOpen}
                  onToggle={() => setDebateOpen((o) => !o)}
                />
              )}
            </>
          )}
          <AgentReActTracePanel
            trace={trace}
            toolCalls={toolCalls}
            activeAgent={activeAgent}
            agentState={run?.states[activeAgent] ?? "pending"}
            events={events}
          />
        </div>
      </section>
    </main>
  );
}

function RunTimelinePanel({
  observability,
}: {
  observability: RunObservabilityView | null;
}): JSX.Element | null {
  if (!observability) return null;
  const health = asRecord(observability.health);
  const timeline = observability.timeline.slice(0, 8);
  const execution = asRecord(observability.execution);
  const audit = asRecord(observability.audit);
  return (
    <section className="rounded border border-mars-border bg-mars-bg/50 p-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">运行时间线</h3>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500">
            状态={observability.status} 原因={metaText(health, "reason", "未知")}
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-right">
          <MetricPill label="事件" value={observability.timeline.length} />
          <MetricPill label="跨度" value={Number(asRecord(observability.trace).span_count ?? 0)} />
          <MetricPill label="失败" value={Number(execution.failure_count ?? 0)} />
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1.2fr,0.8fr]">
        <div className="space-y-1.5">
          {timeline.length > 0 ? (
            timeline.map((event) => {
              const payload = asRecord(event["payload"]);
              const source = asRecord(event["source"]);
              return (
                <div key={metaText(event, "event_id", shortJson(event))} className="rounded border border-mars-border bg-mars-panel/40 px-2 py-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[10px] text-cyan-200">
                      {metaText(event, "kind", "event")}
                    </span>
                    <span className="text-[10px] text-slate-500">
                      {metaText(event, "timestamp").slice(0, 19)}
                    </span>
                  </div>
                  <p className="mt-1 truncate text-[11px] text-slate-300">
                    {metaText(source, "component", metaText(event, "channel"))}
                    {metaText(payload, "agent") ? ` · ${metaText(payload, "agent")}` : ""}
                    {metaText(payload, "to_state") ? ` -> ${metaText(payload, "to_state")}` : ""}
                    {metaText(payload, "tool") ? ` · ${metaText(payload, "tool")}` : ""}
                  </p>
                </div>
              );
            })
          ) : (
            <p className="text-xs text-slate-500">暂无时间线事件。</p>
          )}
        </div>

        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
          <SummaryBox title="执行" rows={[
            ["指标", String(execution.metric_rows ?? 0)],
            ["曲线", String(execution.curve_count ?? 0)],
            ["图表", String(execution.plot_count ?? 0)],
          ]} />
          <SummaryBox title="审计" rows={[
            ["HITL", String(audit.hitl_decisions ?? 0)],
            ["诊断", String(audit.diagnosis_count ?? 0)],
            ["记忆审核", String(audit.memory_reviews ?? 0)],
          ]} />
        </div>
      </div>
    </section>
  );
}

function MetricPill({ label, value }: { label: string; value: number }): JSX.Element {
  return (
    <div className="rounded border border-mars-border bg-mars-panel/60 px-2 py-1">
      <div className="font-mono text-xs text-slate-100">{value}</div>
      <div className="text-[9px] uppercase text-slate-500">{label}</div>
    </div>
  );
}

function SummaryBox({
  title,
  rows,
}: {
  title: string;
  rows: [string, string][];
}): JSX.Element {
  return (
    <div className="rounded border border-mars-border bg-mars-panel/40 p-2">
      <div className="mb-1 text-xs font-semibold uppercase text-slate-500">{title}</div>
      <dl className="space-y-1 text-[11px]">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-2">
            <dt className="text-slate-500">{label}</dt>
            <dd className="font-mono text-slate-200">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function CommanderFeedbackPanel({
  diagnoses,
  feedbackPackets,
  memoryCandidates,
  episodeMemory,
  selfEvolutionLevers,
  selfEvolutionMutations,
  observability,
  evalResult,
  actionResult,
  toolCalls,
  toolApprovals,
  toolAdapters,
  toolActionMessage,
  toolFilter,
  toolStatusFilter,
  toolEventFilter,
  toolCallIdFilter,
  toolLimit,
  onStartFeedback,
  onRollbackTool,
  onApproveTool,
  onRejectTool,
  onToolFilterChange,
  onToolStatusFilterChange,
  onToolEventFilterChange,
  onToolCallIdFilterChange,
  onToolLimitChange,
  onApproveCandidate,
  onRejectCandidate,
  onMarkCandidateStale,
  onSupersedeCandidate,
  onCreateMutation,
  onApproveMutation,
  onRejectMutation,
}: {
  diagnoses: DiagnosisView[];
  feedbackPackets: FeedbackPacketView[];
  memoryCandidates: RunMemoryEventView | null;
  episodeMemory: RunMemoryEventView | null;
  selfEvolutionLevers: SelfEvolutionLeversView | null;
  selfEvolutionMutations: RunMemoryEventView | null;
  observability: CommanderObservabilityView | null;
  evalResult: CommanderAttributionEvalView | null;
  actionResult: FeedbackLoopStartResult | null;
  toolCalls: ToolAuditEntry[];
  toolApprovals: ToolApprovalRecord[];
  toolAdapters: McpAdapterStatus[];
  toolActionMessage: string;
  toolFilter: string;
  toolStatusFilter: string;
  toolEventFilter: string;
  toolCallIdFilter: string;
  toolLimit: number;
  onStartFeedback: () => Promise<void>;
  onRollbackTool: (callId: string) => Promise<void>;
  onApproveTool: (approvalId: string) => Promise<void>;
  onRejectTool: (approvalId: string) => Promise<void>;
  onToolFilterChange: (value: string) => void;
  onToolStatusFilterChange: (value: string) => void;
  onToolEventFilterChange: (value: string) => void;
  onToolCallIdFilterChange: (value: string) => void;
  onToolLimitChange: (value: number) => void;
  onApproveCandidate: (candidateId: string) => Promise<void>;
  onRejectCandidate: (candidateId: string) => Promise<void>;
  onMarkCandidateStale: (candidateId: string) => Promise<void>;
  onSupersedeCandidate: (candidateId: string) => Promise<void>;
  onCreateMutation: (
    lever: SelfEvolutionLeverItem,
    proposedContent: string,
    rationale: string,
  ) => Promise<void>;
  onApproveMutation: (mutationId: string) => Promise<void>;
  onRejectMutation: (mutationId: string) => Promise<void>;
}): JSX.Element {
  const [mutationDrafts, setMutationDrafts] = useState<
    Record<string, { proposedContent: string; rationale: string }>
  >({});
  // Default view stays minimal (status + 启动反馈回路); all the dense
  // observability/governance detail lives behind this disclosure.
  const [showAdvanced, setShowAdvanced] = useState(false);
  const latest = diagnoses.length > 0 ? diagnoses[diagnoses.length - 1] : null;
  const latestMeta = latest?.metadata ?? {};
  const latestPassed = latestMeta.passed === true;
  const feedbackRef = metaText(latestMeta, "feedback_packet_ref");
  const target = metaText(latestMeta, "recommended_target", "none");
  const confidence = metaText(latestMeta, "confidence", "0");
  const canStart = Boolean(latest && !latestPassed && feedbackRef);
  const candidates = memoryCandidates?.items ?? [];
  const episodes = episodeMemory?.items.slice(-3).reverse() ?? [];
  const mutations = selfEvolutionMutations?.items ?? [];
  const leverItems = selfEvolutionLevers
    ? (["prompt", "few_shot", "eval", "kb_finding"] as const).flatMap(
        (key) => selfEvolutionLevers.levers[key] ?? [],
      )
    : [];
  const attempts = observability?.attempts ?? [];
  const evalCases = evalResult?.cases ?? [];

  const mutationDraftFor = (
    lever: SelfEvolutionLeverItem,
  ): { proposedContent: string; rationale: string } =>
    mutationDrafts[lever.id] ?? { proposedContent: "", rationale: "" };

  const updateMutationDraft = (
    leverId: string,
    patch: Partial<{ proposedContent: string; rationale: string }>,
  ): void => {
    setMutationDrafts((current) => ({
      ...current,
      [leverId]: {
        proposedContent: current[leverId]?.proposedContent ?? "",
        rationale: current[leverId]?.rationale ?? "",
        ...patch,
      },
    }));
  };

  const clearMutationDraft = (leverId: string): void => {
    setMutationDrafts((current) => {
      const next = { ...current };
      delete next[leverId];
      return next;
    });
  };

  return (
    <section className="rounded border border-cyan-500/30 bg-cyan-500/5 p-3">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-cyan-100">主控反馈回路</h3>
            <span
              className={`rounded px-2 py-0.5 text-[10px] uppercase ${
                !latest
                  ? "bg-slate-500/20 text-slate-300"
                  : latestPassed
                    ? "bg-emerald-500/20 text-emerald-200"
                    : "bg-amber-500/20 text-amber-200"
              }`}
            >
              {!latest ? "待运行" : latestPassed ? "已通过" : "需要回路"}
            </span>
          </div>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500">
            诊断={latest?.version ?? "无"} · 目标={target} · 置信度={confidence}
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-slate-300">
            {latest
              ? metaText(latestMeta, "recommended_action", "暂无诊断结论。")
              : "尚未触发回路。执行批次未达标时,主控会自动诊断、定位责任 Agent 并给出修复建议;点击「启动反馈回路」即可回溯到该 Agent 重跑。"}
          </p>
          {feedbackRef ? (
            <p className="mt-1.5 break-all font-mono text-[10px] text-cyan-200">反馈包: {feedbackRef}</p>
          ) : null}
          {actionResult ? (
            <p className="mt-1 font-mono text-[10px] text-slate-400">
              状态={actionResult.status} · 目标={actionResult.target ?? "-"} · 轮次={actionResult.attempt ?? "-"}
            </p>
          ) : null}
        </div>
        <button
          onClick={() => void onStartFeedback()}
          disabled={!canStart}
          className="shrink-0 rounded bg-cyan-500/80 px-3 py-1.5 text-xs font-semibold text-white hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          启动反馈回路
        </button>
      </div>

      <button
        onClick={() => setShowAdvanced((v) => !v)}
        className="flex w-full items-center justify-between rounded border border-mars-border bg-mars-bg/40 px-3 py-2 text-xs text-slate-400 transition hover:text-slate-200"
      >
        <span>高级与审计 · 归因详情 / 反馈包 / 记忆候选 / 自进化杠杆 / 回路审计 / 工具调用</span>
        <span className="font-mono">{showAdvanced ? "收起 ▴" : "展开 ▾"}</span>
      </button>

      {showAdvanced ? (
      <div className="mt-3 space-y-3">
      <div className="grid gap-3 lg:grid-cols-3">
        <div className="rounded border border-mars-border bg-mars-bg/50 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-400">归因判断</span>
            <span className={`rounded px-2 py-0.5 text-[10px] uppercase ${
              latestPassed ? "bg-emerald-500/20 text-emerald-200" : "bg-amber-500/20 text-amber-200"
            }`}>
              {latestPassed ? "已通过" : "需要回路"}
            </span>
          </div>
          <p className="text-xs leading-relaxed text-slate-300">
            {metaText(latestMeta, "recommended_action", "暂无诊断。")}
          </p>
          {feedbackRef ? (
            <p className="mt-2 break-all font-mono text-[10px] text-cyan-200">{feedbackRef}</p>
          ) : null}
          {actionResult ? (
            <p className="mt-2 font-mono text-[10px] text-slate-400">
              状态={actionResult.status} 目标={actionResult.target ?? "-"} 轮次={actionResult.attempt ?? "-"}
            </p>
          ) : null}
        </div>

        <div className="rounded border border-mars-border bg-mars-bg/50 p-3">
          <div className="mb-2 text-xs font-semibold uppercase text-slate-400">
            反馈包
          </div>
          {feedbackPackets.length > 0 ? (
            <div className="space-y-2">
              {feedbackPackets.slice(-3).reverse().map((packet) => (
                <div key={packet.path} className="rounded border border-mars-border bg-mars-panel/40 p-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[10px] text-cyan-200">第 {packet.attempt} 轮</span>
                    <span className="text-[10px] text-slate-500">{metaText(packet.metadata, "target_agent")}</span>
                  </div>
                  <ul className="mt-1 space-y-1 text-[11px] text-slate-300">
                    {metaListText(packet.metadata["do_next"]).slice(0, 2).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-500">暂无反馈包。</p>
          )}
        </div>

        <div className="rounded border border-mars-border bg-mars-bg/50 p-3">
          <div className="mb-2 text-xs font-semibold uppercase text-slate-400">
            记忆候选
          </div>
          {candidates.length > 0 ? (
            <div className="space-y-2">
              {candidates.slice(-3).reverse().map((candidate) => {
                const id = metaText(candidate, "id");
                const status = metaText(candidate, "status", "pending_review");
                return (
                  <div key={id || shortJson(candidate)} className="rounded border border-mars-border bg-mars-panel/40 p-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[11px] font-medium text-slate-200">
                        {metaText(candidate, "agent", "agent")}
                      </span>
                      <span className="rounded bg-mars-bg px-1.5 py-0.5 text-[10px] text-slate-400">
                        {statusLabel(status)}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-3 text-[11px] leading-relaxed text-slate-300">
                      {metaText(candidate, "text", shortJson(candidate))}
                    </p>
                    {status === "pending_review" && id ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <button
                          onClick={() => void onApproveCandidate(id)}
                          className="rounded border border-emerald-500/50 px-2 py-1 text-[10px] font-semibold text-emerald-200 hover:bg-emerald-500/10"
                        >
                          批准
                        </button>
                        <button
                          onClick={() => void onRejectCandidate(id)}
                          className="rounded border border-red-500/50 px-2 py-1 text-[10px] font-semibold text-red-200 hover:bg-red-500/10"
                        >
                          驳回
                        </button>
                      </div>
                    ) : null}
                    {(status === "approved" || status === "active") && id ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <button
                          onClick={() => void onMarkCandidateStale(id)}
                          className="rounded border border-amber-500/50 px-2 py-1 text-[10px] font-semibold text-amber-200 hover:bg-amber-500/10"
                        >
                          标记过期
                        </button>
                        <button
                          onClick={() => void onSupersedeCandidate(id)}
                          className="rounded border border-slate-500/60 px-2 py-1 text-[10px] font-semibold text-slate-200 hover:bg-slate-500/10"
                        >
                          标记替代
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-slate-500">暂无记忆候选。</p>
          )}
        </div>
      </div>

      {selfEvolutionLevers ? (
        <div className="mt-3 rounded border border-mars-border bg-mars-bg/40 p-2">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="text-xs font-semibold uppercase text-slate-500">
              自进化杠杆
            </div>
            <span className="rounded bg-mars-panel px-2 py-0.5 font-mono text-[10px] text-slate-400">
              {selfEvolutionLevers.mutation_mode}
            </span>
          </div>
          <div className="grid grid-cols-4 gap-1 text-center text-[10px]">
            {(["prompt", "few_shot", "eval", "kb_finding"] as const).map((key) => (
              <div key={key} className="rounded border border-mars-border bg-mars-panel/40 px-1.5 py-1">
                <p className="font-mono text-slate-200">{selfEvolutionLevers.counts[key] ?? 0}</p>
                <p className="mt-0.5 truncate text-slate-500">{key}</p>
              </div>
            ))}
          </div>
          {leverItems.length > 0 ? (
            <div className="mt-2 grid gap-2 md:grid-cols-3">
              {leverItems.slice(0, 6).map((lever) => {
                const draft = mutationDraftFor(lever);
                const canCreateMutation =
                  isMutationLever(lever)
                  && draft.proposedContent.trim().length > 0
                  && draft.rationale.trim().length > 0;
                return (
                  <div key={lever.id} className="rounded bg-mars-panel/50 px-2 py-1.5">
                    <p className="truncate font-mono text-[10px] text-slate-500">
                      {lever.lever_type} · {lever.agent || "run"}
                    </p>
                    <p className="mt-1 line-clamp-2 text-[11px] text-slate-300">
                      {lever.text_preview || lever.title}
                    </p>
                    {isMutationLever(lever) ? (
                      <div className="mt-2 space-y-1.5">
                        <textarea
                          value={draft.proposedContent}
                          onChange={(event) => updateMutationDraft(
                            lever.id,
                            { proposedContent: event.target.value },
                          )}
                          placeholder="建议内容"
                          className="min-h-20 w-full resize-y rounded border border-mars-border bg-mars-bg/70 px-2 py-1 font-mono text-[10px] text-slate-200 outline-none focus:border-cyan-500/60"
                        />
                        <input
                          value={draft.rationale}
                          onChange={(event) => updateMutationDraft(
                            lever.id,
                            { rationale: event.target.value },
                          )}
                          placeholder="理由"
                          className="w-full rounded border border-mars-border bg-mars-bg/70 px-2 py-1 text-[10px] text-slate-200 outline-none focus:border-cyan-500/60"
                        />
                        <button
                          onClick={() => {
                            void onCreateMutation(
                              lever,
                              draft.proposedContent,
                              draft.rationale,
                            )
                              .then(() => clearMutationDraft(lever.id))
                              .catch(() => undefined);
                          }}
                          disabled={!canCreateMutation}
                          className="rounded border border-cyan-500/50 px-2 py-1 text-[10px] font-semibold text-cyan-200 hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          提议
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}
          {mutations.length > 0 ? (
            <div className="mt-3 grid gap-2 md:grid-cols-3">
              {mutations.slice(-6).reverse().map((mutation) => {
                const id = metaText(mutation, "id");
                const status = metaText(mutation, "status", "pending_review");
                const path = metaText(mutation, "path");
                const gate = asRecord(mutation["eval_gate"]);
                const gatePassed = gate["passed"] === true;
                return (
                  <div key={id || shortJson(mutation)} className="rounded border border-mars-border bg-mars-panel/40 p-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-[10px] text-cyan-200">
                        {metaText(mutation, "agent", "agent")}/{path || "context"}
                      </span>
                      <span className="rounded bg-mars-bg px-1.5 py-0.5 text-[10px] text-slate-400">
                        {statusLabel(status)}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-3 text-[11px] leading-relaxed text-slate-300">
                      {metaText(mutation, "text_preview", shortJson(mutation))}
                    </p>
                    <p className="mt-1 font-mono text-[10px] text-slate-500">
                      门禁={gatePassed ? "通过" : "阻塞"} id={id ? id.slice(0, 8) : "-"}
                    </p>
                    {status === "pending_review" && id ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <button
                          onClick={() => void onApproveMutation(id)}
                          disabled={!gatePassed}
                          className="rounded border border-emerald-500/50 px-2 py-1 text-[10px] font-semibold text-emerald-200 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          应用
                        </button>
                        <button
                          onClick={() => void onRejectMutation(id)}
                          className="rounded border border-red-500/50 px-2 py-1 text-[10px] font-semibold text-red-200 hover:bg-red-500/10"
                        >
                          驳回
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}

      {episodes.length > 0 ? (
        <div className="mt-3 rounded border border-mars-border bg-mars-bg/40 p-2">
          <div className="mb-1 text-xs font-semibold text-slate-500">片段记忆</div>
          <div className="grid gap-2 md:grid-cols-3">
            {episodes.map((episode) => (
              <div key={shortJson(episode)} className="rounded bg-mars-panel/50 px-2 py-1.5">
                <p className="font-mono text-[10px] text-slate-500">
                  轮次={metaText(episode, "attempt")} 目标={metaText(episode, "target_agent")}
                </p>
                <p className="mt-1 line-clamp-2 text-[11px] text-slate-300">
                  {metaText(episode, "reason", metaText(episode, "expected_fix", ""))}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-3 grid gap-3 xl:grid-cols-[1.3fr,0.7fr]">
        <div className="rounded border border-mars-border bg-mars-bg/40 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase text-slate-500">
              回路审计
            </span>
            <span className="font-mono text-[10px] text-slate-500">
              轮次={observability?.attempt_count ?? attempts.length}
            </span>
          </div>
          {attempts.length > 0 ? (
            <div className="space-y-2">
              {attempts.slice(-4).reverse().map((attempt) => (
                <CommanderAttemptAudit key={metaText(attempt, "attempt")} attempt={attempt} />
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-500">暂无回路审计。</p>
          )}
        </div>

        <div className="rounded border border-mars-border bg-mars-bg/40 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase text-slate-500">
              归因评价
            </span>
            <span className="font-mono text-[10px] text-slate-500">
              {evalResult ? `${evalResult.passed}/${evalResult.case_count}` : "未运行"}
            </span>
          </div>
          {evalResult ? (
            <>
              <div className="grid grid-cols-3 gap-2">
                <MetricTile label="目标" value={evalResult.target_accuracy} />
                <MetricTile label="继续" value={evalResult.continuation_accuracy} />
                <MetricTile label="人审" value={evalResult.human_pause_accuracy} />
              </div>
              <div className="mt-3 space-y-1.5">
                {evalCases.slice(0, 5).map((item) => (
                  <EvalCaseRow key={metaText(item, "id")} item={item} />
                ))}
              </div>
            </>
          ) : (
            <p className="text-xs text-slate-500">暂无评价结果。</p>
          )}
        </div>
      </div>

      <ToolAuditPanel
        toolCalls={toolCalls}
        toolApprovals={toolApprovals}
        toolAdapters={toolAdapters}
        actionMessage={toolActionMessage}
        toolFilter={toolFilter}
        toolStatusFilter={toolStatusFilter}
        toolEventFilter={toolEventFilter}
        toolCallIdFilter={toolCallIdFilter}
        toolLimit={toolLimit}
        onRollback={onRollbackTool}
        onApprove={onApproveTool}
        onReject={onRejectTool}
        onToolFilterChange={onToolFilterChange}
        onToolStatusFilterChange={onToolStatusFilterChange}
        onToolEventFilterChange={onToolEventFilterChange}
        onToolCallIdFilterChange={onToolCallIdFilterChange}
        onToolLimitChange={onToolLimitChange}
      />
      </div>
      ) : null}
    </section>
  );
}

function statusClass(status: string): string {
  const normalized = status.toLowerCase();
  if (
    normalized === "success" ||
    normalized === "ok" ||
    normalized === "ready" ||
    normalized === "available" ||
    normalized === "tool.completed"
  ) {
    return "rounded bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[10px] text-emerald-200";
  }
  if (normalized === "error" || normalized === "failed" || normalized === "blocked" || normalized === "tool.failed" || normalized === "tool.blocked") {
    return "rounded bg-red-500/10 px-1.5 py-0.5 font-mono text-[10px] text-red-200";
  }
  if (normalized === "disabled" || normalized === "missing" || normalized === "fallback") {
    return "rounded bg-slate-700/60 px-1.5 py-0.5 font-mono text-[10px] text-slate-400";
  }
  return "rounded bg-amber-500/10 px-1.5 py-0.5 font-mono text-[10px] text-amber-200";
}

function statusLabel(status: string): string {
  const normalized = status.toLowerCase();
  const labels: Record<string, string> = {
    success: "成功",
    ok: "正常",
    ready: "就绪",
    available: "可用",
    running: "运行中",
    error: "错误",
    failed: "失败",
    blocked: "阻塞",
    "tool.started": "调用中",
    "tool.completed": "已返回",
    "tool.failed": "失败",
    "tool.blocked": "阻塞",
    "tool.requires_approval": "需审批",
    "tool.rolled_back": "已回滚",
    disabled: "关闭",
    missing: "缺失",
    fallback: "降级",
    pending: "待处理",
    pending_review: "待审核",
    requires_approval: "需要审批",
    approved: "已批准",
    active: "生效",
    stale: "过期",
    superseded: "已替代",
  };
  return labels[normalized] ?? status;
}

function decisionLabel(decision: string): string {
  const normalized = decision.toLowerCase();
  const labels: Record<string, string> = {
    pass: "通过",
    warn: "警告",
    revise: "需修改",
    block: "阻塞",
    fail: "失败",
  };
  return labels[normalized] ?? decision;
}

function severityLabel(severity?: string): string {
  const normalized = String(severity ?? "info").toLowerCase();
  const labels: Record<string, string> = {
    info: "信息",
    low: "低",
    medium: "中",
    high: "高",
    critical: "严重",
  };
  return labels[normalized] ?? normalized;
}

function priorityLabel(priority: string): string {
  const normalized = priority.toLowerCase();
  const labels: Record<string, string> = {
    normal: "普通",
    elevated: "提升",
    high: "高",
    critical: "严重",
  };
  return labels[normalized] ?? priority;
}

function ToolAuditPanel({
  toolCalls,
  toolApprovals,
  toolAdapters,
  actionMessage,
  toolFilter,
  toolStatusFilter,
  toolEventFilter,
  toolCallIdFilter,
  toolLimit,
  onRollback,
  onApprove,
  onReject,
  onToolFilterChange,
  onToolStatusFilterChange,
  onToolEventFilterChange,
  onToolCallIdFilterChange,
  onToolLimitChange,
}: {
  toolCalls: ToolAuditEntry[];
  toolApprovals: ToolApprovalRecord[];
  toolAdapters: McpAdapterStatus[];
  actionMessage: string;
  toolFilter: string;
  toolStatusFilter: string;
  toolEventFilter: string;
  toolCallIdFilter: string;
  toolLimit: number;
  onRollback: (callId: string) => Promise<void>;
  onApprove: (approvalId: string) => Promise<void>;
  onReject: (approvalId: string) => Promise<void>;
  onToolFilterChange: (value: string) => void;
  onToolStatusFilterChange: (value: string) => void;
  onToolEventFilterChange: (value: string) => void;
  onToolCallIdFilterChange: (value: string) => void;
  onToolLimitChange: (value: number) => void;
}): JSX.Element {
  const recent = toolCalls.slice(-8).reverse();
  const pendingApprovals = toolApprovals.filter(
    (approval) => metaText(approval, "status", "pending") === "pending",
  );
  const recentPendingApprovals = pendingApprovals.slice(-3).reverse();
  return (
    <div className="mt-3 grid gap-3 xl:grid-cols-[1.3fr,0.7fr]">
      <div className="rounded border border-mars-border bg-mars-bg/40 p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-500">工具审计</span>
          <span className="font-mono text-[10px] text-slate-500">{toolCalls.length} 次调用</span>
        </div>
        <div className="mb-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-[1fr,150px,170px,120px,90px]">
          <input
            value={toolFilter}
            onChange={(event) => onToolFilterChange(event.target.value)}
            placeholder="筛选工具"
            className="rounded border border-mars-border bg-mars-panel px-2 py-1 text-xs text-slate-200 outline-none focus:border-cyan-500/60"
          />
          <select
            value={toolStatusFilter}
            onChange={(event) => onToolStatusFilterChange(event.target.value)}
            className="rounded border border-mars-border bg-mars-panel px-2 py-1 text-xs text-slate-200 outline-none focus:border-cyan-500/60"
          >
            <option value="">全部状态</option>
            <option value="success">成功</option>
            <option value="requires_approval">需要审批</option>
            <option value="blocked">已阻塞</option>
            <option value="error">错误</option>
          </select>
          <select
            value={toolEventFilter}
            onChange={(event) => onToolEventFilterChange(event.target.value)}
            className="rounded border border-mars-border bg-mars-panel px-2 py-1 text-xs text-slate-200 outline-none focus:border-cyan-500/60"
          >
            <option value="">全部事件</option>
            <option value="tool.started">已开始</option>
            <option value="tool.completed">已完成</option>
            <option value="tool.failed">失败</option>
            <option value="tool.blocked">阻塞</option>
            <option value="tool.requires_approval">需要审批</option>
            <option value="tool.rolled_back">已回滚</option>
          </select>
          <input
            value={toolCallIdFilter}
            onChange={(event) => onToolCallIdFilterChange(event.target.value)}
            placeholder="调用 ID"
            className="rounded border border-mars-border bg-mars-panel px-2 py-1 font-mono text-xs text-slate-200 outline-none focus:border-cyan-500/60"
          />
          <input
            value={toolLimit}
            onChange={(event) => {
              const next = Number(event.target.value);
              onToolLimitChange(Number.isFinite(next) ? Math.max(1, Math.min(500, next)) : 80);
            }}
            type="number"
            min={1}
            max={500}
            className="rounded border border-mars-border bg-mars-panel px-2 py-1 text-xs text-slate-200 outline-none focus:border-cyan-500/60"
          />
        </div>
        {recent.length > 0 ? (
          <div className="space-y-2">
            {recent.map((call, index) => {
              const callId = metaText(call, "call_id");
              const rollbackRef = metaText(call, "rollback_ref");
              const status = metaText(call, "status", "unknown");
              return (
                <div
                  key={callId || `${metaText(call, "tool", "tool")}-${index}`}
                  className="rounded border border-mars-border bg-mars-panel/40 p-2"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-[10px] text-cyan-200">
                      {metaText(call, "tool", metaText(call, "tool_name", "tool"))}
                    </span>
                    <span className={statusClass(status)}>
                      {statusLabel(status)}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-slate-500">
                    <span>Agent={metaText(call, "agent", "-")}</span>
                    <span>耗时={metaText(call, "duration_ms", "-")}ms</span>
                    {callId ? <span>id={callId.slice(0, 8)}</span> : null}
                  </div>
                  {metaText(call, "error") ? (
                    <p className="mt-1 line-clamp-2 text-[11px] text-red-200">
                      {metaText(call, "error")}
                    </p>
                  ) : null}
                  {rollbackRef && callId ? (
                    <button
                      onClick={() => void onRollback(callId)}
                      className="mt-2 rounded border border-amber-500/50 px-2 py-1 text-[10px] font-semibold text-amber-200 hover:bg-amber-500/10"
                    >
                      回滚
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-xs text-slate-500">暂无工具调用。</p>
        )}
        {actionMessage ? (
          <p className="mt-2 font-mono text-[10px] text-slate-400">{actionMessage}</p>
        ) : null}

        <div className="mt-3 rounded border border-mars-border bg-mars-panel/30 p-2">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-500">工具审批</span>
            <span className="font-mono text-[10px] text-slate-500">
              待处理={pendingApprovals.length}
            </span>
          </div>
          {recentPendingApprovals.length > 0 ? (
            <div className="space-y-2">
              {recentPendingApprovals.map((approval) => {
                const approvalId = metaText(approval, "approval_id");
                const status = metaText(approval, "status", "pending");
                return (
                  <div
                    key={approvalId || shortJson(approval)}
                    className="rounded border border-mars-border bg-mars-bg/40 p-2"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-mono text-[10px] text-cyan-200">
                        {metaText(approval, "tool", "tool")}
                      </span>
                      <span className={statusClass(status)}>{statusLabel(status)}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[11px] text-slate-400">
                      {metaText(approval, "reason", metaText(approval, "gate", "等待审核"))}
                    </p>
                    {status === "pending" && approvalId ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <button
                          onClick={() => void onApprove(approvalId)}
                          className="rounded border border-emerald-500/50 px-2 py-1 text-[10px] font-semibold text-emerald-200 hover:bg-emerald-500/10"
                        >
                          批准
                        </button>
                        <button
                          onClick={() => void onReject(approvalId)}
                          className="rounded border border-red-500/50 px-2 py-1 text-[10px] font-semibold text-red-200 hover:bg-red-500/10"
                        >
                          驳回
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-slate-500">暂无待审批工具调用。</p>
          )}
        </div>
      </div>

      <div className="rounded border border-mars-border bg-mars-bg/40 p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-500">MCP 适配器</span>
          <span className="font-mono text-[10px] text-slate-500">{toolAdapters.length} 类</span>
        </div>
        {toolAdapters.length > 0 ? (
          <div className="space-y-2">
            {toolAdapters.map((adapter) => (
              <div key={adapter.kind} className="rounded border border-mars-border bg-mars-panel/40 p-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[10px] text-cyan-200">{adapter.kind}</span>
                  <span className={adapter.available ? statusClass("success") : statusClass("disabled")}>
                    {adapter.available ? "可用" : adapter.configured ? "已配置" : "降级"}
                  </span>
                </div>
                <p className="mt-1 line-clamp-2 text-[11px] text-slate-300">{adapter.detail}</p>
                <p className="mt-1 line-clamp-1 font-mono text-[10px] text-slate-500">
                  降级={adapter.fallback}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-slate-500">暂无适配器状态。</p>
        )}
      </div>
    </div>
  );
}

function CommanderAttemptAudit({ attempt }: { attempt: Record<string, unknown> }): JSX.Element {
  const context = asRecord(attempt["context"]);
  const feedback = asRecord(context["feedback_context"]);
  const compression = asRecord(context["compression"]);
  const guards = asRecord(context["pollution_guards"]);
  const attribution = asRecord(attempt["attribution"]);
  const rejected = asRecordList(attempt["rejected_alternatives"]);
  const evidence = asStringList(attempt["evidence_refs"]);
  const observability = asBoolRecord(attempt["observability"]);
  return (
    <div className="rounded border border-mars-border bg-mars-panel/40 p-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[10px] text-cyan-200">
          第 {metaText(attempt, "attempt")} 轮 · {metaText(attempt, "recommended_target", "无")}
        </span>
        <span className="text-[10px] text-slate-500">
          置信度={metaText(attempt, "confidence", "0")} 已拒绝={rejected.length}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-slate-300">
        {metaText(attribution, "why_this_agent", metaText(attribution, "expected_fix", ""))}
      </p>
      <div className="mt-2 grid gap-2 md:grid-cols-3">
        <AuditCell
          label="证据"
          value={evidence.slice(0, 2).join(", ") || "无"}
        />
        <AuditCell
          label="上下文"
          value={
            metaText(feedback, "compressed_chars")
              ? `${metaText(feedback, "compressed_chars")}/${metaText(feedback, "original_chars")} 字符`
              : "未注入"
          }
        />
        <AuditCell
          label="护栏"
          value={`target_only=${metaText(guards, "target_only", "false")}`}
        />
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {Object.entries(observability).map(([key, value]) => (
          <span
            key={key}
            className={`rounded px-1.5 py-0.5 text-[10px] ${
              value ? "bg-emerald-500/15 text-emerald-200" : "bg-slate-800 text-slate-500"
            }`}
          >
            {key}
          </span>
        ))}
        {metaText(compression, "strategy") ? (
          <span className="rounded bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-200">
            {metaText(compression, "strategy")}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function AuditCell({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="min-w-0 rounded bg-mars-bg/60 px-2 py-1">
      <p className="text-[9px] uppercase text-slate-600">{label}</p>
      <p className="truncate font-mono text-[10px] text-slate-300">{value}</p>
    </div>
  );
}

function MetricTile({ label, value }: { label: string; value: number }): JSX.Element {
  return (
    <div className="rounded bg-mars-bg/60 px-2 py-1.5">
      <p className="text-[9px] uppercase text-slate-600">{label}</p>
      <p className="font-mono text-sm font-semibold text-slate-100">
        {Math.round(value * 100)}%
      </p>
    </div>
  );
}

function EvalCaseRow({ item }: { item: Record<string, unknown> }): JSX.Element {
  const expected = asRecord(item["expected"]);
  const actual = asRecord(item["actual"]);
  const passed = item["passed"] === true;
  return (
    <div className="rounded border border-mars-border bg-mars-panel/40 px-2 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-[10px] text-slate-300">
          {metaText(item, "id")}
        </span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${
          passed ? "bg-emerald-500/15 text-emerald-200" : "bg-red-500/15 text-red-200"
        }`}>
          {passed ? "通过" : "失败"}
        </span>
      </div>
      <p className="mt-1 font-mono text-[10px] text-slate-500">
        期望={metaText(expected, "target_agent")} 实际={metaText(actual, "target_agent")}
      </p>
    </div>
  );
}

function metaText(source: Record<string, unknown>, key: string, fallback = ""): string {
  const value = source[key];
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function isMutationLever(lever: SelfEvolutionLeverItem): boolean {
  return lever.source === "agent_context"
    && ["prompt", "few_shot", "eval"].includes(lever.lever_type)
    && Boolean(lever.agent)
    && Boolean(lever.title);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asRecordList(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => asRecord(item))
    .filter((item) => Object.keys(item).length > 0);
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item : ""))
    .filter((item) => item.length > 0);
}

function asBoolRecord(value: unknown): Record<string, boolean> {
  const record = asRecord(value);
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [key, item === true]),
  );
}

function metaListText(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === "string") return item;
      if (typeof item === "number" || typeof item === "boolean") return String(item);
      return "";
    })
    .filter((item) => item.length > 0);
}

function shortJson(value: unknown): string {
  try {
    return JSON.stringify(value).slice(0, 240);
  } catch {
    return "";
  }
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function spanBelongsToAgent(span: TraceSpan, activeAgent: string): boolean {
  const attrs = span.attributes;
  const stage = metaText(attrs, "stage");
  const agent = metaText(attrs, "agent");
  const toolName = metaText(attrs, "tool_name");
  if (activeAgent === "commander") {
    return agent === "commander" || agent === "bridge" || span.kind === "agent";
  }
  if (stage === activeAgent || agent === activeAgent) return true;
  if (activeAgent === "execution" && toolName.startsWith("execution.")) return true;
  if (activeAgent === "coding" && toolName.startsWith("code.")) return true;
  return false;
}

function toolCallBelongsToAgent(entry: ToolAuditEntry, activeAgent: string): boolean {
  const agent = metaText(entry, "agent");
  const tool = metaText(entry, "tool", metaText(entry, "tool_name"));
  if (activeAgent === "commander") {
    return agent === "commander" || agent === "bridge";
  }
  if (agent === activeAgent) return true;
  if (activeAgent === "execution" && tool.startsWith("execution.")) return true;
  if (activeAgent === "coding" && tool.startsWith("code.")) return true;
  return false;
}

function buildReactToolSteps(toolCalls: ToolAuditEntry[], activeAgent: string): ReactToolStep[] {
  const grouped = new Map<string, ReactToolStep>();
  toolCalls.forEach((entry, index) => {
    if (!toolCallBelongsToAgent(entry, activeAgent)) return;
    const metadata = asRecord(entry["metadata"]);
    const callId = metaText(entry, "call_id", metaText(metadata, "tool_call_id"));
    const tool = metaText(entry, "tool", metaText(entry, "tool_name", "tool"));
    const timestamp = metaText(entry, "timestamp");
    const id = callId || `${tool}-${timestamp}-${index}`;
    const previous = grouped.get(id);
    const result = toolResultPayload(entry);
    grouped.set(id, {
      id,
      callId,
      tool,
      agent: metaText(entry, "agent", activeAgent),
      status: metaText(entry, "status", previous?.status || metaText(entry, "event", "running")),
      event: metaText(entry, "event", previous?.event || ""),
      timestamp: timestamp || previous?.timestamp || "",
      durationMs: metaText(entry, "duration_ms", previous?.durationMs || "-"),
      args: entry["args"] ?? previous?.args ?? {},
      result: Object.keys(result).length > 0 ? result : previous?.result ?? {},
    });
  });
  return [...grouped.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
}

function toolResultPayload(entry: ToolAuditEntry): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries({
      ok: entry["ok"],
      status: entry["status"],
      event: entry["event"],
      error: entry["error"],
      blocked_by_gate: entry["blocked_by_gate"],
      requires_approval: entry["requires_approval"],
      rollback_ref: entry["rollback_ref"],
      evidence_refs: entry["evidence_refs"],
      metadata: entry["metadata"],
      duration_ms: entry["duration_ms"],
    }).filter(([, value]) => value !== undefined && value !== null && value !== ""),
  );
}

function toolObservationSummary(step: ReactToolStep): string {
  const error = metaText(step.result, "error");
  if (error) return `错误：${error}`;
  const evidence = metaListText(step.result["evidence_refs"]);
  if (evidence.length > 0) return `返回 ${statusLabel(step.status)} · 证据 ${evidence.slice(0, 2).join(", ")}`;
  const metadata = asRecord(step.result["metadata"]);
  const metadataKeys = Object.keys(metadata);
  if (metadataKeys.length > 0) return `返回 ${statusLabel(step.status)} · metadata ${metadataKeys.slice(0, 3).join(", ")}`;
  return `返回 ${statusLabel(step.status)} · ${step.durationMs}ms`;
}

function spanDuration(span: TraceSpan): string {
  const start = Date.parse(span.started_at);
  const end = span.ended_at ? Date.parse(span.ended_at) : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "-";
  return `${Math.max(0, end - start)}ms`;
}

function traceSpanLabel(span: TraceSpan): string {
  return span.name.startsWith(`${span.kind}:`) ? span.name : `${span.kind}:${span.name}`;
}

function reactCurrentWork(
  activeAgent: string,
  agentState: string,
  latestSpan: TraceSpan | null,
  latestTool: ReactToolStep | null,
  latestEvent: WSMessage | undefined,
): string {
  if (latestTool && ["running", "tool.started"].includes(latestTool.status)) {
    return `正在调用工具 ${latestTool.tool}`;
  }
  if (agentState === "running") {
    return latestSpan
      ? `正在执行 ${latestSpan.name}`
      : "正在读取上下文、推理下一步动作或等待工具返回。";
  }
  if (agentState === "waiting_review") return "产物已生成，等待人工审核。";
  if (agentState === "done" || agentState === "approved") return "当前 Agent 已完成，正在沉淀产物、Trace 与记忆。";
  if (agentState === "failed") return "当前 Agent 失败，等待诊断与反馈回路。";
  const eventPayload = asRecord(latestEvent?.payload);
  const toState = metaText(eventPayload, "to_state");
  return toState ? `最近状态跳转到 ${toState}` : `${agentLabel(activeAgent)} 等待状态机调度。`;
}

function parseEvaluationSummary(value: unknown): ArtifactEvaluationSummary | null {
  const record = asRecord(value);
  const artifactRef = metaText(record, "artifact_ref");
  const agent = metaText(record, "agent");
  if (!artifactRef || !agent) return null;
  return {
    agent,
    node: metaText(record, "node", agent),
    artifact_ref: artifactRef,
    artifact_id: metaText(record, "artifact_id"),
    stem: metaText(record, "stem"),
    version: metaText(record, "version"),
    decision: metaText(record, "decision", "pass"),
    blocking: record["blocking"] === true,
    report_count: nullableNumber(record["report_count"]) ?? 0,
    overall_score: nullableNumber(record["overall_score"]),
    top_findings: asEvaluationFindings(record["top_findings"]),
    reports: asEvaluationReportItems(record["reports"]),
    policy: parseEvaluationPolicy(record["policy"]) ?? undefined,
  };
}

function relativeArtifactRef(view: ArtifactView): string {
  return `${view.agent_dir}/${view.stem}.${view.version}.md`;
}

function buildSummaryFromReports(
  view: ArtifactView,
  reports: ArtifactEvaluationReport[],
): ArtifactEvaluationSummary | null {
  if (reports.length === 0) return null;
  const items = reports.map(reportItemFromReport);
  return {
    agent: view.agent_dir,
    node: view.agent_dir,
    artifact_ref: relativeArtifactRef(view),
    artifact_id: `${view.stem}.${view.version}.md`,
    stem: view.stem,
    version: view.version,
    decision: worstDecision(items),
    blocking: items.some((item) => item.blocking === true || item.decision === "block" || item.decision === "fail"),
    report_count: items.length,
    overall_score: averageScore(items),
    top_findings: collectTopFindings(items, 5),
    reports: items,
  };
}

function stageEvaluationBadge(
  stage: string,
  scorecard: EvaluationScorecard | null,
  liveSummaries: Record<string, ArtifactEvaluationSummary>,
): EvaluationBadgeSummary | null {
  const live = Object.values(liveSummaries).filter((summary) => summary.agent === stage);
  if (live.length > 0) {
    const latest = live[live.length - 1];
    return {
      decision: latest.decision,
      overall_score: latest.overall_score,
      blocking: latest.blocking,
    };
  }
  if (!scorecard) return null;
  const items = scorecard.reports.filter((report) =>
    typeof report.target_ref === "string" && report.target_ref.startsWith(`${stage}/`),
  );
  if (items.length === 0) return null;
  return {
    decision: worstDecision(items),
    overall_score: averageScore(items),
    blocking: items.some((item) => item.blocking === true || item.decision === "block" || item.decision === "fail"),
  };
}

function reportItemFromReport(report: ArtifactEvaluationReport): EvaluationReportItem {
  const findings = asEvaluationFindings(report.metadata.findings);
  return {
    path: report.path,
    target_ref: metaText(report.metadata, "target_ref"),
    target_schema: metaText(report.metadata, "target_schema"),
    evaluator: metaText(report.metadata, "evaluator", report.evaluator_slug),
    decision: metaText(report.metadata, "decision", "pass"),
    blocking: report.metadata.blocking === true,
    overall_score: nullableNumber(report.metadata.overall_score),
    finding_count: findings.length,
    findings,
  };
}

function parseEvaluationPolicy(value: unknown): EvaluationPolicyDecision | null {
  const record = asRecord(value);
  const gate = metaText(record, "gate");
  const action = metaText(record, "action");
  if (!gate || !action) return null;
  return {
    schema: metaText(record, "schema", "evaluation_policy_decision.v1"),
    scope: metaText(record, "scope", "artifact"),
    gate,
    action,
    review_priority: metaText(record, "review_priority", "normal"),
    auto_approval_allowed:
      typeof record["auto_approval_allowed"] === "boolean"
        ? record["auto_approval_allowed"]
        : undefined,
    auto_approval_enforced:
      typeof record["auto_approval_enforced"] === "boolean"
        ? record["auto_approval_enforced"]
        : undefined,
    completion_allowed:
      typeof record["completion_allowed"] === "boolean"
        ? record["completion_allowed"]
        : undefined,
    enforcement_mode: optionalText(record, "enforcement_mode"),
    thresholds: asRecord(record["thresholds"]),
    reasons: asStringList(record["reasons"]),
  };
}

function asEvaluationReportItems(value: unknown): EvaluationReportItem[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const record = asRecord(item);
    const findings = asEvaluationFindings(record["findings"]);
    return {
      path: optionalText(record, "path"),
      target_ref: optionalText(record, "target_ref"),
      target_schema: optionalText(record, "target_schema"),
      evaluator: optionalText(record, "evaluator"),
      decision: optionalText(record, "decision") ?? "pass",
      blocking: record["blocking"] === true,
      overall_score: nullableNumber(record["overall_score"]),
      finding_count: nullableNumber(record["finding_count"]) ?? findings.length,
      findings,
    };
  });
}

function asEvaluationFindings(value: unknown): EvaluationFinding[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const record = asRecord(item);
    return {
      id: optionalText(record, "id"),
      severity: optionalText(record, "severity"),
      category: optionalText(record, "category"),
      message: optionalText(record, "message"),
      evidence_refs: asStringList(record["evidence_refs"]),
      target_ref: optionalText(record, "target_ref"),
      evaluator: optionalText(record, "evaluator"),
    };
  });
}

function collectTopFindings(
  items: EvaluationReportItem[],
  limit: number,
): EvaluationFinding[] {
  const findings = items.flatMap((item) =>
    (item.findings ?? []).map((finding) => ({
      ...finding,
      target_ref: finding.target_ref ?? item.target_ref,
      evaluator: finding.evaluator ?? item.evaluator,
    })),
  );
  findings.sort((left, right) => severityRank(left.severity) - severityRank(right.severity));
  return findings.slice(0, limit);
}

function worstDecision(items: EvaluationReportItem[]): EvaluationDecision | string {
  const decisions = items
    .map((item) => item.decision)
    .filter((decision): decision is EvaluationDecision => isEvaluationDecision(decision));
  if (decisions.length === 0) return "pass";
  return decisions.reduce((worst, decision) =>
    DECISION_RANK[decision] > DECISION_RANK[worst] ? decision : worst,
  );
}

function averageScore(items: EvaluationReportItem[]): number | null {
  const scores = items
    .map((item) => item.overall_score)
    .filter((score): score is number => typeof score === "number");
  if (scores.length === 0) return null;
  return Math.round((scores.reduce((sum, score) => sum + score, 0) / scores.length) * 1000) / 1000;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalText(source: Record<string, unknown>, key: string): string | undefined {
  const value = source[key];
  if (typeof value === "string" && value.length > 0) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return undefined;
}

function isEvaluationDecision(value: unknown): value is EvaluationDecision {
  return value === "pass" || value === "warn" || value === "revise" || value === "block" || value === "fail";
}

function formatScore(value: number | null): string {
  if (value === null) return "-";
  return `${Math.round(value * 100)}%`;
}

function decisionClass(decision: EvaluationDecision | string, blocking?: boolean): string {
  if (blocking || decision === "block" || decision === "fail") {
    return "bg-red-500/20 text-red-200";
  }
  if (decision === "revise") return "bg-cyan-500/20 text-cyan-200";
  if (decision === "warn") return "bg-amber-500/20 text-amber-200";
  return "bg-emerald-500/20 text-emerald-200";
}

function severityClass(severity: string | undefined): string {
  if (severity === "blocker" || severity === "high") return "bg-red-500/20 text-red-200";
  if (severity === "medium") return "bg-amber-500/20 text-amber-200";
  if (severity === "low") return "bg-cyan-500/20 text-cyan-200";
  return "bg-slate-700 text-slate-300";
}

function priorityClass(priority: string): string {
  if (priority === "critical") return "bg-red-500/20 text-red-200";
  if (priority === "high") return "bg-cyan-500/20 text-cyan-200";
  if (priority === "elevated") return "bg-amber-500/20 text-amber-200";
  return "bg-emerald-500/20 text-emerald-200";
}

function severityRank(severity: string | undefined): number {
  if (severity === "blocker") return 0;
  if (severity === "high") return 1;
  if (severity === "medium") return 2;
  if (severity === "low") return 3;
  return 4;
}

function ArtifactBodyEditor({
  text,
  onChange,
  frontmatter,
}: {
  text: string;
  onChange: (value: string) => void;
  frontmatter: string;
}): JSX.Element {
  const { t } = useI18n();
  return (
    <section className="flex min-h-[300px] flex-1 flex-col overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="flex items-center justify-between border-b border-mars-border px-3 py-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">{t("artifact.body")}</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">{t("artifact.editorHint")}</p>
        </div>
      </div>
      <textarea
        value={text}
        onChange={(e) => onChange(e.target.value)}
        className="min-h-[260px] w-full flex-1 resize-none bg-transparent p-4 font-mono text-sm leading-relaxed text-slate-100 outline-none"
      />
      {frontmatter ? (
        <details className="border-t border-mars-border bg-mars-panel/35">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-slate-300 hover:text-slate-100">
            {t("artifact.metadata")}
          </summary>
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words px-3 pb-3 font-mono text-[11px] leading-relaxed text-slate-400">
            {frontmatter}
          </pre>
        </details>
      ) : null}
    </section>
  );
}

function StateBadge({ state }: { state: string }): JSX.Element {
  const { t } = useI18n();
  const classes: Record<string, string> = {
    configured: "bg-cyan-500/25 text-cyan-200",
    pending: "bg-slate-700 text-slate-200",
    running: "bg-amber-500/30 text-amber-200",
    waiting_review: "bg-fuchsia-500/30 text-fuchsia-200",
    approved: "bg-emerald-500/30 text-emerald-200",
    done: "bg-emerald-500/40 text-emerald-100",
    failed: "bg-red-500/40 text-red-100",
    skipped: "bg-slate-800 text-slate-400",
  };
  const label = state === "configured" ? "已配置" : t(`state.${state}`);
  return (
    <span className={`rounded px-2 py-0.5 text-[10px] font-medium uppercase ${classes[state] ?? "bg-slate-700"}`}>
      {label}
    </span>
  );
}

function agentLabel(agent: string): string {
  const labels: Record<string, string> = {
    commander: "Commander Agent",
    idea: "Idea Agent",
    experiment: "Experiment Agent",
    coding: "Coding Agent",
    execution: "Execution Agent",
    writing: "Writing Agent",
  };
  return labels[agent] ?? agent;
}

function RunningOrEmpty({
  agentState,
  debate,
  open,
  onToggle,
}: {
  agentState: string;
  debate: DebateTranscript | null;
  open: boolean;
  onToggle: () => void;
}): JSX.Element {
  const { t } = useI18n();
  const isRunning = agentState === "running";

  if (debate?.exists && debate.text) {
    return (
      <>
        {isRunning ? (
          <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            {t("run.empty.runningWithDebate")}
          </div>
        ) : null}
        <DebatePanel debate={debate} open={true} onToggle={onToggle} modeFromMeta="" />
      </>
    );
  }
  if (isRunning) {
    return (
      <div className="rounded border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-200">
        {t("run.empty.running")}
      </div>
    );
  }
  return (
    <p className="text-sm text-slate-500">
      {t("run.empty.noArtifact")}
    </p>
  );
}

function DebatePanel({
  debate,
  open,
  onToggle,
  modeFromMeta,
}: {
  debate: DebateTranscript | null;
  open: boolean;
  onToggle: () => void;
  modeFromMeta: string;
}): JSX.Element | null {
  const { t } = useI18n();
  if (debate === null || !debate.exists) {
    // hide entirely for non-debate agents to avoid noise
    return null;
  }
  return (
    <div className="rounded border border-fuchsia-500/30 bg-fuchsia-500/5">
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs hover:bg-fuchsia-500/10"
      >
        <span className="font-semibold text-fuchsia-200">
          🗣 {t("debate.title")}
          {modeFromMeta ? (
            <span className="ml-2 rounded bg-fuchsia-500/20 px-1.5 py-0.5 font-mono text-[10px] text-fuchsia-100">
              {t("debate.modeLabel")}={modeFromMeta}
            </span>
          ) : null}
        </span>
        <span className="text-[11px] text-fuchsia-300">
          {open ? t("debate.hide") : t("debate.show")}
        </span>
      </button>
      {open ? (
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words border-t border-fuchsia-500/20 bg-mars-bg/40 p-3 font-mono text-[11px] leading-relaxed text-slate-200">
          {debate.text}
        </pre>
      ) : null}
    </div>
  );
}

function ValidationBadge({ view }: { view: ArtifactView }): JSX.Element {
  const { t } = useI18n();
  if (view.valid) {
    const text = t("artifact.valid")
      .replace("{schema}", view.schema_id ?? "?")
      .replace("{version}", view.version);
    return (
      <div className="rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
        {text}
      </div>
    );
  }
  const invalidText = t("artifact.invalid").replace("{schema}", view.schema_id ?? "?");
  return (
    <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200">
      {invalidText}
      <ul className="mt-1 list-disc pl-4">
        {view.errors.map((e, i) => (
          <li key={i}>
            <span className="font-mono">{e.path}</span>: {e.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

type EvaluationBadgeSummary = {
  decision: EvaluationDecision | string;
  overall_score: number | null;
  blocking?: boolean;
};

const DECISION_RANK: Record<EvaluationDecision, number> = {
  pass: 0,
  warn: 1,
  revise: 2,
  block: 3,
  fail: 4,
};

function RunEvaluationScorecard({
  scorecard,
  postTrainingExport,
  exportMessage,
  onExportPostTraining,
}: {
  scorecard: EvaluationScorecard;
  postTrainingExport: PostTrainingExportManifest | null;
  exportMessage: string;
  onExportPostTraining: () => Promise<void>;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const qualityGate = scorecard.quality_gate;
  const counts = ["pass", "warn", "revise", "block", "fail"].map((decision) => ({
    decision,
    count: scorecard.counts[decision] ?? 0,
  }));
  return (
    <section className="rounded border border-mars-border bg-mars-panel/45">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full flex-wrap items-center justify-between gap-3 px-3 py-3 text-left transition hover:bg-mars-bg/35"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase text-slate-400">
            质量评估总览
          </span>
          <EvaluationDecisionBadge
            decision={scorecard.overall_decision}
            blocking={scorecard.overall_decision === "block" || scorecard.overall_decision === "fail"}
          />
          {qualityGate ? (
            <EvaluationDecisionBadge
              decision={qualityGate.gate}
              blocking={qualityGate.gate === "block"}
            />
          ) : null}
          <span className="font-mono text-[11px] text-slate-500">
            分数={formatScore(scorecard.overall_score)}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {counts.map((item) => (
            <span
              key={item.decision}
              className="rounded bg-mars-bg px-2 py-0.5 font-mono text-[10px] text-slate-400"
            >
              {decisionLabel(item.decision)}:{item.count}
            </span>
          ))}
          <span className="rounded bg-mars-bg px-2 py-0.5 font-mono text-[10px] text-slate-400">
            发现:{scorecard.finding_count}
          </span>
          {postTrainingExport ? (
            <span className="rounded bg-emerald-500/10 px-2 py-0.5 font-mono text-[10px] text-emerald-200">
              导出:{postTrainingExport.eligible_count}/{postTrainingExport.record_count}
            </span>
          ) : null}
          <span className="rounded bg-mars-bg px-2 py-0.5 font-mono text-[10px] text-slate-400">
            {open ? "收起" : "展开"}
          </span>
        </div>
      </button>
      {open ? (
      <div className="border-t border-mars-border px-3 pb-3 pt-3">
        <div className="mb-3 flex flex-wrap justify-end gap-2">
          <button
            onClick={() => void onExportPostTraining()}
            className="rounded border border-mars-border bg-mars-bg px-2 py-1 text-[10px] font-medium uppercase text-slate-300 hover:bg-mars-panel"
          >
            导出 JSONL
          </button>
        </div>
        {qualityGate ? <PolicyDecisionStrip policy={qualityGate} /> : null}
        {postTrainingExport || exportMessage ? (
        <div className="mt-3 rounded border border-mars-border bg-mars-bg/50 px-2 py-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[10px] text-slate-300">
              post_training_export
            </span>
            {postTrainingExport ? (
              <>
                <span className="font-mono text-[10px] text-slate-500">
                  path={postTrainingExport.path}
                </span>
                <span className="font-mono text-[10px] text-slate-500">
                  草稿={postTrainingExport.include_drafts ? "是" : "否"}
                </span>
              </>
            ) : null}
            {exportMessage ? (
              <span className="font-mono text-[10px] text-cyan-200">
                {exportMessage}
              </span>
            ) : null}
          </div>
          {postTrainingExport?.records_preview?.[0] ? (
            <p className="mt-1 truncate font-mono text-[10px] text-slate-500">
              样例={metaText(asRecord(postTrainingExport.records_preview[0].artifact), "ref")}
            </p>
          ) : null}
        </div>
        ) : null}
        {scorecard.top_findings.length > 0 ? (
          <EvaluationFindingList findings={scorecard.top_findings.slice(0, 3)} compact />
        ) : null}
      </div>
      ) : null}
    </section>
  );
}

function ArtifactEvaluationPanel({
  summary,
  reports,
}: {
  summary: ArtifactEvaluationSummary | null;
  reports: ArtifactEvaluationReport[];
}): JSX.Element {
  const reportItems = summary?.reports ?? reports.map(reportItemFromReport);
  const findings = summary?.top_findings ?? collectTopFindings(reportItems, 5);
  const policy = summary?.policy;
  return (
    <section className="rounded border border-mars-border bg-mars-panel/45 p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-100">产物评价</h3>
          {summary ? (
            <>
              <EvaluationDecisionBadge
                decision={summary.decision}
                blocking={summary.blocking}
              />
              <span className="font-mono text-[11px] text-slate-500">
                分数={formatScore(summary.overall_score)}
              </span>
            </>
          ) : (
            <span className="rounded bg-slate-700 px-2 py-0.5 text-[10px] uppercase text-slate-300">
              待处理
            </span>
          )}
        </div>
        <span className="font-mono text-[11px] text-slate-500">
          报告={summary?.report_count ?? reports.length}
        </span>
      </div>

      {policy ? <PolicyDecisionStrip policy={policy} /> : null}

      {reportItems.length > 0 ? (
        <div className="mt-3 grid gap-2 md:grid-cols-3">
          {reportItems.map((report, index) => (
            <div
              key={`${report.path ?? report.evaluator ?? "report"}-${index}`}
              className="min-w-0 rounded border border-mars-border bg-mars-bg/50 px-2 py-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-[10px] text-slate-300">
                  {report.evaluator ?? "evaluator"}
                </span>
                <EvaluationDecisionBadge
                  decision={report.decision ?? "pass"}
                  blocking={report.blocking}
                  compact
                />
              </div>
              <p className="mt-1 font-mono text-[10px] text-slate-500">
                分数={formatScore(report.overall_score ?? null)} 发现={report.finding_count ?? report.findings?.length ?? 0}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      {findings.length > 0 ? (
        <EvaluationFindingList findings={findings} />
      ) : (
        <p className="mt-3 text-xs text-slate-500">暂无评价发现。</p>
      )}
    </section>
  );
}

function PolicyDecisionStrip({
  policy,
}: {
  policy: EvaluationPolicyDecision;
}): JSX.Element {
  const allowText =
    policy.scope === "run"
      ? `完成=${policy.completion_allowed === false ? "阻塞" : "允许"}`
      : `自动批准=${policy.auto_approval_allowed === false ? "暂停" : "允许"}`;
  return (
    <div className="mt-3 rounded border border-mars-border bg-mars-bg/50 px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${priorityClass(policy.review_priority)}`}>
          {priorityLabel(policy.review_priority)}
        </span>
        <span className="font-mono text-[10px] text-slate-300">
          动作={policy.action}
        </span>
        <span className="font-mono text-[10px] text-slate-500">
          {allowText}
        </span>
        {policy.enforcement_mode ? (
          <span className="font-mono text-[10px] text-slate-500">
            模式={policy.enforcement_mode}
          </span>
        ) : null}
      </div>
      {policy.reasons.length > 0 ? (
        <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-slate-400">
          {policy.reasons.slice(0, 2).join(" · ")}
        </p>
      ) : null}
    </div>
  );
}

function EvaluationFindingList({
  findings,
  compact = false,
}: {
  findings: EvaluationFinding[];
  compact?: boolean;
}): JSX.Element {
  return (
    <ul className={`space-y-1.5 ${compact ? "mt-2" : "mt-3"}`}>
      {findings.map((finding, index) => (
        <li
          key={`${finding.evaluator ?? "eval"}-${finding.id ?? index}`}
          className="rounded border border-mars-border bg-mars-bg/50 px-2 py-1.5"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${severityClass(finding.severity)}`}>
              {severityLabel(finding.severity)}
            </span>
            <span className="font-mono text-[10px] text-slate-500">
              {finding.evaluator ?? finding.category ?? "评价"}
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-300">
            {finding.message ?? "无消息。"}
          </p>
        </li>
      ))}
    </ul>
  );
}

function EvaluationMiniBadge({
  summary,
}: {
  summary: EvaluationBadgeSummary;
}): JSX.Element {
  return (
    <span
      className={`rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ${decisionClass(summary.decision, summary.blocking)}`}
      title={`评价 ${summary.decision} · 分数 ${formatScore(summary.overall_score)}`}
    >
      {decisionLabel(summary.decision).slice(0, 4)}
    </span>
  );
}

function EvaluationDecisionBadge({
  decision,
  blocking,
  compact = false,
}: {
  decision: EvaluationDecision | string;
  blocking?: boolean;
  compact?: boolean;
}): JSX.Element {
  return (
    <span
      className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${decisionClass(decision, blocking)}`}
    >
      {compact ? decisionLabel(decision).slice(0, 4) : decisionLabel(decision)}
    </span>
  );
}

type ReactToolStep = {
  id: string;
  callId: string;
  tool: string;
  agent: string;
  status: string;
  event: string;
  timestamp: string;
  durationMs: string;
  args: unknown;
  result: Record<string, unknown>;
};

function AgentReActTracePanel({
  trace,
  toolCalls,
  activeAgent,
  agentState,
  events,
}: {
  trace: TraceManifest | null;
  toolCalls: ToolAuditEntry[];
  activeAgent: string;
  agentState: string;
  events: WSMessage[];
}): JSX.Element {
  const [open, setOpen] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const spans = trace?.spans.filter((span) => spanBelongsToAgent(span, activeAgent)).slice(-8) ?? [];
  const tools = buildReactToolSteps(toolCalls, activeAgent).slice(-6);
  const latestSpan = spans.at(-1) ?? null;
  const latestAgentSpan = spans.filter((span) => span.kind === "agent").at(-1) ?? latestSpan;
  const latestTool = tools.at(-1) ?? null;
  const latestEvent = [...events]
    .reverse()
    .find((event) => metaText(asRecord(event.payload), "agent") === activeAgent);
  const currentWork = reactCurrentWork(activeAgent, agentState, latestAgentSpan, latestTool, latestEvent);
  const latestObservation = latestTool
    ? toolObservationSummary(latestTool)
    : latestSpan
      ? `${latestSpan.name} · ${spanDuration(latestSpan)}`
      : "等待 Trace、工具返回或状态机事件。";

  const toggleStep = (id: string): void => {
    setExpanded((current) => ({ ...current, [id]: !current[id] }));
  };

  return (
    <section className="rounded border border-cyan-500/25 bg-mars-panel/45">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left transition hover:bg-cyan-500/5"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-100">ReAct 执行循环</h3>
            <span className="rounded bg-cyan-500/15 px-2 py-0.5 font-mono text-[10px] text-cyan-200">
              {agentLabel(activeAgent)}
            </span>
            <StateBadge state={agentState} />
          </div>
          <p className="mt-1 truncate text-xs text-slate-400">{currentWork}</p>
        </div>
        <span className="shrink-0 font-mono text-[11px] text-slate-500">
          {open ? "收起 ▴" : "展开 ▾"}
        </span>
      </button>

      {open ? (
        <div className="border-t border-mars-border p-3">
          <div className="grid gap-3 xl:grid-cols-[310px,1fr]">
            <div className="rounded border border-mars-border bg-mars-bg/45 p-3">
              <div className="grid gap-2">
                <ReactLoopCard
                  label="Think"
                  title="当前思考"
                  value={latestAgentSpan ? latestAgentSpan.name : currentWork}
                  tone="cyan"
                />
                <ReactLoopArrow />
                <ReactLoopCard
                  label="Act"
                  title="工具调用"
                  value={latestTool ? `${latestTool.tool} · ${statusLabel(latestTool.status)}` : "暂无工具调用"}
                  tone="amber"
                />
                <ReactLoopArrow />
                <ReactLoopCard
                  label="Observe"
                  title="返回结果"
                  value={latestObservation}
                  tone="emerald"
                />
              </div>
            </div>

            <div className="min-w-0 rounded border border-mars-border bg-mars-bg/45 p-3">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h4 className="text-xs font-semibold text-slate-200">工具调用与返回</h4>
                  <p className="mt-0.5 text-[11px] text-slate-500">
                    展示当前 Agent 相关的最近调用；参数和返回可逐条展开。
                  </p>
                </div>
                <span className="font-mono text-[10px] text-slate-500">
                  Trace {spans.length} · Tool {tools.length}
                </span>
              </div>

              {tools.length > 0 ? (
                <div className="space-y-2">
                  {tools.map((step) => {
                    const isOpen = expanded[step.id] ?? false;
                    return (
                      <div key={step.id} className="rounded border border-mars-border bg-mars-panel/35">
                        <button
                          type="button"
                          onClick={() => toggleStep(step.id)}
                          className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left hover:bg-mars-bg/45"
                        >
                          <span className="min-w-0">
                            <span className="block truncate font-mono text-[11px] text-cyan-100">
                              {step.tool}
                            </span>
                            <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">
                              {step.callId ? `call=${step.callId.slice(0, 10)}` : "call=unknown"} · {step.durationMs}
                            </span>
                          </span>
                          <span className="flex shrink-0 items-center gap-2">
                            <span className={statusClass(step.status)}>{statusLabel(step.status)}</span>
                            <span className="font-mono text-[10px] text-slate-500">
                              {isOpen ? "收起" : "展开"}
                            </span>
                          </span>
                        </button>
                        {isOpen ? (
                          <div className="grid gap-2 border-t border-mars-border p-3 lg:grid-cols-2">
                            <TraceJsonBlock title="调用参数" value={step.args} />
                            <TraceJsonBlock title="返回结果" value={step.result} />
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded border border-dashed border-mars-border px-3 py-6 text-center text-xs text-slate-500">
                  当前 Agent 暂无工具调用；可继续观察 Trace span 和状态机事件。
                </div>
              )}

              {spans.length > 0 ? (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {spans.map((span, index) => (
                    <span
                      key={`${span.span_id}-${index}`}
                      className="rounded border border-mars-border bg-mars-bg px-2 py-1 font-mono text-[10px] text-slate-400"
                    >
                      {traceSpanLabel(span)} · {spanDuration(span)}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function ReactLoopCard({
  label,
  title,
  value,
  tone,
}: {
  label: string;
  title: string;
  value: string;
  tone: "cyan" | "amber" | "emerald";
}): JSX.Element {
  const toneClass =
    tone === "cyan"
      ? "border-cyan-500/30 bg-cyan-500/10 text-cyan-100"
      : tone === "amber"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-100"
        : "border-emerald-500/30 bg-emerald-500/10 text-emerald-100";
  return (
    <div className={`rounded border px-3 py-2 ${toneClass}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-wider opacity-80">{label}</span>
        <span className="text-[10px] opacity-70">{title}</span>
      </div>
      <p className="mt-1 line-clamp-2 text-xs leading-relaxed">{value}</p>
    </div>
  );
}

function ReactLoopArrow(): JSX.Element {
  return (
    <div className="flex items-center justify-center text-cyan-300/45">
      <span className="h-5 w-px rounded bg-cyan-400/25" />
      <span className="ml-1 text-[10px]">↓</span>
    </div>
  );
}

function TraceJsonBlock({ title, value }: { title: string; value: unknown }): JSX.Element {
  return (
    <div className="min-w-0 rounded border border-mars-border bg-mars-bg/70">
      <div className="border-b border-mars-border px-2 py-1 text-[10px] font-semibold text-slate-400">
        {title}
      </div>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words p-2 font-mono text-[10px] leading-relaxed text-slate-300">
        {prettyJson(value)}
      </pre>
    </div>
  );
}

function PatchPanel({ patch }: { patch: PatchView }): JSX.Element {
  const { t } = useI18n();
  return (
    <section className="rounded border border-cyan-500/30 bg-cyan-500/5">
      <div className="flex items-center justify-between border-b border-cyan-500/20 px-3 py-2">
        <h3 className="text-sm font-semibold text-cyan-100">Patch {patch.version}</h3>
        <span className="rounded bg-mars-bg/70 px-2 py-0.5 text-[10px] uppercase text-cyan-200">
          {patch.approved ? t("patch.status.approved") : t("patch.status.pending")}
        </span>
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-[11px] leading-relaxed text-slate-200">
        {patch.text}
      </pre>
    </section>
  );
}

function TraceView({ trace }: { trace: TraceManifest | null }): JSX.Element | null {
  const { t } = useI18n();
  if (!trace) return null;
  const spans = [...trace.spans].slice(-16);
  if (spans.length === 0) return null;
  return (
    <section className="rounded border border-mars-border bg-mars-panel/40 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-100">{t("trace.title")}</h3>
        <span className="font-mono text-[10px] text-slate-500">{trace.trace_id.slice(0, 16)}</span>
      </div>
      <div className="space-y-1">
        {spans.map((span) => (
          <TraceRow key={span.span_id} span={span} />
        ))}
      </div>
    </section>
  );
}

function TraceRow({ span }: { span: TraceSpan }): JSX.Element {
  const start = Date.parse(span.started_at);
  const end = span.ended_at ? Date.parse(span.ended_at) : Date.now();
  const duration = Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : 0;
  const width = Math.min(100, Math.max(8, duration / 20));
  const statusClass =
    span.status === "error"
      ? "bg-red-400"
      : span.status === "running"
        ? "bg-amber-300"
        : "bg-emerald-400";
  return (
    <div className="grid grid-cols-[130px,1fr,70px] items-center gap-2 text-[11px]">
      <span className="truncate font-mono text-slate-400">{span.kind}</span>
      <div className="min-w-0">
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="truncate text-slate-200">{span.name}</span>
          <span className="font-mono text-slate-600">{span.span_id.slice(0, 6)}</span>
        </div>
        <div className="h-1.5 rounded bg-mars-bg">
          <div className={`h-1.5 rounded ${statusClass}`} style={{ width: `${width}%` }} />
        </div>
      </div>
      <span className="text-right font-mono text-slate-500">{duration}ms</span>
    </div>
  );
}

// ----------------------- Execution live curves panel -----------------------

type Curve = { experiment_id: string; metric: string; values: number[] };

function ExecutionLivePanel({ runId }: { runId: string }): JSX.Element {
  const { t } = useI18n();
  const [curves, setCurves] = useState<Curve[]>([]);
  const [plots, setPlots] = useState<ExecutionPlot[]>([]);
  const base = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

  useEffect(() => {
    let alive = true;
    const refresh = async (): Promise<void> => {
      try {
        const [names, nextPlots] = await Promise.all([
          fetch(`${base}/api/execution/${runId}/curves`).then((r) => r.json()),
          listExecutionPlots(runId).catch(() => []),
        ]);
        if (!alive) return;
        setPlots(nextPlots);
        if (!Array.isArray(names) || names.length === 0) {
          setCurves([]);
          return;
        }
        const fetched = await Promise.all(
          (names as string[]).map((n) => fetch(`${base}/api/execution/${runId}/curves/${n}`).then((r) => r.json())),
        );
        if (alive) setCurves(fetched as Curve[]);
      } catch {
        /* ignore */
      }
    };
    void refresh();
    const iv = setInterval(refresh, 1500);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [runId, base]);

  const orderedPlots = [...plots].sort((a, b) => a.experiment_id.localeCompare(b.experiment_id));
  const orderedCurves = [...curves].sort((a, b) => a.experiment_id.localeCompare(b.experiment_id));
  const featuredPlot = orderedPlots[0] ?? null;
  const featuredCurve = featuredPlot ? null : (orderedCurves[0] ?? null);
  const foldedCount = Math.max(0, (featuredPlot ? orderedPlots.length : orderedCurves.length) - 1);
  const recentPlots = [...orderedPlots]
    .filter((plot) => plot.filename !== featuredPlot?.filename)
    .sort((a, b) => b.updated_at - a.updated_at)
    .slice(0, 4);

  return (
    <section className="rounded border border-rose-500/30 bg-rose-500/5 p-3">
      <header className="mb-2 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-rose-100">{t("execution.live.title")}</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">{t("execution.live.focus")}</p>
        </div>
        <Link
          href={`/runs/${runId}/multi`}
          className="text-[11px] text-mars-accent hover:underline"
        >
          {t("execution.live.gotoMulti")}
        </Link>
      </header>
      {featuredPlot ? (
        <LivePlotCard plot={featuredPlot} />
      ) : featuredCurve ? (
        <MiniCurve curve={featuredCurve} featured />
      ) : (
        <p className="text-[11px] text-slate-500">{t("execution.live.empty")}</p>
      )}
      {foldedCount > 0 ? (
        <div className="mt-3 rounded border border-mars-border bg-mars-bg/40 px-3 py-2">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[11px] text-slate-400">
              {t("execution.live.folded")} · {foldedCount}
            </span>
            <span className="text-[10px] text-slate-500">{t("execution.live.foldedHint")}</span>
          </div>
          {recentPlots.length > 0 ? (
            <div className="mt-2 grid grid-cols-1 gap-1.5 md:grid-cols-2">
              {recentPlots.map((plot) => (
                <div
                  key={plot.filename}
                  className="flex min-w-0 items-center justify-between gap-2 rounded bg-mars-panel/70 px-2 py-1"
                >
                  <span className="truncate font-mono text-[10px] text-slate-300">{plot.experiment_id}</span>
                  <span className="shrink-0 text-[10px] text-slate-500">
                    {t("execution.live.updated")} {new Date(plot.updated_at * 1000).toLocaleTimeString()}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function LivePlotCard({
  plot,
  compact = false,
}: {
  plot: ExecutionPlot;
  compact?: boolean;
}): JSX.Element {
  return (
    <figure className="overflow-hidden rounded border border-mars-border bg-mars-bg/60">
      <div className="flex items-center justify-between border-b border-mars-border px-2 py-1">
        <figcaption className="truncate text-[10px] text-slate-300">
          {plot.experiment_id}
        </figcaption>
        <span className="font-mono text-[9px] text-slate-500">
          {plot.metric} · {new Date(plot.updated_at * 1000).toLocaleTimeString()}
        </span>
      </div>
      <img
        src={executionPlotUrl(plot)}
        alt={`${plot.experiment_id} live ${plot.metric} plot`}
        className={`w-full bg-white object-contain ${compact ? "max-h-56" : "max-h-72"}`}
      />
    </figure>
  );
}

function MiniCurve({ curve, featured = false }: { curve: Curve; featured?: boolean }): JSX.Element {
  const max = Math.max(...curve.values, 0.0001);
  const min = Math.min(...curve.values, 0);
  const range = max - min || 1;
  const w = 200;
  const h = featured ? 110 : 70;
  const path = curve.values
    .map((v, i) => {
      const x = (i / Math.max(1, curve.values.length - 1)) * w;
      const y = h - ((v - min) / range) * h;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <div className={`rounded bg-mars-bg/60 p-2 ${featured ? "border border-mars-border" : ""}`}>
      <div className="flex items-center justify-between">
        <span className="truncate text-[10px] text-slate-300">{curve.experiment_id}</span>
        <span className="text-[9px] text-slate-500">{curve.metric}</span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className={`mt-1 w-full ${featured ? "h-36" : "h-16"}`}>
        <path d={path} fill="none" stroke="#f43f5e" strokeWidth={1.4} />
      </svg>
      <div className="flex justify-between text-[9px] text-slate-500">
        <span>{min.toFixed(3)}</span>
        <span>n={curve.values.length}</span>
        <span>{max.toFixed(3)}</span>
      </div>
    </div>
  );
}

// useSearchParams() (read in RunDetailPageInner for ?agent= deep-linking) must
// sit under a Suspense boundary in the Next.js 15 App Router.
export default function RunDetailPage(props: {
  params: Promise<{ id: string }>;
}): JSX.Element {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-slate-400">加载中…</div>}>
      <RunDetailPageInner {...props} />
    </Suspense>
  );
}
