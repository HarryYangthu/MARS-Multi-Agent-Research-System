"use client";

/* eslint-disable react/jsx-no-undef */

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AgentContextPanel } from "@/components/AgentContextPanel";
import { CodingWorkspacePanel } from "@/components/CodingWorkspacePanel";
import { ReportsPanel } from "@/components/ReportsPanel";
import { SidebarToggleButton } from "@/components/SidebarToggleButton";
import { TimelinePanel } from "@/components/TimelinePanel";
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
  getAgentContext,
  getContextManifest,
  getContextRun,
  getCodingWorkspace,
  getCodingWorkspaceFile,
  getCommanderAttributionEval,
  getCommanderObservability,
  getDebateTranscript,
  getEvaluationScorecard,
  getPostTrainingExport,
  getPatch,
  getRun,
  getRunObservability,
  getRunWorkLog,
  getWorkspaceFile,
  getWorkspaceTree,
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
  retryAgent,
  startFeedbackLoop,
  supersedeMemoryCandidate,
  type AgentCodeRepository,
  type AgentContextFile,
  type AgentContextView,
  type AgentResearchSite,
  type ArtifactEvaluationReport,
  type ArtifactEvaluationSummary,
  type ArtifactView,
  type CodeFileContent,
  type CodeTreeItem,
  type CodingWorkspace,
  type CommanderAttributionEvalView,
  type CommanderObservabilityView,
  type ContextManifestSummary,
  type ContextManifestV2,
  type ContextRunView,
  type ContextSegment,
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
  type WorkLogItem,
  type WorkLogView,
  type WorkspaceFileView,
  type WorkspaceTreeView,
  updateAgentCodeRepositories,
  updateAgentContextItem,
  updateAgentResearchSites,
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

function runIdFromPathname(pathname: string): string {
  const [, rest = ""] = pathname.split("/runs/");
  return decodeURIComponent(rest.split("/")[0] ?? "");
}

function normalizeAgentParam(agentParam: string | null): string {
  return agentParam && (AGENT_NAV as readonly string[]).includes(agentParam)
    ? agentParam
    : "commander";
}

function RunDetailPageInner({ initialRunId }: { initialRunId: string }): JSX.Element {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const runId = useMemo(
    () => runIdFromPathname(pathname ?? "") || initialRunId,
    [initialRunId, pathname],
  );
  const { t } = useI18n();
  // Deep-link support: /runs/<id>?agent=<stage> opens straight to that agent.
  // Clicking an agent card on the dashboard now lands on THAT agent, not always
  // the Commander; an explicit ?agent=commander opens the Commander.
  const initialAgent = useMemo(
    () => normalizeAgentParam(searchParams?.get("agent") ?? null),
    [searchParams],
  );
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<WSMessage[]>([]);
  const [activeAgent, setActiveAgent] = useState<string>(initialAgent);
  const [artifact, setArtifact] = useState<ArtifactView | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [debate, setDebate] = useState<DebateTranscript | null>(null);
  const [debateOpen, setDebateOpen] = useState(false);
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFileView[]>([]);
  const [workspaceTree, setWorkspaceTree] = useState<WorkspaceTreeView | null>(null);
  const [patch, setPatch] = useState<PatchView | null>(null);
  const [trace, setTrace] = useState<TraceManifest | null>(null);
  const [viewMode, setViewMode] = useState<
    "artifact" | "context" | "workspace" | "timeline" | "reports"
  >("artifact");
  const [primarySidebarCollapsed, setPrimarySidebarCollapsed] = useState(false);
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
  const [revisionDialogOpen, setRevisionDialogOpen] = useState(false);
  const [revisionReason, setRevisionReason] = useState("");
  const [revisionSaveFirst, setRevisionSaveFirst] = useState(true);
  const [revisionSubmitting, setRevisionSubmitting] = useState(false);

  useEffect(() => {
    setActiveAgent(initialAgent);
  }, [initialAgent, runId]);

  useEffect(() => {
    setRun(null);
    setEvents([]);
    setArtifact(null);
    setEditing(null);
    setDebate(null);
    setWorkspaceFiles([]);
    setWorkspaceTree(null);
    setPatch(null);
    setTrace(null);
    setArtifactEvaluations([]);
    setScorecard(null);
    setPostTrainingExport(null);
    setLiveEvaluationSummaries({});
    setDiagnoses([]);
    setFeedbackPackets([]);
    setMemoryCandidates(null);
    setEpisodeMemory(null);
    setSelfEvolutionLevers(null);
    setSelfEvolutionMutations(null);
    setFeedbackAction(null);
    setCommanderObservability(null);
    setCommanderEval(null);
    setRunObservability(null);
    setToolCalls([]);
    setToolApprovals([]);
    setToolAdapters([]);
    setToolActionMessage("");
  }, [runId]);

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
    void getCommanderAttributionEval(run?.project ?? "pimc")
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
  const artifactPatchVersion = patchVersionFromArtifact(artifact);

  useEffect(() => {
    if (artifactAgentDir !== "coding" || !artifactVersion) {
      setPatch(null);
      return;
    }
    const patchVersion = artifactPatchVersion || artifactVersion;
    let alive = true;
    void getPatch(runId, patchVersion)
      .then((next) => {
        if (alive) setPatch(next);
      })
      .catch(() => {
        if (alive) setPatch(null);
      });
    return () => {
      alive = false;
    };
  }, [artifactAgentDir, artifactPatchVersion, artifactVersion, runId]);

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

  useEffect(() => {
    let alive = true;
    let interval: ReturnType<typeof setInterval> | null = null;
    setWorkspaceFiles([]);
    if (!artifact) {
      return () => {
        alive = false;
        if (interval) clearInterval(interval);
      };
    }
    const paths = workspaceFilePathsForArtifact(artifact);
    if (paths.length === 0) {
      return () => {
        alive = false;
        if (interval) clearInterval(interval);
      };
    }
    const refreshWorkspaceFiles = (): void => {
      void Promise.all(
        paths.map((path) =>
          getWorkspaceFile(runId, artifact.agent_dir, path).catch(() => null),
        ),
      ).then((files) => {
        if (!alive) return;
        setWorkspaceFiles(files.filter((file): file is WorkspaceFileView => file !== null));
      });
    };
    refreshWorkspaceFiles();
    interval = setInterval(refreshWorkspaceFiles, 5000);
    return () => {
      alive = false;
      if (interval) clearInterval(interval);
    };
  }, [artifact, runId]);

  useEffect(() => {
    let alive = true;
    let interval: ReturnType<typeof setInterval> | null = null;
    setWorkspaceTree(null);
    if (activeAgent === "commander") {
      return () => {
        alive = false;
        if (interval) clearInterval(interval);
      };
    }
    const refreshWorkspaceTree = (): void => {
      void getWorkspaceTree(runId, activeAgent)
        .then((tree) => {
          if (alive) setWorkspaceTree(tree);
        })
        .catch(() => {
          if (alive) setWorkspaceTree(null);
        });
    };
    refreshWorkspaceTree();
    interval = setInterval(refreshWorkspaceTree, 5000);
    return () => {
      alive = false;
      if (interval) clearInterval(interval);
    };
  }, [activeAgent, runId]);

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
    if (!artifact) {
      setToolActionMessage(reviewActionHint);
      return;
    }
    if (run?.states[artifact.agent_dir] !== "waiting_review") {
      setToolActionMessage(reviewActionHint);
      return;
    }
    try {
      const next =
        artifact.agent_dir === "coding" && patch
          ? await approvePatch(runId, artifact.version)
          : await approveArtifact(runId, artifact.agent_dir, artifact.stem, artifact.version);
      setArtifact(next);
      setToolActionMessage(`${agentLabel(artifact.agent_dir)} 已批准，正在推进执行流。`);
      const nextRun = await getRun(runId);
      setRun(nextRun);
    } catch (error) {
      setToolActionMessage(error instanceof Error ? error.message : "批准失败");
    }
  }

  function openRevisionDialog(): void {
    setRevisionReason("");
    setRevisionSaveFirst(Boolean(editing && artifact && editing !== splitFrontmatter(artifact.text).body));
    setRevisionDialogOpen(true);
  }

  async function submitRevisionRequest(): Promise<void> {
    if (!artifact) return;
    try {
      setRevisionSubmitting(true);
      let target = artifact;
      if (revisionSaveFirst) {
        const saved = await saveArtifactEdits();
        if (saved) {
          target = saved;
        }
      }
      const reason = revisionReason.trim() || "请基于人工审阅意见返工，并生成一个新的可审核版本。";
      if (target.agent_dir === "coding" && patch) {
        await rejectPatch(runId, target.version, reason);
        setToolActionMessage("已驳回 patch，等待 Coding Agent 返工。");
        setRevisionDialogOpen(false);
        return;
      }
      const result = await rejectArtifact(
        runId,
        target.agent_dir,
        target.stem,
        reason,
      );
      setToolActionMessage(
        result.status === "revision_requested" || result.status === "revision_started"
          ? "已请求返工，Agent 会基于当前意见生成新版本。"
          : `驳回状态：${result.status}`,
      );
      setRevisionDialogOpen(false);
      void getRun(runId).then((nextRun) => setRun(nextRun)).catch(() => undefined);
    } catch (error) {
      setToolActionMessage(error instanceof Error ? error.message : "驳回失败");
    } finally {
      setRevisionSubmitting(false);
    }
  }

  async function saveArtifactEdits(): Promise<ArtifactView | null> {
    if (!artifact || editing === null) return null;
    // Accept both body-only editing (default UI) and full markdown documents.
    const m = /^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$/.exec(editing);
    const body = m ? m[2] : editing;
    const next = await editArtifact(runId, artifact.agent_dir, artifact.stem, artifact.version, {
      body,
      // metadata_patch left empty: schema metadata is preserved from the base version.
    });
    setArtifact(next);
    setEditing(splitFrontmatter(next.text).body);
    return next;
  }

  async function save(): Promise<void> {
    await saveArtifactEdits();
  }

  async function retryActiveAgent(): Promise<void> {
    try {
      const result = await retryAgent(
        runId,
        activeAgent,
        `人工从工作台请求重试 ${agentLabel(activeAgent)}。`,
      );
      setToolActionMessage(`${agentLabel(activeAgent)} 已重新启动：${result.status}`);
      const nextRun = await getRun(runId);
      setRun(nextRun);
    } catch (error) {
      setToolActionMessage(error instanceof Error ? error.message : "重试失败");
    }
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
  const isTimelineView = viewMode === "timeline";
  const isReportsView = viewMode === "reports";
  const activeAgentState = run?.states[activeAgent] ?? "pending";
  const firstWaitingReviewAgent =
    PIPELINE_STAGES.find((stage) => run?.states[stage] === "waiting_review") ?? null;
  const activeAgentIsWaitingReview = activeAgentState === "waiting_review";
  const reviewTargetAgent = activeAgentIsWaitingReview ? activeAgent : firstWaitingReviewAgent;
  const canReviewCurrentAgent = Boolean(artifact && activeAgentIsWaitingReview);
  const reviewActionHint = canReviewCurrentAgent
    ? "当前产物可以审核。"
    : reviewTargetAgent && reviewTargetAgent !== activeAgent
      ? `当前 ${agentLabel(activeAgent)} 还不能审核，${agentLabel(reviewTargetAgent)} 正在等待批准。`
      : activeAgentIsWaitingReview
        ? `当前 ${agentLabel(activeAgent)} 等待审核，但还没有加载到可批准产物。`
        : `当前 ${agentLabel(activeAgent)} 状态为 ${activeAgentState}，没有可批准产物。`;
  const activeArtifactEvaluation = artifact
    ? liveEvaluationSummaries[relativeArtifactRef(artifact)]
      ?? buildSummaryFromReports(artifact, artifactEvaluations)
    : null;
  const isAgentArtifactView =
    STAGE_TO_STEM[activeAgent] &&
    !isContextView &&
    !isWorkspaceView &&
    !isTimelineView &&
    !isReportsView;
  const hasScorecardSignal = scorecard ? scorecardHasSignal(scorecard) : false;

  return (
    <main className={`grid h-screen gap-0 ${primarySidebarCollapsed ? "grid-cols-[1fr]" : "grid-cols-[260px,1fr]"}`}>
      {!primarySidebarCollapsed ? (
      <aside className="border-r border-mars-border bg-mars-panel/60 p-4">
        <div className="flex items-center justify-between gap-2">
          <Link href="/" className="text-xs text-slate-500 hover:text-slate-300">
            &larr; 实验台
          </Link>
          <div className="flex items-center gap-2">
            <Link
              href={`/runs/${runId}/multi`}
              className="text-xs text-mars-accent hover:underline"
            >
              多实验视图 &rarr;
            </Link>
            <SidebarToggleButton
              collapsed={primarySidebarCollapsed}
              side="left"
              label="流水线边栏"
              onToggle={() => setPrimarySidebarCollapsed((current) => !current)}
            />
          </div>
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
      ) : null}

      <section className="flex flex-col">
        {(() => {
          const isWaiting =
            activeAgentIsWaitingReview &&
            !isContextView &&
            !isWorkspaceView &&
            !isTimelineView &&
            !isReportsView;
          const hasReviewElsewhere =
            Boolean(reviewTargetAgent && reviewTargetAgent !== activeAgent) &&
            !isContextView &&
            !isWorkspaceView &&
            !isTimelineView &&
            !isReportsView;
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
                        onClick={openRevisionDialog}
                        disabled={!canReviewCurrentAgent}
                        title={canReviewCurrentAgent ? "驳回当前产物并输入返工意见" : reviewActionHint}
                        className="rounded border border-red-500/50 bg-red-500/15 px-3 py-1.5 text-sm font-medium text-red-100 hover:bg-red-500/25 disabled:opacity-50"
                      >
                        ✗ {t("run.reject")}
                      </button>
                      <button
                        onClick={approve}
                        disabled={!canReviewCurrentAgent}
                        title={canReviewCurrentAgent ? "批准当前产物并推进下游 Agent" : reviewActionHint}
                        className="rounded bg-emerald-500/80 px-4 py-1.5 text-sm font-bold text-white shadow hover:bg-emerald-500 disabled:opacity-50"
                      >
                        ✓ {t("run.approve")}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}
              {hasReviewElsewhere && reviewTargetAgent ? (
                <div className="border-b border-cyan-500/30 bg-cyan-500/10 px-4 py-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs text-cyan-100">{reviewActionHint}</p>
                    <button
                      onClick={() => selectAgent(reviewTargetAgent)}
                      className="rounded border border-cyan-500/40 px-2.5 py-1 text-xs font-medium text-cyan-100 hover:bg-cyan-500/15"
                    >
                      去 {agentLabel(reviewTargetAgent)} 审核
                    </button>
                  </div>
                </div>
              ) : null}
              <header className="flex flex-wrap items-start justify-between gap-3 border-b border-mars-border p-4">
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-3">
                    {primarySidebarCollapsed ? (
                      <SidebarToggleButton
                        collapsed={primarySidebarCollapsed}
                        side="left"
                        label="流水线边栏"
                        onToggle={() => setPrimarySidebarCollapsed((current) => !current)}
                      />
                    ) : null}
                    <h1 className="min-w-0 break-words text-xl font-semibold leading-tight">
                      {agentLabel(activeAgent)}
                    </h1>
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
                      {STAGE_TO_STEM[activeAgent] ? (
                        <>
                          <button
                            onClick={() => setViewMode("artifact")}
                            className={`rounded px-2.5 py-1 ${
                              viewMode === "artifact" ? "bg-mars-accent text-white" : "text-slate-400 hover:text-slate-200"
                            }`}
                          >
                            工作区
                          </button>
                      </>
                    ) : null}
                      <button
                        onClick={() => setViewMode("timeline")}
                        className={`rounded px-2.5 py-1 ${
                          viewMode === "timeline" ? "bg-mars-accent text-white" : "text-slate-400 hover:text-slate-200"
                        }`}
                      >
                        执行流
                      </button>
                      <button
                        onClick={() => setViewMode("reports")}
                        className={`rounded px-2.5 py-1 ${
                          viewMode === "reports" ? "bg-mars-accent text-white" : "text-slate-400 hover:text-slate-200"
                        }`}
                      >
                        报告
                      </button>
                    </div>
                  </div>
                  <p className="mt-1 max-w-full truncate text-xs text-slate-500">
                    {isWorkspaceView
                      ? "编码工作台"
                      : isContextView
                        ? "Agent 级上下文、上传材料和研究来源"
                        : isTimelineView
                          ? "可审计执行流、工具证据、HITL 与 LangGraph 事件"
                          : isReportsView
                            ? "报告 manifest、Office 产物和 QA 状态"
                            : artifact
                              ? `工作区 · ${artifact.path}`
                              : "尚无产物"}
                  </p>
                </div>
                <div className={`shrink-0 gap-2 ${isContextView || isWorkspaceView || isTimelineView || isReportsView ? "hidden" : "flex"}`}>
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
                        onClick={openRevisionDialog}
                        disabled={!canReviewCurrentAgent}
                        title={canReviewCurrentAgent ? "驳回当前产物并输入返工意见" : reviewActionHint}
                        className="rounded border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-sm text-red-200 hover:bg-red-500/20 disabled:opacity-50"
                      >
                        {t("run.reject")}
                      </button>
                      <button
                        onClick={approve}
                        disabled={!canReviewCurrentAgent}
                        title={canReviewCurrentAgent ? "批准当前产物并推进下游 Agent" : reviewActionHint}
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
          {toolActionMessage ? (
            <div className="rounded border border-cyan-500/25 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-100">
              {toolActionMessage}
            </div>
          ) : null}
          {isAgentArtifactView ? (
            <AgentWorkbench
              run={run}
              agent={activeAgent}
              artifact={artifact}
              patch={patch}
              editing={editing}
              evaluation={activeArtifactEvaluation}
              debate={debate}
              workspaceFiles={workspaceFiles}
              workspaceTree={workspaceTree}
              trace={trace}
              toolCalls={toolCalls}
              events={events}
              canReviewCurrentAgent={canReviewCurrentAgent}
              reviewActionHint={reviewActionHint}
              reviewTargetAgent={reviewTargetAgent}
              onEdit={setEditing}
              onSave={save}
              onApprove={approve}
              onReject={() => {
                openRevisionDialog();
                return Promise.resolve();
              }}
              onGoToReviewAgent={selectAgent}
              onRetryAgent={retryActiveAgent}
              onOpenTimeline={() => setViewMode("timeline")}
              onOpenCommander={() => selectAgent("commander")}
            />
          ) : isTimelineView ? (
            <TimelinePanel runId={runId} />
          ) : isReportsView ? (
            <ReportsPanel runId={runId} />
          ) : activeAgent === "commander" ? (
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
          ) : isWorkspaceView && activeAgent === "coding" ? (
            <CodingWorkspacePanel runId={runId} project={run?.project ?? "pimc"} />
          ) : isContextView ? (
            <AgentContextPanel agent={activeAgent} project={run?.project ?? "pimc"} />
          ) : (
            <>
              {activeAgent === "execution" ? (
                <ExecutionLivePanel runId={runId} />
              ) : null}
              {artifact ? (
                <>
                  <CodingChangeSummary artifact={artifact} patch={patch} onOpenPath={() => undefined} />
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
          {!isAgentArtifactView && !isTimelineView && !isReportsView ? (
            <AgentReActTracePanel
              trace={trace}
              toolCalls={toolCalls}
              activeAgent={activeAgent}
              agentState={run?.states[activeAgent] ?? "pending"}
              events={events}
            />
          ) : null}
          {scorecard && hasScorecardSignal ? (
            <RunEvaluationScorecard
              scorecard={scorecard}
              postTrainingExport={postTrainingExport}
              exportMessage={postTrainingExportMessage}
              onExportPostTraining={exportPostTrainingData}
            />
          ) : null}
          {revisionDialogOpen && artifact ? (
            <RevisionRequestDialog
              artifact={artifact}
              reason={revisionReason}
              saveFirst={revisionSaveFirst}
              submitting={revisionSubmitting}
              onReasonChange={setRevisionReason}
              onSaveFirstChange={setRevisionSaveFirst}
              onCancel={() => setRevisionDialogOpen(false)}
              onSubmit={() => void submitRevisionRequest()}
            />
          ) : null}
        </div>
      </section>
    </main>
  );
}

function RevisionRequestDialog({
  artifact,
  reason,
  saveFirst,
  submitting,
  onReasonChange,
  onSaveFirstChange,
  onCancel,
  onSubmit,
}: {
  artifact: ArtifactView;
  reason: string;
  saveFirst: boolean;
  submitting: boolean;
  onReasonChange: (value: string) => void;
  onSaveFirstChange: (value: boolean) => void;
  onCancel: () => void;
  onSubmit: () => void;
}): JSX.Element {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 px-4">
      <section className="w-full max-w-2xl overflow-hidden rounded border border-mars-border bg-mars-bg shadow-2xl">
        <div className="border-b border-mars-border px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-100">驳回返工意见</h3>
          <p className="mt-1 truncate font-mono text-[11px] text-slate-500">
            {artifact.agent_dir}/{artifact.stem}.{artifact.version}.md
          </p>
        </div>
        <div className="space-y-3 p-4">
          <textarea
            value={reason}
            onChange={(event) => onReasonChange(event.target.value)}
            placeholder="写清楚需要 Agent 怎么改，例如：补充论文证据、解释低相关命中、修改假设边界、重新给出实验 hint..."
            className="min-h-[180px] w-full resize-y rounded border border-mars-border bg-mars-panel/40 p-3 text-sm leading-relaxed text-slate-100 outline-none placeholder:text-slate-600"
          />
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={saveFirst}
              onChange={(event) => onSaveFirstChange(event.target.checked)}
              className="h-4 w-4 accent-mars-accent"
            />
            先保存当前编辑内容，再让 Agent 基于它继续返工
          </label>
        </div>
        <div className="flex justify-end gap-2 border-t border-mars-border px-4 py-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded border border-mars-border px-3 py-1.5 text-sm text-slate-200 hover:bg-mars-panel disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={onSubmit}
            disabled={submitting}
            className="rounded bg-red-500/85 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
          >
            {submitting ? "提交中…" : "提交返工"}
          </button>
        </div>
      </section>
    </div>
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
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  return (
    <section className="flex min-h-[300px] flex-1 flex-col overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="flex items-center justify-between border-b border-mars-border px-3 py-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">{t("artifact.body")}</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">{t("artifact.editorHint")}</p>
        </div>
        <div className="flex rounded border border-mars-border bg-mars-panel/50 p-0.5 text-[11px]">
          <button
            type="button"
            onClick={() => setMode("preview")}
            className={`rounded px-2 py-1 ${
              mode === "preview" ? "bg-mars-accent text-white" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            阅读
          </button>
          <button
            type="button"
            onClick={() => setMode("edit")}
            className={`rounded px-2 py-1 ${
              mode === "edit" ? "bg-mars-accent text-white" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            编辑
          </button>
        </div>
      </div>
      {mode === "edit" ? (
        <textarea
          value={text}
          onChange={(e) => onChange(e.target.value)}
          className="min-h-[260px] w-full flex-1 resize-none bg-transparent p-4 font-mono text-sm leading-relaxed text-slate-100 outline-none"
        />
      ) : (
        <div className="min-h-[260px] flex-1 overflow-auto px-5 py-4 text-sm leading-relaxed text-slate-200">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h1: ({ children }) => (
                <h1 className="mb-3 text-xl font-semibold text-slate-50">{children}</h1>
              ),
              h2: ({ children }) => (
                <h2 className="mb-2 mt-5 border-b border-mars-border pb-1 text-base font-semibold text-slate-100">
                  {children}
                </h2>
              ),
              h3: ({ children }) => (
                <h3 className="mb-1.5 mt-4 text-sm font-semibold text-slate-100">
                  {children}
                </h3>
              ),
              p: ({ children }) => (
                <p className="mb-3 max-w-5xl text-slate-300">{children}</p>
              ),
              ul: ({ children }) => (
                <ul className="mb-3 list-disc space-y-1 pl-5 text-slate-300">{children}</ul>
              ),
              ol: ({ children }) => (
                <ol className="mb-3 list-decimal space-y-1 pl-5 text-slate-300">{children}</ol>
              ),
              li: ({ children }) => <li className="pl-1">{children}</li>,
              blockquote: ({ children }) => (
                <blockquote className="mb-3 border-l-2 border-cyan-400/60 pl-3 text-slate-300">
                  {children}
                </blockquote>
              ),
              code: ({ children }) => (
                <code className="rounded bg-slate-900/80 px-1.5 py-0.5 font-mono text-[0.9em] text-cyan-100">
                  {children}
                </code>
              ),
              pre: ({ children }) => (
                <pre className="mb-3 overflow-auto rounded border border-mars-border bg-slate-950/70 p-3 text-xs leading-relaxed text-slate-100">
                  {children}
                </pre>
              ),
              a: ({ children, href }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  className="text-cyan-300 underline decoration-cyan-300/40 underline-offset-2 hover:text-cyan-200"
                >
                  {children}
                </a>
              ),
              table: ({ children }) => (
                <div className="mb-3 overflow-auto">
                  <table className="min-w-full border-collapse text-xs">{children}</table>
                </div>
              ),
              th: ({ children }) => (
                <th className="border border-mars-border bg-mars-panel px-2 py-1 text-left text-slate-200">
                  {children}
                </th>
              ),
              td: ({ children }) => (
                <td className="border border-mars-border px-2 py-1 text-slate-300">
                  {children}
                </td>
              ),
            }}
          >
            {text || "暂无正文"}
          </ReactMarkdown>
        </div>
      )}
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

const AGENT_WORK_COPY: Record<
  string,
  { title: string; purpose: string; artifact: string; handoff: string }
> = {
  idea: {
    title: "研究假设生成",
    purpose: "把研究问题收束成可审核的 proposal.v1，并给 Experiment Agent 一个清晰入口。",
    artifact: "proposal.v1",
    handoff: "批准后进入实验方案设计",
  },
  experiment: {
    title: "实验方案设计",
    purpose: "把 proposal 转成可执行的 experiment_plan.v1，明确变量、指标和门槛。",
    artifact: "experiment_plan.v1",
    handoff: "批准后进入代码规格",
  },
  coding: {
    title: "代码规格与补丁",
    purpose: "把实验方案转成 code_spec.v1，并准备可审计的代码修改。",
    artifact: "code_spec.v1",
    handoff: "批准后进入执行仿真",
  },
  execution: {
    title: "仿真执行",
    purpose: "运行实验批次，沉淀 run_log.v1、曲线、图表和失败原因。",
    artifact: "run_log.v1",
    handoff: "批准后进入论文写作",
  },
  writing: {
    title: "报告写作",
    purpose: "把实验结果和证据整合成 report.v1，供最终审阅和导出。",
    artifact: "report.v1",
    handoff: "批准后进入完整沉淀",
  },
};

function AgentWorkbench({
  run,
  agent,
  artifact,
  patch,
  editing,
  evaluation,
  debate,
  workspaceFiles,
  workspaceTree,
  trace,
  toolCalls,
  events,
  canReviewCurrentAgent,
  reviewActionHint,
  reviewTargetAgent,
  onEdit,
  onSave,
  onApprove,
  onReject,
  onGoToReviewAgent,
  onRetryAgent,
  onOpenTimeline,
  onOpenCommander,
}: {
  run: RunDetail | null;
  agent: string;
  artifact: ArtifactView | null;
  patch: PatchView | null;
  editing: string | null;
  evaluation: ArtifactEvaluationSummary | null;
  debate: DebateTranscript | null;
  workspaceFiles: WorkspaceFileView[];
  workspaceTree: WorkspaceTreeView | null;
  trace: TraceManifest | null;
  toolCalls: ToolAuditEntry[];
  events: WSMessage[];
  canReviewCurrentAgent: boolean;
  reviewActionHint: string;
  reviewTargetAgent: string | null;
  onEdit: (value: string) => void;
  onSave: () => Promise<void>;
  onApprove: () => Promise<void>;
  onReject: () => Promise<void>;
  onGoToReviewAgent: (agent: string) => void;
  onRetryAgent: () => Promise<void>;
  onOpenTimeline: () => void;
  onOpenCommander: () => void;
}): JSX.Element {
  const state = run?.states[agent] ?? "pending";
  const copy = AGENT_WORK_COPY[agent] ?? {
    title: "Agent 工作区",
    purpose: "查看当前 Agent 的产物、上下文、评估和运行证据。",
    artifact: "artifact.v1",
    handoff: "批准后进入下一步",
  };
  const artifactFileName = artifact ? `${artifact.stem}.${artifact.version}.md` : "";
  const artifactShouldBePrimary = state === "waiting_review" && Boolean(artifactFileName);
  const defaultPath =
    (artifactShouldBePrimary ? artifactFileName : "") ||
    WORKBENCH_WORKLOG_PATH ||
    artifactFileName ||
    workspaceTree?.entries.find((entry) => entry.kind === "file")?.relative_path ||
    "";
  const [selectedPath, setSelectedPath] = useState(defaultPath);
  const [selectedFile, setSelectedFile] = useState<WorkspaceFileView | null>(null);
  const [agentContext, setAgentContext] = useState<AgentContextView | null>(null);
  const [contextRun, setContextRun] = useState<ContextRunView | null>(null);
  const [selectedManifest, setSelectedManifest] = useState<ContextManifestV2 | null>(null);
  const [workLog, setWorkLog] = useState<WorkLogView | null>(null);
  const [explorerSidebarCollapsed, setExplorerSidebarCollapsed] = useState(false);
  const [processSidebarCollapsed, setProcessSidebarCollapsed] = useState(false);
  const autoOpenedReviewArtifactRef = useRef("");
  const spans = trace?.spans.filter((span) => spanBelongsToAgent(span, agent)) ?? [];
  const tools = buildReactToolSteps(toolCalls, agent);
  const agentEvents = events.filter((event) => metaText(asRecord(event.payload), "agent") === agent);
  const activityRows = buildWorkspaceActivityRows({
    agent,
    artifact,
    debate,
    workspaceFiles,
    spans,
    tools,
    events: agentEvents,
  });
  const literature = literatureSummariesFromWorkspace(workspaceFiles);
  const warnings = workspaceQualityWarnings(artifact, literature);
  const debateRows = debateHighlights(artifact, debate);
  const selectedContextFile = findContextFileForWorkbenchPath(agentContext, selectedPath);
  const selectedManifestId = contextManifestIdFromWorkbenchPath(selectedPath);
  const isWorkLogPane = selectedPath === WORKBENCH_WORKLOG_PATH;
  const isResearchSitesPane = selectedPath === WORKBENCH_RESEARCH_SITES_PATH;
  const isCodeRepositoriesPane = selectedPath === WORKBENCH_CODE_REPOSITORIES_PATH;
  const isReceivedContextPane = selectedPath === WORKBENCH_AGENT_RECEIVED_OVERVIEW_PATH;
  const isProducedContentPane = selectedPath === WORKBENCH_AGENT_PRODUCED_PATH;
  const isArtifactPane = Boolean(artifact && selectedPath === artifactFileName);
  const project = run?.project ?? "pimc";
  const displayPath = selectedContextFile
    ? selectedContextFile.path
    : isWorkLogPane
      ? "工作内容"
    : isResearchSitesPane
      ? "网址源"
      : isCodeRepositoriesPane
        ? "代码仓"
      : isReceivedContextPane
        ? "接收的上下文"
      : isProducedContentPane
        ? "本 Agent 产出"
      : selectedManifestId
        ? selectedManifestId
    : selectedPath || artifactFileName || copy.artifact;
  const workbenchGridClass =
    explorerSidebarCollapsed && processSidebarCollapsed
      ? "xl:grid-cols-[minmax(0,1fr)]"
      : explorerSidebarCollapsed
        ? "xl:grid-cols-[minmax(0,1fr)_360px]"
        : processSidebarCollapsed
          ? "xl:grid-cols-[260px_minmax(0,1fr)]"
          : "xl:grid-cols-[260px_minmax(0,1fr)_360px]";

  useEffect(() => {
    if (!selectedPath && defaultPath) {
      setSelectedPath(defaultPath);
    }
  }, [defaultPath, selectedPath]);

  useEffect(() => {
    const key = `${agent}:${artifactFileName}:${state}`;
    if (!artifactShouldBePrimary || !artifactFileName || autoOpenedReviewArtifactRef.current === key) {
      return;
    }
    autoOpenedReviewArtifactRef.current = key;
    setSelectedPath(artifactFileName);
  }, [agent, artifactFileName, artifactShouldBePrimary, state]);

  useEffect(() => {
    let alive = true;
    setSelectedFile(null);
    if (!workspaceTree || !selectedPath || isContextWorkbenchPath(selectedPath)) {
      return () => {
        alive = false;
      };
    }
    void getWorkspaceFile(workspaceTree.run_id, workspaceTree.agent_dir, selectedPath)
      .then((file) => {
        if (alive) setSelectedFile(file);
      })
      .catch(() => {
        if (alive) setSelectedFile(null);
      });
    return () => {
      alive = false;
    };
  }, [selectedPath, workspaceTree]);

  useEffect(() => {
    let alive = true;
    setAgentContext(null);
    void getAgentContext(agent, project)
      .then((next) => {
        if (alive) setAgentContext(next);
      })
      .catch(() => {
        if (alive) setAgentContext(null);
      });
    return () => {
      alive = false;
    };
  }, [agent, project]);

  useEffect(() => {
    let alive = true;
    setContextRun(null);
    if (!run?.run_id) {
      return () => {
        alive = false;
      };
    }
    void getContextRun(run.run_id)
      .then((next) => {
        if (alive) setContextRun(next);
      })
      .catch(() => {
        if (alive) setContextRun(null);
      });
    return () => {
      alive = false;
    };
  }, [run?.run_id]);

  useEffect(() => {
    let alive = true;
    setSelectedManifest(null);
    if (!run?.run_id || !selectedManifestId) {
      return () => {
        alive = false;
      };
    }
    void getContextManifest(run.run_id, selectedManifestId)
      .then((manifest) => {
        if (alive) setSelectedManifest(manifest);
      })
      .catch(() => {
        if (alive) setSelectedManifest(null);
      });
    return () => {
      alive = false;
    };
  }, [run?.run_id, selectedManifestId]);

  useEffect(() => {
    let alive = true;
    setWorkLog(null);
    if (!run?.run_id) {
      return () => {
        alive = false;
      };
    }
    const refresh = (): void => {
      void getRunWorkLog(run.run_id, agent)
        .then((next) => {
          if (alive) setWorkLog(next);
        })
        .catch(() => {
          if (alive) setWorkLog(null);
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [agent, run?.run_id]);

  async function saveContextFile(path: string, content: string): Promise<void> {
    const updated = await updateAgentContextItem(agent, { path, content });
    setAgentContext((current) =>
      current
        ? {
            ...current,
            files: current.files.map((file) => (file.path === updated.path ? updated : file)),
          }
        : current,
    );
  }

  async function saveResearchSites(sites: AgentResearchSite[]): Promise<void> {
    const saved = await updateAgentResearchSites(agent, sites);
    setAgentContext((current) => (current ? { ...current, research_sites: saved } : current));
  }

  async function saveCodeRepositories(repositories: AgentCodeRepository[]): Promise<void> {
    const saved = await updateAgentCodeRepositories(agent, project, repositories);
    setAgentContext((current) => (current ? { ...current, code_repositories: saved } : current));
  }

  return (
    <section className={`grid min-h-[720px] flex-1 overflow-hidden rounded border border-mars-border bg-mars-bg ${workbenchGridClass}`}>
      {!explorerSidebarCollapsed ? (
        <aside className="min-h-0 border-b border-mars-border bg-mars-panel/45 xl:border-b-0 xl:border-r">
          <div className="flex items-start justify-between gap-2 border-b border-mars-border px-3 py-2">
            <div className="min-w-0">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Explorer
              </h3>
              <p className="mt-1 truncate font-mono text-[10px] text-slate-500">
                {workspaceTree ? `${workspaceTree.agent_dir}/` : "workspace pending"}
              </p>
            </div>
            <SidebarToggleButton
              collapsed={explorerSidebarCollapsed}
              side="left"
              label="Explorer 边栏"
              onToggle={() => setExplorerSidebarCollapsed((current) => !current)}
            />
          </div>
          <WorkbenchExplorer
            tree={workspaceTree}
            contextFiles={agentContext?.files ?? []}
            codeRepositories={agentContext?.code_repositories ?? []}
            contextManifests={contextRun?.manifests ?? []}
            currentAgent={agent}
            selectedPath={selectedPath}
            onSelect={setSelectedPath}
          />
        </aside>
      ) : null}

      <main className="flex min-w-0 flex-col overflow-hidden">
        <div className="flex min-h-[44px] items-center justify-between gap-3 border-b border-mars-border bg-mars-panel/30 px-3 py-2">
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              {explorerSidebarCollapsed ? (
                <SidebarToggleButton
                  collapsed={explorerSidebarCollapsed}
                  side="left"
                  label="Explorer 边栏"
                  onToggle={() => setExplorerSidebarCollapsed((current) => !current)}
                />
              ) : null}
              <span className="rounded bg-mars-accent/80 px-2 py-0.5 text-[10px] font-semibold uppercase text-white">
                {agentLabel(agent)}
              </span>
              <span className="truncate font-mono text-xs text-slate-300">
                {displayPath}
              </span>
            </div>
            <p className="mt-0.5 truncate text-[11px] text-slate-500">
              {copy.title} · {copy.purpose}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {processSidebarCollapsed ? (
              <SidebarToggleButton
                collapsed={processSidebarCollapsed}
                side="right"
                label="Agent 过程边栏"
                onToggle={() => setProcessSidebarCollapsed((current) => !current)}
              />
            ) : null}
            {artifact && !isArtifactPane ? (
              <button
                type="button"
                onClick={() => setSelectedPath(artifactFileName)}
                className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2.5 py-1 text-xs font-medium text-cyan-100 hover:bg-cyan-500/15"
              >
                查看产物
              </button>
            ) : null}
            <div className={`shrink-0 gap-2 ${isArtifactPane ? "flex" : "hidden"}`}>
            <button
              type="button"
              onClick={() => void onSave()}
              disabled={!artifact}
              className="rounded border border-mars-border px-2.5 py-1 text-xs text-slate-200 hover:bg-mars-panel disabled:opacity-40"
            >
              保存
            </button>
            <button
              type="button"
              onClick={() => void onReject()}
              disabled={!canReviewCurrentAgent}
              title={canReviewCurrentAgent ? "驳回当前产物并输入返工意见" : reviewActionHint}
              className="rounded border border-red-500/40 bg-red-500/10 px-2.5 py-1 text-xs text-red-100 hover:bg-red-500/20 disabled:opacity-40"
            >
              驳回返工
            </button>
            <button
              type="button"
              onClick={() => void onApprove()}
              disabled={!canReviewCurrentAgent}
              title={canReviewCurrentAgent ? "批准当前产物并推进下游 Agent" : reviewActionHint}
              className="rounded bg-mars-accent px-2.5 py-1 text-xs font-medium text-white disabled:opacity-40"
            >
              批准
            </button>
            </div>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-3">
          {!canReviewCurrentAgent && reviewTargetAgent && reviewTargetAgent !== agent ? (
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded border border-cyan-500/25 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-100">
              <span>{reviewActionHint}</span>
              <button
                type="button"
                onClick={() => onGoToReviewAgent(reviewTargetAgent)}
                className="rounded border border-cyan-500/40 px-2 py-1 font-medium hover:bg-cyan-500/15"
              >
                去 {agentLabel(reviewTargetAgent)} 审核
              </button>
            </div>
          ) : null}
          {artifact && !isArtifactPane ? (
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded border border-fuchsia-500/25 bg-fuchsia-500/10 px-3 py-2 text-xs text-fuchsia-100">
              <span>
                当前产物是 <span className="font-mono">{artifactFileName}</span>，现在中间区域显示的是
                {isWorkLogPane ? "工作内容" : "所选文件"}。
              </span>
              <button
                type="button"
                onClick={() => setSelectedPath(artifactFileName)}
                className="rounded border border-fuchsia-500/40 px-2 py-1 font-medium hover:bg-fuchsia-500/15"
              >
                打开产物正文
              </button>
            </div>
          ) : null}
          {isWorkLogPane ? (
            <WorkbenchWorkLogPanel
              workLog={workLog}
              agent={agent}
              state={state}
              artifact={artifact}
              onOpenTimeline={onOpenTimeline}
            />
          ) : isResearchSitesPane ? (
            <WorkbenchResearchSitesPanel
              sites={agentContext?.research_sites ?? []}
              onSave={saveResearchSites}
            />
          ) : isCodeRepositoriesPane ? (
            <WorkbenchCodeRepositoriesPanel
              project={project}
              runId={run?.run_id ?? ""}
              repositories={agentContext?.code_repositories ?? []}
              onSave={saveCodeRepositories}
            />
          ) : isReceivedContextPane ? (
            <WorkbenchReceivedContextPanel
              agent={agent}
              manifests={contextRun?.manifests ?? []}
              onOpenManifest={setSelectedPath}
            />
          ) : isProducedContentPane ? (
            <WorkbenchProducedContentPanel
              artifact={artifact}
              artifactFileName={artifactFileName}
              workspaceFiles={workspaceFiles}
              workspaceTree={workspaceTree}
              onOpenPath={setSelectedPath}
            />
          ) : selectedManifestId ? (
            <WorkbenchContextManifestPreview
              manifest={selectedManifest}
              manifestId={selectedManifestId}
              artifact={artifact}
              workspaceFiles={workspaceFiles}
            />
          ) : selectedContextFile ? (
            <WorkbenchContextFilePreview file={selectedContextFile} onSave={saveContextFile} />
          ) : artifact && selectedPath === artifactFileName ? (
            <div className="space-y-3">
              <CodingChangeSummary artifact={artifact} patch={patch} onOpenPath={setSelectedPath} />
              <ValidationBadge view={artifact} />
              <ArtifactEvaluationPanel summary={evaluation} reports={[]} />
              {patch ? <PatchPanel patch={patch} /> : null}
              <ArtifactBodyEditor
                text={editing ?? splitFrontmatter(artifact.text).body}
                onChange={onEdit}
                frontmatter={splitFrontmatter(artifact.text).frontmatter}
              />
            </div>
          ) : selectedFile?.exists ? (
            patch && selectedFile.relative_path.endsWith(".diff") ? (
              <PatchPanel patch={patch} />
            ) : (
              <WorkbenchFilePreview file={selectedFile} />
            )
          ) : artifact ? (
            <div className="space-y-3">
              <CodingChangeSummary artifact={artifact} patch={patch} onOpenPath={setSelectedPath} />
              <ValidationBadge view={artifact} />
              {patch ? <PatchPanel patch={patch} /> : null}
              <ArtifactBodyEditor
                text={editing ?? splitFrontmatter(artifact.text).body}
                onChange={onEdit}
                frontmatter={splitFrontmatter(artifact.text).frontmatter}
              />
            </div>
          ) : (
            <RunningOrEmpty
              agentState={state}
              debate={debate}
              open={false}
              onToggle={() => undefined}
            />
          )}
        </div>
      </main>

      {!processSidebarCollapsed ? (
        <aside className="min-h-0 border-t border-mars-border bg-mars-panel/35 xl:border-l xl:border-t-0">
          <div className="flex justify-end border-b border-mars-border px-2 py-2 xl:border-b-0 xl:pb-0">
            <SidebarToggleButton
              collapsed={processSidebarCollapsed}
              side="right"
              label="Agent 过程边栏"
              onToggle={() => setProcessSidebarCollapsed((current) => !current)}
            />
          </div>
          <WorkbenchProcessPanel
            state={state}
            copy={copy}
            evaluation={evaluation}
            activityRows={activityRows}
            warnings={warnings}
            literature={literature}
            debateRows={debateRows}
            debate={debate}
            spans={spans}
            tools={tools}
            onOpenContext={() => {
              setSelectedPath(WORKBENCH_AGENT_RECEIVED_OVERVIEW_PATH);
            }}
            onRetryAgent={onRetryAgent}
            onOpenTimeline={onOpenTimeline}
            onOpenCommander={onOpenCommander}
          />
        </aside>
      ) : null}
    </section>
  );
}

const WORKBENCH_ROOT_PATH = "__mars_workspace_root__";
const WORKBENCH_WORKLOG_PATH = "__mars_worklog__";
const WORKBENCH_CONTEXT_ROOT_PATH = "__mars_context__";
const WORKBENCH_RESEARCH_SITES_PATH = `${WORKBENCH_CONTEXT_ROOT_PATH}/__research_sites__`;
const WORKBENCH_CODE_REPOSITORIES_PATH = `${WORKBENCH_CONTEXT_ROOT_PATH}/__code_repositories__`;
const WORKBENCH_AGENT_COMM_ROOT_PATH = `${WORKBENCH_CONTEXT_ROOT_PATH}/__agent_comm__`;
const WORKBENCH_AGENT_RECEIVED_PATH = `${WORKBENCH_AGENT_COMM_ROOT_PATH}/__received__`;
const WORKBENCH_AGENT_RECEIVED_OVERVIEW_PATH = `${WORKBENCH_AGENT_RECEIVED_PATH}/__overview__`;
const WORKBENCH_AGENT_PRODUCED_PATH = `${WORKBENCH_AGENT_COMM_ROOT_PATH}/__produced__`;
const WORKBENCH_AGENT_ALL_CALLS_PATH = `${WORKBENCH_AGENT_COMM_ROOT_PATH}/__all_calls__`;
const DEFAULT_WORKBENCH_EXPANDED_PATHS = [
  WORKBENCH_ROOT_PATH,
  WORKBENCH_CONTEXT_ROOT_PATH,
  WORKBENCH_CODE_REPOSITORIES_PATH,
  WORKBENCH_AGENT_COMM_ROOT_PATH,
  WORKBENCH_AGENT_RECEIVED_PATH,
  WORKBENCH_AGENT_ALL_CALLS_PATH,
];

function WorkbenchExplorer({
  tree,
  contextFiles,
  codeRepositories,
  contextManifests,
  currentAgent,
  selectedPath,
  onSelect,
}: {
  tree: WorkspaceTreeView | null;
  contextFiles: AgentContextFile[];
  codeRepositories: AgentCodeRepository[];
  contextManifests: ContextManifestSummary[];
  currentAgent: string;
  selectedPath: string;
  onSelect: (path: string) => void;
}): JSX.Element {
  const nodes = useMemo(() => buildExplorerTree(tree?.entries ?? []), [tree]);
  const contextNodes = useMemo(
    () => buildContextExplorerTree(contextFiles, codeRepositories, contextManifests, currentAgent),
    [codeRepositories, contextFiles, contextManifests, currentAgent],
  );
  const directoryPaths = useMemo(
    () => [
      WORKBENCH_ROOT_PATH,
      WORKBENCH_CONTEXT_ROOT_PATH,
      WORKBENCH_CODE_REPOSITORIES_PATH,
      WORKBENCH_AGENT_COMM_ROOT_PATH,
      ...collectExplorerDirectoryPaths(contextNodes),
      ...collectExplorerDirectoryPaths(nodes),
    ],
    [contextNodes, nodes],
  );
  const treeKey = tree ? `${tree.run_id}:${tree.agent_dir}` : "empty";
  const previousTreeKeyRef = useRef(treeKey);
  const seenDirectoryPathsRef = useRef<Set<string>>(new Set(DEFAULT_WORKBENCH_EXPANDED_PATHS));
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(DEFAULT_WORKBENCH_EXPANDED_PATHS),
  );
  useEffect(() => {
    if (previousTreeKeyRef.current === treeKey) {
      return;
    }
    previousTreeKeyRef.current = treeKey;
    seenDirectoryPathsRef.current = new Set(DEFAULT_WORKBENCH_EXPANDED_PATHS);
    setExpanded(new Set(DEFAULT_WORKBENCH_EXPANDED_PATHS));
  }, [treeKey]);
  useEffect(() => {
    setExpanded((current) => {
      const seen = seenDirectoryPathsRef.current;
      const next = new Set(current);
      let changed = false;
      for (const path of directoryPaths) {
        if (!seen.has(path)) {
          seen.add(path);
          next.add(path);
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [directoryPaths]);
  const toggle = (path: string): void => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };
  const rootOpen = expanded.has(WORKBENCH_ROOT_PATH);
  return (
    <div className="max-h-[660px] overflow-auto px-1 py-1.5">
      {tree ? (
        <button
          type="button"
          onClick={() => toggle(WORKBENCH_ROOT_PATH)}
          className="mb-1 flex w-full min-w-0 items-center gap-1 rounded px-2 py-1 text-left font-mono text-[11px] font-semibold text-slate-300 hover:bg-mars-bg/60"
        >
          <span className="w-3 text-slate-500">{rootOpen ? "v" : ">"}</span>
          <span className="truncate">{tree.agent_dir}</span>
        </button>
      ) : null}
      {rootOpen ? (
        <>
          <ExplorerNodeRow
            node={{
              name: "工作内容",
              path: WORKBENCH_WORKLOG_PATH,
              kind: "file",
              children: [],
            }}
            depth={1}
            expanded={expanded}
            selectedPath={selectedPath}
            onSelect={onSelect}
            onToggle={toggle}
          />
          <ExplorerNodeRow
            node={{
              name: "上下文配置",
              path: WORKBENCH_CONTEXT_ROOT_PATH,
              kind: "directory",
              children: contextNodes,
            }}
            depth={1}
            expanded={expanded}
            selectedPath={selectedPath}
            onSelect={onSelect}
            onToggle={toggle}
          />
          {nodes.length > 0 ? (
            nodes.map((node) => (
              <ExplorerNodeRow
                key={node.path}
                node={node}
                depth={1}
                expanded={expanded}
                selectedPath={selectedPath}
                onSelect={onSelect}
                onToggle={toggle}
              />
            ))
          ) : (
            <p className="px-2 py-2 text-xs text-slate-500">等待 Agent 沉淀工作区文件。</p>
          )}
        </>
      ) : (
        null
      )}
    </div>
  );
}

type ExplorerTreeNode = {
  name: string;
  path: string;
  kind: "file" | "directory";
  children: ExplorerTreeNode[];
};

function ExplorerNodeRow({
  node,
  depth,
  expanded,
  selectedPath,
  onSelect,
  onToggle,
}: {
  node: ExplorerTreeNode;
  depth: number;
  expanded: Set<string>;
  selectedPath: string;
  onSelect: (path: string) => void;
  onToggle: (path: string) => void;
}): JSX.Element {
  const isDirectory = node.kind === "directory";
  const isOpen = expanded.has(node.path);
  const active = node.path === selectedPath;
  return (
    <>
      <button
        type="button"
        onClick={() => {
          if (isDirectory) {
            onToggle(node.path);
          } else {
            onSelect(node.path);
          }
        }}
        className={`flex w-full min-w-0 items-center gap-1 rounded px-2 py-1 text-left font-mono text-[11px] ${
          active
            ? "bg-cyan-500/15 text-cyan-100"
            : isDirectory
              ? "text-slate-400 hover:bg-mars-bg/60"
              : "text-slate-300 hover:bg-mars-bg/70"
        }`}
        style={{ paddingLeft: `${8 + depth * 12}px` }}
      >
        <span className="w-3 shrink-0 text-slate-500">
          {isDirectory ? (isOpen ? "v" : ">") : ""}
        </span>
        <span className="truncate">{node.name}</span>
      </button>
      {isDirectory && isOpen
        ? node.children.map((child) => (
            <ExplorerNodeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              selectedPath={selectedPath}
              onSelect={onSelect}
              onToggle={onToggle}
            />
          ))
        : null}
    </>
  );
}

function buildExplorerTree(entries: WorkspaceTreeView["entries"]): ExplorerTreeNode[] {
  const root: ExplorerTreeNode = {
    name: "",
    path: "",
    kind: "directory",
    children: [],
  };
  for (const entry of entries) {
    if (workspaceExplorerHidden(entry.relative_path)) {
      continue;
    }
    const parts = entry.relative_path.split("/").filter(Boolean);
    let current = root;
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join("/");
      const kind = index === parts.length - 1 ? entry.kind : "directory";
      if (kind !== "file" && kind !== "directory") {
        return;
      }
      let child = current.children.find((item) => item.path === path);
      if (!child) {
        child = {
          name: part,
          path,
          kind,
          children: [],
        };
        current.children.push(child);
      }
      current = child;
    });
  }
  sortExplorerNodes(root.children);
  return root.children;
}

function buildContextExplorerTree(
  files: AgentContextFile[],
  repositories: AgentCodeRepository[],
  manifests: ContextManifestSummary[],
  currentAgent: string,
): ExplorerTreeNode[] {
  const root: ExplorerTreeNode = {
    name: "",
    path: "",
    kind: "directory",
    children: [],
  };
  root.children.push({
    name: "网址源",
    path: WORKBENCH_RESEARCH_SITES_PATH,
    kind: "file",
    children: [],
  });
  const hasRepository = repositories.some((repo) => repo.repo_path.trim());
  root.children.push({
    name: hasRepository ? "代码仓" : "代码仓（未配置）",
    path: WORKBENCH_CODE_REPOSITORIES_PATH,
    kind: "file",
    children: [],
  });
  const communicationRoot: ExplorerTreeNode = {
    name: "Agent通信记录",
    path: WORKBENCH_AGENT_COMM_ROOT_PATH,
    kind: "directory",
    children: [],
  };
  const receivedRoot: ExplorerTreeNode = {
    name: "接收的上下文",
    path: WORKBENCH_AGENT_RECEIVED_PATH,
    kind: "directory",
    children: [],
  };
  receivedRoot.children.push({
    name: "总览",
    path: WORKBENCH_AGENT_RECEIVED_OVERVIEW_PATH,
    kind: "file",
    children: [],
  });
  manifests
    .filter((manifest) => manifest.agent === currentAgent)
    .forEach((manifest, index) => {
      receivedRoot.children.push({
        name: manifestCallLabel(manifest, index),
        path: contextManifestWorkbenchPath(manifest.manifest_id),
        kind: "file",
        children: [],
      });
    });
  communicationRoot.children.push(receivedRoot);
  communicationRoot.children.push({
    name: "本 Agent 产出",
    path: WORKBENCH_AGENT_PRODUCED_PATH,
    kind: "file",
    children: [],
  });
  const allCallsRoot: ExplorerTreeNode = {
    name: "全链路调用快照",
    path: WORKBENCH_AGENT_ALL_CALLS_PATH,
    kind: "directory",
    children: [],
  };
  for (const [agentName, agentManifests] of groupManifestsByAgent(manifests)) {
    const agentNode: ExplorerTreeNode = {
      name: agentName,
      path: `${WORKBENCH_AGENT_ALL_CALLS_PATH}/${agentName}`,
      kind: "directory",
      children: [],
    };
    agentManifests.forEach((manifest, index) => {
      agentNode.children.push({
        name: manifestCallLabel(manifest, index),
        path: contextManifestWorkbenchPath(manifest.manifest_id),
        kind: "file",
        children: [],
      });
    });
    allCallsRoot.children.push(agentNode);
  }
  communicationRoot.children.push(allCallsRoot);
  if (manifests.length === 0) {
    communicationRoot.children.push({
      name: "暂无调用快照",
      path: WORKBENCH_AGENT_COMM_ROOT_PATH,
      kind: "file",
      children: [],
    });
  }
  root.children.push(communicationRoot);
  for (const file of files) {
    const parts = file.path.split("/").filter(Boolean);
    if (parts.length === 0) {
      continue;
    }
    let current = root;
    parts.forEach((part, index) => {
      const isFile = index === parts.length - 1;
      const path = isFile
        ? contextWorkbenchPath(file.path)
        : `${WORKBENCH_CONTEXT_ROOT_PATH}/${parts.slice(0, index + 1).join("/")}`;
      const kind = isFile ? "file" : "directory";
      let child = current.children.find((item) => item.path === path);
      if (!child) {
        child = {
          name: part,
          path,
          kind,
          children: [],
        };
        current.children.push(child);
      }
      current = child;
    });
  }
  sortExplorerNodes(root.children);
  return root.children;
}

function contextWorkbenchPath(path: string): string {
  return `${WORKBENCH_CONTEXT_ROOT_PATH}/${path}`;
}

function contextPathFromWorkbenchPath(path: string): string {
  const prefix = `${WORKBENCH_CONTEXT_ROOT_PATH}/`;
  return path.startsWith(prefix) ? path.slice(prefix.length) : "";
}

function isContextWorkbenchPath(path: string): boolean {
  return path === WORKBENCH_CONTEXT_ROOT_PATH || path.startsWith(`${WORKBENCH_CONTEXT_ROOT_PATH}/`);
}

function contextManifestWorkbenchPath(manifestId: string): string {
  return `${WORKBENCH_AGENT_COMM_ROOT_PATH}/${encodeURIComponent(manifestId)}`;
}

function contextManifestIdFromWorkbenchPath(path: string): string {
  const prefix = `${WORKBENCH_AGENT_COMM_ROOT_PATH}/`;
  if (!path.startsWith(prefix)) {
    return "";
  }
  const raw = path.slice(prefix.length);
  if (!raw || !raw.includes("context_manifest")) {
    return "";
  }
  return decodeURIComponent(raw);
}

function groupManifestsByAgent(
  manifests: ContextManifestSummary[],
): Array<[string, ContextManifestSummary[]]> {
  const grouped = new Map<string, ContextManifestSummary[]>();
  for (const manifest of manifests) {
    const agentName = manifest.agent || "unknown";
    grouped.set(agentName, [...(grouped.get(agentName) ?? []), manifest]);
  }
  return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function manifestCallLabel(manifest: ContextManifestSummary, index: number): string {
  const ordinal = String(index + 1).padStart(2, "0");
  const purpose = manifest.purpose || manifest.node_key || "context";
  const time = shortManifestTime(manifest.created_at);
  return time ? `${ordinal} · ${purpose} · ${time}.json` : `${ordinal} · ${purpose}.json`;
}

function shortManifestTime(value: string): string {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value.replace("T", " ").slice(0, 16);
  }
  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function findContextFileForWorkbenchPath(
  context: AgentContextView | null,
  selectedPath: string,
): AgentContextFile | null {
  const contextPath = contextPathFromWorkbenchPath(selectedPath);
  if (!contextPath) {
    return null;
  }
  return context?.files.find((file) => file.path === contextPath) ?? null;
}

function collectExplorerDirectoryPaths(nodes: ExplorerTreeNode[]): string[] {
  const paths: string[] = [];
  for (const node of nodes) {
    if (node.kind === "directory") {
      paths.push(node.path);
      paths.push(...collectExplorerDirectoryPaths(node.children));
    }
  }
  return paths;
}

function workspaceExplorerHidden(path: string): boolean {
  return (
    path === "evals" ||
    path.startsWith("evals/") ||
    path.endsWith(".artifact_quality.v1.json") ||
    path.endsWith(".contract_provenance.v1.json") ||
    path.endsWith(".contract_schema_validity.v1.json")
  );
}

function sortExplorerNodes(nodes: ExplorerTreeNode[]): void {
  nodes.sort((a, b) => {
    const rankA = explorerNodeRank(a);
    const rankB = explorerNodeRank(b);
    if (rankA !== rankB) {
      return rankA - rankB;
    }
    if (a.kind !== b.kind) {
      return a.kind === "directory" ? -1 : 1;
    }
    return a.name.localeCompare(b.name);
  });
  nodes.forEach((node) => sortExplorerNodes(node.children));
}

function explorerNodeRank(node: ExplorerTreeNode): number {
  if (node.path === WORKBENCH_RESEARCH_SITES_PATH) return 0;
  if (node.path === WORKBENCH_CODE_REPOSITORIES_PATH) return 1;
  if (node.path === WORKBENCH_AGENT_COMM_ROOT_PATH) return 2;
  if (node.path === WORKBENCH_AGENT_RECEIVED_PATH) return 0;
  if (node.path === WORKBENCH_AGENT_RECEIVED_OVERVIEW_PATH) return 0;
  if (node.path === WORKBENCH_AGENT_PRODUCED_PATH) return 1;
  if (node.path === WORKBENCH_AGENT_ALL_CALLS_PATH) return 2;
  return 10;
}

function WorkbenchFilePreview({ file }: { file: WorkspaceFileView }): JSX.Element {
  return (
    <section className="overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="flex min-h-[42px] items-center justify-between gap-2 border-b border-mars-border px-3 py-2">
        <span className="truncate font-mono text-xs text-slate-300">{file.relative_path}</span>
        <span className="shrink-0 font-mono text-[10px] text-slate-500">
          {Math.ceil(file.size_bytes / 1024)} KB
        </span>
      </div>
      <div className="max-h-[620px] overflow-auto px-5 py-4 text-sm leading-relaxed text-slate-200">
        {file.content_type === "text/markdown" ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {file.text.slice(0, 16000)}
          </ReactMarkdown>
        ) : (
          <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-slate-300">
            {file.text.slice(0, 16000)}
          </pre>
        )}
      </div>
    </section>
  );
}

function WorkbenchContextFilePreview({
  file,
  onSave,
}: {
  file: AgentContextFile;
  onSave: (path: string, content: string) => Promise<void>;
}): JSX.Element {
  const isMarkdown = file.path.endsWith(".md") || file.path.endsWith(".markdown");
  const [draft, setDraft] = useState(file.content);
  const [status, setStatus] = useState("");
  useEffect(() => {
    setDraft(file.content);
    setStatus("");
  }, [file.content, file.path]);
  async function save(): Promise<void> {
    if (!file.editable) return;
    await onSave(file.path, draft);
    setStatus("已保存");
  }
  return (
    <section className="overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="flex min-h-[42px] items-center justify-between gap-2 border-b border-mars-border px-3 py-2">
        <div className="min-w-0">
          <span className="block truncate font-mono text-xs text-slate-300">{file.path}</span>
          <span className="mt-0.5 block truncate text-[10px] text-slate-500">
            上下文配置 · {contextSourceLabel(file)} · {file.size_chars} chars
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {status ? <span className="text-[10px] text-emerald-300">{status}</span> : null}
          <span className="rounded border border-mars-border px-2 py-0.5 text-[10px] text-slate-400">
            {file.editable ? "可编辑" : "只读"}
          </span>
          {file.editable ? (
            <button
              type="button"
              onClick={() => void save()}
              className="rounded bg-mars-accent px-2 py-1 text-[11px] font-medium text-white"
            >
              保存
            </button>
          ) : null}
        </div>
      </div>
      <div className="max-h-[620px] overflow-auto px-5 py-4 text-sm leading-relaxed text-slate-200">
        {file.editable ? (
          <textarea
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              setStatus("");
            }}
            className="min-h-[520px] w-full resize-none bg-transparent font-mono text-xs leading-relaxed text-slate-100 outline-none"
          />
        ) : isMarkdown ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {draft}
          </ReactMarkdown>
        ) : (
          <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-slate-300">
            {draft}
          </pre>
        )}
      </div>
    </section>
  );
}

function WorkbenchWorkLogPanel({
  workLog,
  agent,
  state,
  artifact,
  onOpenTimeline,
}: {
  workLog: WorkLogView | null;
  agent: string;
  state: string;
  artifact: ArtifactView | null;
  onOpenTimeline: () => void;
}): JSX.Element {
  const items = workLog?.items ?? [];
  const latest = items.at(-1);
  const elapsed = workLog?.elapsed_seconds ?? latest?.elapsed_seconds ?? null;
  const nextAction = latest?.next_action || agentNextAction(agent, state, artifact);
  const copy = AGENT_WORK_COPY[agent] ?? {
    title: "Agent 工作区",
    purpose: "查看当前 Agent 的产物、上下文、评估和运行证据。",
    artifact: "artifact.v1",
    handoff: "批准后进入下一步",
  };
  return (
    <section className="overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="border-b border-mars-border bg-mars-panel/35 px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded bg-mars-accent/80 px-2 py-0.5 text-[10px] font-semibold uppercase text-white">
                {agentLabel(agent)}
              </span>
              <StateBadge state={state} />
              <span className="rounded bg-mars-bg px-2 py-0.5 font-mono text-[10px] text-slate-400">
                已处理 {formatWorkLogElapsed(elapsed)}
              </span>
            </div>
            <h3 className="mt-2 text-base font-semibold text-slate-100">工作内容</h3>
            <p className="mt-1 max-w-3xl text-xs leading-relaxed text-slate-400">
              {latest?.detail || `${copy.title}会在这里展示可公开过程摘要、工具动作、人工反馈、证据沉淀和下一步。`}
            </p>
            <p className="mt-2 text-xs leading-relaxed text-cyan-100">
              下一步：{nextAction}
            </p>
          </div>
          <button
            type="button"
            onClick={onOpenTimeline}
            className="rounded border border-mars-border bg-mars-bg/70 px-3 py-1.5 text-xs text-slate-200 hover:bg-mars-panel"
          >
            查看原始执行流
          </button>
        </div>
      </div>

      <div className="grid min-h-[580px] gap-0 xl:grid-cols-[minmax(0,1fr)_320px]">
        <ol className="max-h-[calc(100vh-260px)] min-h-[580px] overflow-auto p-4">
          {items.length > 0 ? (
            items.map((item) => (
              <WorkLogRow key={item.id} item={item} />
            ))
          ) : (
            <li className="rounded border border-dashed border-mars-border px-3 py-12 text-center text-sm text-slate-500">
              {state === "pending" ? "等待上游 Agent 交接，启动后这里会出现真实工作过程。" : "正在等待工作事件。"}
            </li>
          )}
        </ol>
        <aside className="border-t border-mars-border bg-mars-panel/25 p-4 xl:border-l xl:border-t-0">
          <h4 className="text-xs font-semibold uppercase text-slate-500">本轮状态</h4>
          <div className="mt-3 space-y-2 text-xs">
            <WorkLogFact label="Run" value={workLog?.run_id ?? "-"} mono />
            <WorkLogFact label="Agent" value={agentLabel(agent)} />
            <WorkLogFact label="状态" value={state} />
            <WorkLogFact
              label="产物"
              value={artifact ? `${artifact.stem}.${artifact.version}.md` : "未生成"}
              mono
            />
            <WorkLogFact
              label="过程事件"
              value={`${items.length}`}
            />
          </div>
          <div className="mt-5 rounded border border-cyan-500/25 bg-cyan-500/10 p-3 text-xs leading-relaxed text-cyan-50">
            这里不是模型私有草稿，而是从真实事件、工具调用、评价、工作区文件和人工反馈中整理出的过程摘要。
          </div>
        </aside>
      </div>
    </section>
  );
}

function WorkLogRow({ item }: { item: WorkLogItem }): JSX.Element {
  return (
    <li className="relative border-l border-mars-border pb-4 pl-5 last:pb-0">
      <span className={`absolute -left-[5px] top-2 h-2.5 w-2.5 rounded-full border ${workLogDotClass(item.kind, item.status)}`} />
      <article className="rounded border border-mars-border bg-mars-panel/45 p-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded border px-1.5 py-0.5 text-[10px] ${workLogKindClass(item.kind)}`}>
                {workLogKindLabel(item.kind)}
              </span>
              {item.status ? (
                <span className="rounded bg-mars-bg px-1.5 py-0.5 text-[10px] text-slate-400">
                  {item.status}
                </span>
              ) : null}
              <h4 className="break-words text-sm font-semibold text-slate-100">
                {item.title}
              </h4>
            </div>
            {item.detail ? (
              <p className="mt-1 break-words text-xs leading-relaxed text-slate-400">
                {item.detail}
              </p>
            ) : null}
            {item.next_action ? (
              <p className="mt-2 break-words text-xs leading-relaxed text-cyan-100/90">
                下一步：{item.next_action}
              </p>
            ) : null}
          </div>
          <div className="shrink-0 text-right font-mono text-[10px] text-slate-500">
            <div>{formatWorkLogTime(item.timestamp)}</div>
            <div>{formatWorkLogElapsed(item.elapsed_seconds)}</div>
          </div>
        </div>
        {item.evidence_refs.length > 0 ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {item.evidence_refs.slice(0, 6).map((ref) => (
              <span
                key={ref}
                className="max-w-full truncate rounded bg-mars-bg px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
              >
                {ref}
              </span>
            ))}
          </div>
        ) : null}
      </article>
    </li>
  );
}

function WorkLogFact({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="rounded border border-mars-border bg-mars-bg/50 px-2 py-1.5">
      <div className="text-[10px] uppercase text-slate-500">{label}</div>
      <div className={`mt-0.5 break-words text-slate-200 ${mono ? "font-mono text-[11px]" : "text-xs"}`}>
        {value}
      </div>
    </div>
  );
}

function agentNextAction(agent: string, state: string, artifact: ArtifactView | null): string {
  if (state === "running") {
    const running: Record<string, string> = {
      idea: "继续读代码仓/资料，收敛为可审核研究假设。",
      experiment: "继续核对 proposal、baseline 约束和实验预算，生成实验矩阵。",
      coding: "继续读取目标文件、生成 code_spec 和可审计补丁。",
      execution: "继续运行或汇总仿真，沉淀日志、指标和曲线。",
      writing: "继续汇总全链路证据，生成报告和可导出附件。",
    };
    return running[agent] ?? "继续收集证据、调用工具或生成新版产物。";
  }
  if (state === "waiting_review") return artifact ? "等待人工批准，或输入意见驳回后继续返工。" : "等待产物进入审核。";
  if (state === "pending") {
    const pending: Record<string, string> = {
      experiment: "等待 Idea Agent 的 proposal 批准后启动。",
      coding: "等待 Experiment Agent 的 experiment_plan 批准后启动。",
      execution: "等待 Coding Agent 的 code_spec/patch 批准后启动。",
      writing: "等待 Execution Agent 的 run_log 和指标完成后启动。",
    };
    return pending[agent] ?? "等待上游节点完成后启动。";
  }
  if (state === "failed") return "查看主控诊断并决定是否重试。";
  return "查看工作区文件、上下文配置和执行流。";
}

function formatWorkLogElapsed(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "-";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

function formatWorkLogTime(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 19);
  return date.toLocaleTimeString();
}

function workLogKindLabel(kind: string): string {
  const labels: Record<string, string> = {
    run: "任务",
    state: "状态",
    context: "上下文",
    tool: "工具",
    human_feedback: "反馈",
    revision: "返工",
    evaluation: "评价",
    artifact: "产物",
    hitl: "审核",
  };
  return labels[kind] ?? kind;
}

function workLogKindClass(kind: string): string {
  const classes: Record<string, string> = {
    run: "border-cyan-500/40 bg-cyan-500/10 text-cyan-100",
    state: "border-slate-500/40 bg-slate-500/10 text-slate-100",
    context: "border-sky-500/40 bg-sky-500/10 text-sky-100",
    tool: "border-amber-500/40 bg-amber-500/10 text-amber-100",
    human_feedback: "border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-100",
    revision: "border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-100",
    evaluation: "border-violet-500/40 bg-violet-500/10 text-violet-100",
    artifact: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100",
    hitl: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100",
  };
  return classes[kind] ?? "border-mars-border bg-mars-bg text-slate-200";
}

function workLogDotClass(kind: string, status: string): string {
  if (status === "error" || status === "failed") return "border-red-400 bg-red-400";
  if (kind === "tool") return "border-amber-300 bg-amber-300";
  if (kind === "human_feedback" || kind === "revision") return "border-fuchsia-300 bg-fuchsia-300";
  if (kind === "evaluation" || kind === "artifact" || kind === "hitl") return "border-emerald-300 bg-emerald-300";
  return "border-cyan-300 bg-cyan-300";
}

function WorkbenchResearchSitesPanel({
  sites,
  onSave,
}: {
  sites: AgentResearchSite[];
  onSave: (sites: AgentResearchSite[]) => Promise<void>;
}): JSX.Element {
  const [draft, setDraft] = useState<AgentResearchSite[]>(sites);
  const [status, setStatus] = useState("");
  useEffect(() => {
    setDraft(sites);
    setStatus("");
  }, [sites]);
  function patchSite(index: number, patch: Partial<AgentResearchSite>): void {
    setDraft((current) => current.map((site, i) => (i === index ? { ...site, ...patch } : site)));
    setStatus("");
  }
  function addSite(): void {
    setDraft((current) => [
      ...current,
      {
        id: `site_${current.length + 1}`,
        label: "New Site",
        url: "https://",
        enabled: true,
        source: "user",
      },
    ]);
    setStatus("");
  }
  function removeSite(index: number): void {
    setDraft((current) => current.filter((_, i) => i !== index));
    setStatus("");
  }
  async function save(): Promise<void> {
    await onSave(draft);
    setStatus("已保存网址源");
  }
  return (
    <section className="overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="flex min-h-[42px] items-center justify-between gap-2 border-b border-mars-border px-3 py-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">网址源</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">
            这些站点会进入 Agent 的调研上下文，用来约束真实检索和证据来源。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {status ? <span className="text-[10px] text-emerald-300">{status}</span> : null}
          <button
            type="button"
            onClick={addSite}
            className="rounded border border-mars-border px-2 py-1 text-[11px] text-slate-200 hover:bg-mars-panel"
          >
            新增
          </button>
          <button
            type="button"
            onClick={() => void save()}
            className="rounded bg-mars-accent px-2 py-1 text-[11px] font-medium text-white"
          >
            保存
          </button>
        </div>
      </div>
      <div className="space-y-2 p-3">
        {draft.map((site, index) => (
          <div
            key={`${site.id}:${index}`}
            className="grid grid-cols-[auto_160px_minmax(0,1fr)_auto] items-center gap-2 rounded border border-mars-border bg-mars-panel/25 p-2"
          >
            <input
              type="checkbox"
              checked={site.enabled}
              onChange={(event) => patchSite(index, { enabled: event.target.checked })}
              className="h-4 w-4 accent-mars-accent"
              aria-label={`${site.label} enabled`}
            />
            <input
              value={site.label}
              onChange={(event) => patchSite(index, { label: event.target.value })}
              className="min-w-0 rounded border border-mars-border bg-mars-bg px-2 py-1.5 text-xs text-slate-100 outline-none"
              aria-label="site label"
            />
            <input
              value={site.url}
              onChange={(event) => patchSite(index, { url: event.target.value })}
              className="min-w-0 rounded border border-mars-border bg-mars-bg px-2 py-1.5 font-mono text-xs text-slate-100 outline-none"
              aria-label="site url"
            />
            <button
              type="button"
              onClick={() => removeSite(index)}
              className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-[11px] text-red-200 hover:bg-red-500/20"
            >
              删除
            </button>
          </div>
        ))}
        {draft.length === 0 ? (
          <p className="rounded border border-mars-border bg-mars-panel/25 p-3 text-xs text-slate-500">
            还没有配置网址源。
          </p>
        ) : null}
      </div>
    </section>
  );
}

function WorkbenchCodeRepositoriesPanel({
  project,
  runId,
  repositories,
  onSave,
}: {
  project: string;
  runId: string;
  repositories: AgentCodeRepository[];
  onSave: (repositories: AgentCodeRepository[]) => Promise<void>;
}): JSX.Element {
  const [draft, setDraft] = useState<AgentCodeRepository[]>(
    repositories.length > 0 ? repositories : [emptyCodeRepository()],
  );
  const [workspace, setWorkspace] = useState<CodingWorkspace | null>(null);
  const [workspaceError, setWorkspaceError] = useState("");
  const [source, setSource] = useState("project_repo");
  const [selectedPath, setSelectedPath] = useState("");
  const [selectedFile, setSelectedFile] = useState<CodeFileContent | null>(null);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [codeSidebarCollapsed, setCodeSidebarCollapsed] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [status, setStatus] = useState("");
  useEffect(() => {
    setDraft(repositories.length > 0 ? repositories : [emptyCodeRepository()]);
    setStatus("");
  }, [repositories]);
  const repo = draft[0] ?? emptyCodeRepository();
  const persistedRepo = repositories[0] ?? emptyCodeRepository();
  const persistedSignature = codeRepositorySignature(persistedRepo);
  const configDirty = codeRepositorySignature(repo) !== persistedSignature;
  const activeSource = workspace?.selected_source ?? source;
  const codeTree = useMemo(
    () => buildCodeRepositoryTree(workspace?.files ?? []),
    [workspace],
  );
  const fileCount = workspace?.files.filter((item) => item.kind === "file").length ?? 0;
  const visibleFiles = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const files = workspace?.files.filter((item) => item.kind === "file") ?? [];
    if (!needle) {
      return files;
    }
    return files.filter((item) => item.path.toLowerCase().includes(needle));
  }, [query, workspace]);
  const codeRepositoryGridClass = codeSidebarCollapsed
    ? "lg:grid-cols-[minmax(0,1fr)]"
    : "lg:grid-cols-[320px_minmax(0,1fr)]";

  useEffect(() => {
    setSource(persistedRepo.exists ? "project_repo" : "auto");
    setSelectedPath("");
    setSelectedFile(null);
  }, [persistedSignature, persistedRepo.exists]);

  useEffect(() => {
    let alive = true;
    setWorkspaceError("");
    void getCodingWorkspace({
      project,
      runId: runId || undefined,
      source,
    })
      .then((next) => {
        if (!alive) return;
        setWorkspace(next);
        const firstReadable =
          next.files.find(
            (item) =>
              item.kind === "file" &&
              codePathScope(item.path, persistedRepo) !== "outside",
          ) ?? next.files.find((item) => item.kind === "file");
        setSelectedPath((current) => {
          if (current && next.files.some((item) => item.kind === "file" && item.path === current)) {
            return current;
          }
          return firstReadable?.path ?? "";
        });
        setExpanded((current) => {
          if (current.size > 0) {
            return current;
          }
          return new Set(firstReadable ? codePathAncestors(firstReadable.path) : []);
        });
      })
      .catch((error: unknown) => {
        if (!alive) return;
        setWorkspace(null);
        setWorkspaceError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
    };
  }, [project, runId, source, persistedSignature, reloadToken]);

  useEffect(() => {
    let alive = true;
    setSelectedFile(null);
    if (!selectedPath || !workspace) {
      return () => {
        alive = false;
      };
    }
    void getCodingWorkspaceFile({
      project,
      source: activeSource,
      path: selectedPath,
    })
      .then((next) => {
        if (alive) setSelectedFile(next);
      })
      .catch(() => {
        if (alive) setSelectedFile(null);
      });
    return () => {
      alive = false;
    };
  }, [activeSource, project, selectedPath, workspace]);

  function patchRepo(patch: Partial<AgentCodeRepository>): void {
    setDraft((current) => [{ ...(current[0] ?? emptyCodeRepository()), ...patch }]);
    setStatus("");
  }
  async function save(): Promise<void> {
    await onSave([repo]);
    setStatus("已保存代码仓");
    setReloadToken((current) => current + 1);
  }
  function toggleDirectory(path: string): void {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }
  return (
    <section className="overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="flex min-h-[54px] items-center justify-between gap-3 border-b border-mars-border px-3 py-2">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-100">代码仓</h3>
            <span className={`rounded px-2 py-0.5 text-[10px] ${repo.exists ? "bg-emerald-500/15 text-emerald-200" : "bg-amber-500/15 text-amber-200"}`}>
              {repo.exists ? "路径存在" : "路径未找到"}
            </span>
            {configDirty ? (
              <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[10px] text-amber-200">
                未保存
              </span>
            ) : null}
            <span className="rounded bg-mars-panel px-2 py-0.5 text-[10px] text-slate-400">
              {repo.read_only ? "只读" : "可写"} · {repo.sync_strategy}
            </span>
          </div>
          <p className="mt-1 truncate font-mono text-[11px] text-slate-500">
            {repo.repo_path || "未配置仓库路径"}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
          {status ? <span className="text-[10px] text-emerald-300">{status}</span> : null}
          <SidebarToggleButton
            collapsed={codeSidebarCollapsed}
            side="left"
            label="项目代码边栏"
            onToggle={() => setCodeSidebarCollapsed((current) => !current)}
          />
          <button
            type="button"
            onClick={() => setReloadToken((current) => current + 1)}
            className="rounded border border-mars-border px-2 py-1 text-[11px] text-slate-200 hover:bg-mars-panel"
          >
            刷新
          </button>
          <button
            type="button"
            onClick={() => setSettingsOpen((current) => !current)}
            className="rounded border border-mars-border px-2 py-1 text-[11px] text-slate-200 hover:bg-mars-panel"
          >
            {settingsOpen ? "收起规则" : "上下文规则"}
          </button>
          <button
            type="button"
            onClick={() => void save()}
            className="rounded bg-mars-accent px-2 py-1 text-[11px] font-medium text-white"
          >
            保存
          </button>
        </div>
      </div>
      <div className={`grid h-[calc(100vh-250px)] min-h-[560px] overflow-hidden ${codeRepositoryGridClass}`}>
        {!codeSidebarCollapsed ? (
        <aside className="flex min-h-0 flex-col border-b border-mars-border bg-mars-panel/30 lg:border-b-0 lg:border-r">
          <div className="space-y-2 border-b border-mars-border p-3">
            <div className="grid grid-cols-3 gap-2">
              <CodeRepoMetric label="文件" value={fileCount} />
              <CodeRepoMetric label="读取" value={repo.allowed_paths.length} />
              <CodeRepoMetric label="写保护" value={repo.protected_paths.length} />
            </div>
            <select
              value={activeSource}
              onChange={(event) => {
                setSource(event.target.value);
                setSelectedPath("");
                setSelectedFile(null);
                setExpanded(new Set());
              }}
              className="w-full rounded border border-mars-border bg-mars-bg px-2 py-2 text-xs text-slate-200 outline-none"
            >
              {(workspace?.sources ?? []).map((item) => (
                <option key={item.id} value={item.id} disabled={!item.exists}>
                  {item.label}{item.exists ? "" : " (不可用)"}
                </option>
              ))}
              {!workspace ? <option value={source}>{source}</option> : null}
            </select>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索文件"
              className="w-full rounded border border-mars-border bg-mars-bg px-2 py-2 text-xs text-slate-200 outline-none placeholder:text-slate-600"
            />
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {workspaceError ? (
              <div className="rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
                {workspaceError}
              </div>
            ) : null}
            {!workspace && !workspaceError ? (
              <div className="rounded border border-dashed border-mars-border px-3 py-8 text-center text-xs text-slate-500">
                正在读取代码仓…
              </div>
            ) : null}
            {workspace && query.trim() ? (
              <div className="space-y-1">
                {visibleFiles.slice(0, 120).map((item) => (
                  <CodeRepositorySearchRow
                    key={item.path}
                    item={item}
                    repo={repo}
                    active={selectedPath === item.path}
                    onSelect={() => setSelectedPath(item.path)}
                  />
                ))}
                {visibleFiles.length === 0 ? (
                  <div className="rounded border border-dashed border-mars-border px-3 py-8 text-center text-xs text-slate-500">
                    没有匹配文件
                  </div>
                ) : null}
              </div>
            ) : null}
            {workspace && !query.trim() ? (
              codeTree.length > 0 ? (
                codeTree.map((node) => (
                  <CodeRepositoryTreeRow
                    key={node.path}
                    node={node}
                    depth={0}
                    repo={repo}
                    expanded={expanded}
                    selectedPath={selectedPath}
                    onToggle={toggleDirectory}
                    onSelect={setSelectedPath}
                  />
                ))
              ) : (
                <div className="rounded border border-dashed border-mars-border px-3 py-8 text-center text-xs text-slate-500">
                  空代码仓
                </div>
              )
            ) : null}
          </div>
        </aside>
        ) : null}
        <div className="flex min-h-0 flex-col">
          <CodeRepositoryFilePreview file={selectedFile} selectedPath={selectedPath} repo={repo} />
          {settingsOpen ? (
            <div className="border-t border-mars-border bg-mars-panel/20 p-3">
              <div className="grid gap-3">
                <label className="grid gap-1 text-xs text-slate-400">
                  仓库路径
                  <input
                    value={repo.repo_path}
                    onChange={(event) => patchRepo({ repo_path: event.target.value })}
                    className="rounded border border-mars-border bg-mars-bg px-2 py-1.5 font-mono text-xs text-slate-100 outline-none"
                  />
                </label>
                <div className="grid gap-3 md:grid-cols-3">
                  <label className="grid gap-1 text-xs text-slate-400">
                    repo_mode
                    <select
                      value={repo.repo_mode}
                      onChange={(event) => patchRepo({ repo_mode: event.target.value })}
                      className="rounded border border-mars-border bg-mars-bg px-2 py-1.5 text-xs text-slate-100 outline-none"
                    >
                      <option value="local_path">local_path</option>
                      <option value="git_submodule">git_submodule</option>
                      <option value="mirror">mirror</option>
                    </select>
                  </label>
                  <label className="grid gap-1 text-xs text-slate-400">
                    sync_strategy
                    <select
                      value={repo.sync_strategy}
                      onChange={(event) => patchRepo({ sync_strategy: event.target.value })}
                      className="rounded border border-mars-border bg-mars-bg px-2 py-1.5 text-xs text-slate-100 outline-none"
                    >
                      <option value="live">live</option>
                      <option value="snapshot">snapshot</option>
                    </select>
                  </label>
                  <label className="flex items-center gap-2 pt-5 text-xs text-slate-300">
                    <input
                      type="checkbox"
                      checked={repo.read_only}
                      onChange={(event) => patchRepo({ read_only: event.target.checked })}
                      className="h-4 w-4 accent-mars-accent"
                    />
                    read_only
                  </label>
                </div>
                <div className="grid gap-3 xl:grid-cols-3">
                  <ContextListTextarea
                    label="allowed_paths"
                    value={repo.allowed_paths}
                    onChange={(items) => patchRepo({ allowed_paths: items })}
                  />
                  <ContextListTextarea
                    label="protected_paths"
                    value={repo.protected_paths}
                    onChange={(items) => patchRepo({ protected_paths: items })}
                  />
                  <ContextListTextarea
                    label="ignore_patterns"
                    value={repo.ignore_patterns}
                    onChange={(items) => patchRepo({ ignore_patterns: items })}
                  />
                </div>
                <label className="grid gap-1 text-xs text-slate-400">
                  baseline_rules_file
                  <input
                    value={repo.baseline_rules_file}
                    onChange={(event) => patchRepo({ baseline_rules_file: event.target.value })}
                    className="rounded border border-mars-border bg-mars-bg px-2 py-1.5 font-mono text-xs text-slate-100 outline-none"
                  />
                </label>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

type CodeRepositoryTreeNode = {
  name: string;
  path: string;
  kind: "file" | "directory";
  item: CodeTreeItem | null;
  children: CodeRepositoryTreeNode[];
};

function CodeRepoMetric({ label, value }: { label: string; value: number }): JSX.Element {
  return (
    <div className="rounded border border-mars-border bg-mars-bg px-2 py-1.5 text-center">
      <div className="font-mono text-sm text-slate-100">{value}</div>
      <div className="mt-0.5 text-[9px] uppercase text-slate-500">{label}</div>
    </div>
  );
}

function CodeRepositoryTreeRow({
  node,
  depth,
  repo,
  expanded,
  selectedPath,
  onToggle,
  onSelect,
}: {
  node: CodeRepositoryTreeNode;
  depth: number;
  repo: AgentCodeRepository;
  expanded: Set<string>;
  selectedPath: string;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
}): JSX.Element {
  const isDirectory = node.kind === "directory";
  const isOpen = expanded.has(node.path);
  const active = selectedPath === node.path;
  const scope = codePathScope(node.path, repo);
  return (
    <>
      <button
        type="button"
        onClick={() => {
          if (isDirectory) {
            onToggle(node.path);
          } else {
            onSelect(node.path);
          }
        }}
        className={`mb-0.5 flex w-full min-w-0 items-center gap-1 rounded px-2 py-1.5 text-left font-mono text-[11px] ${
          active ? "bg-cyan-500/15 text-cyan-100" : "text-slate-300 hover:bg-mars-bg/70"
        } ${isDirectory ? "text-slate-400" : ""}`}
        style={{ paddingLeft: `${8 + depth * 13}px` }}
      >
        <span className="w-3 shrink-0 text-slate-500">
          {isDirectory ? (isOpen ? "v" : ">") : ""}
        </span>
        <span className="min-w-0 flex-1 truncate">
          {node.name}{isDirectory ? "/" : ""}
        </span>
        {!isDirectory ? <CodeScopeBadge scope={scope} compact /> : null}
      </button>
      {isDirectory && isOpen
        ? node.children.map((child) => (
            <CodeRepositoryTreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              repo={repo}
              expanded={expanded}
              selectedPath={selectedPath}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))
        : null}
    </>
  );
}

function CodeRepositorySearchRow({
  item,
  repo,
  active,
  onSelect,
}: {
  item: CodeTreeItem;
  repo: AgentCodeRepository;
  active: boolean;
  onSelect: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full min-w-0 items-center gap-2 rounded px-2 py-1.5 text-left font-mono text-[11px] ${
        active ? "bg-cyan-500/15 text-cyan-100" : "text-slate-300 hover:bg-mars-bg/70"
      }`}
    >
      <span className="min-w-0 flex-1 truncate">{item.path}</span>
      <CodeScopeBadge scope={codePathScope(item.path, repo)} compact />
    </button>
  );
}

function CodeRepositoryFilePreview({
  file,
  selectedPath,
  repo,
}: {
  file: CodeFileContent | null;
  selectedPath: string;
  repo: AgentCodeRepository;
}): JSX.Element {
  if (!selectedPath) {
    return (
      <section className="flex min-h-0 flex-1 items-center justify-center overflow-hidden text-sm text-slate-500">
        选择左侧文件
      </section>
    );
  }
  if (!file) {
    return (
      <section className="flex min-h-0 flex-1 items-center justify-center overflow-hidden text-sm text-slate-500">
        正在读取文件…
      </section>
    );
  }
  const lines = file.content.split("\n");
  const scope = codePathScope(file.path, repo);
  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex min-h-[48px] items-center justify-between gap-3 border-b border-mars-border px-3 py-2">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate font-mono text-xs font-semibold text-slate-100">
              {file.path}
            </span>
            <CodeScopeBadge scope={scope} />
          </div>
          <p className="mt-0.5 truncate text-[10px] text-slate-500">
            {file.language || "text"} · {file.size_chars} chars{file.truncated ? " · truncated" : ""}
          </p>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto overscroll-contain bg-[#090d14] py-3">
        <pre className="font-mono text-[11px] leading-relaxed text-slate-300">
          {lines.map((line, index) => (
            <span
              key={`${index}:${line.slice(0, 12)}`}
              className="grid min-w-max grid-cols-[52px_minmax(0,1fr)] px-3 hover:bg-white/[0.035]"
            >
              <span className="select-none pr-4 text-right text-slate-600">
                {index + 1}
              </span>
              <span className="whitespace-pre pr-6">{line || " "}</span>
            </span>
          ))}
        </pre>
      </div>
    </section>
  );
}

type CodePathScope = "protected" | "allowed" | "readable" | "outside";

function CodeScopeBadge({
  scope,
  compact = false,
}: {
  scope: CodePathScope;
  compact?: boolean;
}): JSX.Element {
  const labels: Record<CodePathScope, string> = {
    protected: compact ? "可读·保护" : "可读 · 写保护",
    allowed: "读取",
    readable: "可读",
    outside: "未纳入",
  };
  const classes: Record<CodePathScope, string> = {
    protected: "border-amber-500/40 bg-amber-500/10 text-amber-100",
    allowed: "border-emerald-500/35 bg-emerald-500/10 text-emerald-200",
    readable: "border-slate-600 bg-slate-800/60 text-slate-300",
    outside: "border-amber-500/35 bg-amber-500/10 text-amber-200",
  };
  return (
    <span
      className={`shrink-0 rounded border ${classes[scope]} ${
        compact ? "px-1.5 py-0 text-[9px]" : "px-2 py-0.5 text-[10px]"
      }`}
    >
      {labels[scope]}
    </span>
  );
}

function buildCodeRepositoryTree(items: CodeTreeItem[]): CodeRepositoryTreeNode[] {
  const root: CodeRepositoryTreeNode = {
    name: "",
    path: "",
    kind: "directory",
    item: null,
    children: [],
  };
  for (const item of items) {
    const parts = item.path.split("/").filter(Boolean);
    let current = root;
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join("/");
      const kind = index === parts.length - 1 ? item.kind : "directory";
      let child = current.children.find((candidate) => candidate.path === path);
      if (!child) {
        child = {
          name: part,
          path,
          kind,
          item: kind === item.kind && path === item.path ? item : null,
          children: [],
        };
        current.children.push(child);
      }
      if (path === item.path) {
        child.kind = item.kind;
        child.item = item;
      }
      current = child;
    });
  }
  sortCodeRepositoryNodes(root.children);
  return root.children;
}

function sortCodeRepositoryNodes(nodes: CodeRepositoryTreeNode[]): void {
  nodes.sort((a, b) => {
    if (a.kind !== b.kind) {
      return a.kind === "directory" ? -1 : 1;
    }
    return a.name.localeCompare(b.name);
  });
  nodes.forEach((node) => sortCodeRepositoryNodes(node.children));
}

function codeRepositorySignature(repo: AgentCodeRepository): string {
  return JSON.stringify({
    repo_path: repo.repo_path,
    repo_mode: repo.repo_mode,
    read_only: repo.read_only,
    sync_strategy: repo.sync_strategy,
    allowed_paths: repo.allowed_paths,
    protected_paths: repo.protected_paths,
    ignore_patterns: repo.ignore_patterns,
    baseline_rules_file: repo.baseline_rules_file,
    exists: repo.exists,
  });
}

function codePathScope(path: string, repo: AgentCodeRepository): CodePathScope {
  if (repo.protected_paths.some((rule) => codePathMatchesRule(path, rule))) {
    return "protected";
  }
  if (repo.allowed_paths.length === 0) {
    return "readable";
  }
  if (repo.allowed_paths.some((rule) => codePathMatchesRule(path, rule))) {
    return "allowed";
  }
  return "outside";
}

function codePathMatchesRule(path: string, rule: string): boolean {
  const normalizedPath = normalizeCodePathRule(path);
  const normalizedRule = normalizeCodePathRule(rule.split(":", 1)[0] ?? "");
  if (!normalizedRule) {
    return false;
  }
  if (normalizedRule.endsWith("/")) {
    const prefix = normalizedRule.replace(/\/+$/, "");
    return normalizedPath === prefix || normalizedPath.startsWith(`${prefix}/`);
  }
  return normalizedPath === normalizedRule || normalizedPath.startsWith(`${normalizedRule}/`);
}

function normalizeCodePathRule(value: string): string {
  return value.trim().replace(/^\.\//, "").replace(/\\/g, "/");
}

function codePathAncestors(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

function ContextListTextarea({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string[];
  onChange: (items: string[]) => void;
}): JSX.Element {
  return (
    <label className="grid gap-1 text-xs text-slate-400">
      {label}
      <textarea
        value={value.join("\n")}
        onChange={(event) =>
          onChange(event.target.value.split("\n").map((item) => item.trim()).filter(Boolean))
        }
        className="min-h-[88px] resize-y rounded border border-mars-border bg-mars-bg px-2 py-1.5 font-mono text-xs leading-relaxed text-slate-100 outline-none"
      />
    </label>
  );
}

function emptyCodeRepository(): AgentCodeRepository {
  return {
    project: "pimc",
    label: "项目代码仓",
    repo_mode: "local_path",
    repo_path: "",
    exists: false,
    read_only: false,
    sync_strategy: "live",
    allowed_paths: [],
    protected_paths: [],
    ignore_patterns: [],
    baseline_rules_file: "./AGENTS.md",
  };
}

function WorkbenchReceivedContextPanel({
  agent,
  manifests,
  onOpenManifest,
}: {
  agent: string;
  manifests: ContextManifestSummary[];
  onOpenManifest: (path: string) => void;
}): JSX.Element {
  const currentManifests = manifests.filter((manifest) => manifest.agent === agent);
  return (
    <section className="overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="border-b border-mars-border px-3 py-2">
        <h3 className="text-sm font-semibold text-slate-100">接收的上下文</h3>
        <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
          每一条都是 {agentLabel(agent)} Agent 调 LLM 前的一次上下文快照。打开某次调用可以直接看到上游 Agent 传入的 handoff 段。
        </p>
      </div>
      <div className="max-h-[620px] space-y-2 overflow-auto p-3">
        {currentManifests.length > 0 ? (
          currentManifests.map((manifest, index) => (
            <button
              key={manifest.manifest_id}
              type="button"
              onClick={() => onOpenManifest(contextManifestWorkbenchPath(manifest.manifest_id))}
              className="block w-full rounded border border-mars-border bg-mars-panel/25 p-3 text-left hover:bg-mars-panel/45"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs text-slate-200">
                  {manifestCallLabel(manifest, index)}
                </span>
                <span className="text-[10px] text-slate-500">
                  {manifest.segment_count} segments
                </span>
              </div>
              <p className="mt-1 truncate text-[11px] text-slate-500">
                {manifest.path} · tokens {String(manifest.budget.used ?? "-")}
              </p>
            </button>
          ))
        ) : (
          <div className="rounded border border-mars-border bg-mars-panel/25 p-3 text-xs text-slate-500">
            当前 Agent 还没有 context manifest。运行到该 Agent 的 LLM 调用前，这里会出现逐次快照。
          </div>
        )}
      </div>
    </section>
  );
}

function WorkbenchProducedContentPanel({
  artifact,
  artifactFileName,
  workspaceFiles,
  workspaceTree,
  onOpenPath,
}: {
  artifact: ArtifactView | null;
  artifactFileName: string;
  workspaceFiles: WorkspaceFileView[];
  workspaceTree: WorkspaceTreeView | null;
  onOpenPath: (path: string) => void;
}): JSX.Element {
  const treeFiles =
    workspaceTree?.entries.filter((entry) => entry.kind === "file" && !workspaceExplorerHidden(entry.relative_path)) ??
    [];
  return (
    <section className="overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="border-b border-mars-border px-3 py-2">
        <h3 className="text-sm font-semibold text-slate-100">本 Agent 产出</h3>
        <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
          这里汇总当前 Agent 已沉淀的主产物和工作区文件，点击条目可切到正文或文件预览。
        </p>
      </div>
      <div className="max-h-[620px] space-y-3 overflow-auto p-3">
        {artifact ? (
          <button
            type="button"
            onClick={() => onOpenPath(artifactFileName)}
            className="block w-full rounded border border-cyan-500/30 bg-cyan-500/10 p-3 text-left hover:bg-cyan-500/15"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-mono text-xs font-semibold text-cyan-100">
                {artifactFileName}
              </span>
              <span className="rounded bg-cyan-500/20 px-2 py-0.5 text-[10px] text-cyan-100">
                主产物
              </span>
            </div>
            <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs leading-relaxed text-slate-300">
              {splitFrontmatter(artifact.text).body.slice(0, 600)}
            </p>
            <CodingChangeSummary artifact={artifact} compact onOpenPath={onOpenPath} />
          </button>
        ) : null}

        {workspaceFiles.length > 0 ? (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold uppercase text-slate-500">关键工作区文件</h4>
            {workspaceFiles.map((file) => (
              <button
                key={file.relative_path}
                type="button"
                onClick={() => onOpenPath(file.relative_path)}
                className="block w-full rounded border border-mars-border bg-mars-panel/25 p-2 text-left hover:bg-mars-panel/45"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-xs text-slate-200">{file.relative_path}</span>
                  <span className="text-[10px] text-slate-500">
                    {Math.ceil(file.size_bytes / 1024)} KB
                  </span>
                </div>
                <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-[11px] leading-relaxed text-slate-400">
                  {file.text.slice(0, 360)}
                </p>
              </button>
            ))}
          </div>
        ) : null}

        {treeFiles.length > 0 ? (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold uppercase text-slate-500">全部沉淀文件</h4>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {treeFiles.slice(0, 80).map((entry) => (
                <button
                  key={entry.relative_path}
                  type="button"
                  onClick={() => onOpenPath(entry.relative_path)}
                  className="truncate rounded border border-mars-border bg-mars-panel/20 px-2 py-1.5 text-left font-mono text-[11px] text-slate-300 hover:bg-mars-panel/45"
                >
                  {entry.relative_path}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {!artifact && workspaceFiles.length === 0 && treeFiles.length === 0 ? (
          <div className="rounded border border-mars-border bg-mars-panel/25 p-3 text-xs text-slate-500">
            当前 Agent 还没有写入产物或工作区文件。
          </div>
        ) : null}
      </div>
    </section>
  );
}

type PatchLineKind = "add" | "delete" | "hunk" | "context";

type PatchLineView = {
  kind: PatchLineKind;
  text: string;
};

type PatchFileView = {
  path: string;
  oldPath: string;
  newPath: string;
  insertions: number;
  deletions: number;
  lines: PatchLineView[];
};

type CodingChangeFileSummary = {
  path: string;
  type: string;
  risk: string;
  insertions?: number;
  deletions?: number;
};

type CodingChangeSummaryData = {
  fileCount: number;
  insertions: number;
  deletions: number;
  files: CodingChangeFileSummary[];
  diffPath: string;
};

function CodingChangeSummary({
  artifact,
  patch,
  compact = false,
  onOpenPath,
}: {
  artifact: ArtifactView | null;
  patch?: PatchView | null;
  compact?: boolean;
  onOpenPath: (path: string) => void;
}): JSX.Element | null {
  const patchFiles = patch ? parsePatchFiles(patch.text) : [];
  const summary = codingChangeSummaryFromArtifact(artifact, patchFiles);
  if (!summary) return null;
  const tone =
    summary.fileCount > 0
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-100"
      : "border-amber-500/30 bg-amber-500/10 text-amber-100";
  if (compact) {
    return (
      <span className={`mt-2 inline-flex max-w-full items-center gap-2 rounded-full border px-2 py-0.5 text-[11px] ${tone}`}>
        <span>{summary.fileCount} 个文件已更改</span>
        <span className="font-mono text-emerald-300">+{summary.insertions}</span>
        <span className="font-mono text-red-300">-{summary.deletions}</span>
      </span>
    );
  }
  return (
    <section className={`rounded border p-3 ${tone}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="font-medium">{summary.fileCount} 个文件已更改</span>
          <span className="font-mono text-emerald-300">+{summary.insertions}</span>
          <span className="font-mono text-red-300">-{summary.deletions}</span>
        </div>
        {summary.diffPath ? (
          <button
            type="button"
            onClick={() => onOpenPath(summary.diffPath)}
            className="rounded border border-emerald-500/40 px-2 py-1 text-xs font-medium hover:bg-emerald-500/15"
          >
            打开 diff
          </button>
        ) : null}
      </div>
      {summary.files.length > 0 ? (
        <div className="mt-3 grid gap-1.5">
          {summary.files.slice(0, 8).map((file) => (
            <div
              key={`${file.path}:${file.type}`}
              className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded border border-emerald-500/15 bg-mars-bg/55 px-2 py-1.5"
              title={`${file.type}${file.risk ? ` · ${file.risk}` : ""}`}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="truncate font-mono text-[11px] text-slate-100">{file.path}</span>
                <span className="rounded bg-slate-500/15 px-1.5 py-0.5 text-[10px] text-slate-400">
                  {file.type}
                </span>
                {file.risk ? (
                  <span className="rounded border border-amber-400/25 px-1.5 py-0.5 text-[10px] text-amber-200">
                    {file.risk}
                  </span>
                ) : null}
              </div>
              {file.insertions !== undefined || file.deletions !== undefined ? (
                <div className="flex shrink-0 items-center gap-2 font-mono text-[11px]">
                  <span className="text-emerald-300">+{file.insertions ?? 0}</span>
                  <span className="text-red-300">-{file.deletions ?? 0}</span>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function codingChangeSummaryFromArtifact(
  artifact: ArtifactView | null,
  patchFiles: PatchFileView[] = [],
): CodingChangeSummaryData | null {
  if (!artifact || artifact.agent_dir !== "coding") return null;
  const backend = asRecord(artifact.metadata["coding_backend"]);
  const stats = asRecord(backend["diff_stats"]);
  const patchByPath = new Map(patchFiles.map((file) => [file.path, file]));
  const files = asRecordList(artifact.metadata["files_changed"]).map((file) => {
    const path = metaText(file, "path");
    const patchFile = patchByPath.get(path) ?? findPatchFileByPath(patchFiles, path);
    return {
      path,
      type: metaText(file, "type", "modified"),
      risk: metaText(file, "risk"),
      insertions: patchFile?.insertions,
      deletions: patchFile?.deletions,
    };
  }).filter((file) => file.path);
  const knownPaths = new Set(files.map((file) => file.path));
  for (const patchFile of patchFiles) {
    if (knownPaths.has(patchFile.path)) continue;
    files.push({
      path: patchFile.path,
      type: "modified",
      risk: "",
      insertions: patchFile.insertions,
      deletions: patchFile.deletions,
    });
  }
  const patchInsertions = patchFiles.reduce((total, file) => total + file.insertions, 0);
  const patchDeletions = patchFiles.reduce((total, file) => total + file.deletions, 0);
  const fileCount = numberFromUnknown(stats["files_changed"], files.length);
  const insertions = numberFromUnknown(stats["insertions"], patchInsertions);
  const deletions = numberFromUnknown(stats["deletions"], patchDeletions);
  const diffPath = workspaceRelativeForAgent(
    metaText(backend, "diff"),
    artifact.agent_dir,
  );
  if (fileCount <= 0 && insertions <= 0 && deletions <= 0 && !diffPath) {
    return null;
  }
  return { fileCount, insertions, deletions, files, diffPath };
}

function findPatchFileByPath(files: PatchFileView[], path: string): PatchFileView | undefined {
  if (!path) return undefined;
  return files.find((file) => file.path.endsWith(`/${path}`) || path.endsWith(`/${file.path}`));
}

function parsePatchFiles(text: string): PatchFileView[] {
  const files: PatchFileView[] = [];
  let pendingOldPath = "";
  let current: PatchFileView | null = null;
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("--- ")) {
      pendingOldPath = normalizePatchPath(line.slice(4));
      continue;
    }
    if (line.startsWith("+++ ")) {
      const newPath = normalizePatchPath(line.slice(4));
      const path = newPath === "/dev/null" ? pendingOldPath : newPath;
      current = {
        path,
        oldPath: pendingOldPath,
        newPath,
        insertions: 0,
        deletions: 0,
        lines: [],
      };
      files.push(current);
      pendingOldPath = "";
      continue;
    }
    if (!current) continue;
    if (line.startsWith("@@")) {
      current.lines.push({ kind: "hunk", text: line });
    } else if (line.startsWith("+")) {
      current.insertions += 1;
      current.lines.push({ kind: "add", text: line });
    } else if (line.startsWith("-")) {
      current.deletions += 1;
      current.lines.push({ kind: "delete", text: line });
    } else {
      current.lines.push({ kind: "context", text: line });
    }
  }
  return files.filter((file) => file.path || file.lines.length > 0);
}

function normalizePatchPath(path: string): string {
  const compactPath = path.trim().split(/\s+/)[0] ?? "";
  if (compactPath === "/dev/null") return compactPath;
  return compactPath.replace(/^a\//, "").replace(/^b\//, "");
}

function patchVersionFromArtifact(artifact: ArtifactView | null): string {
  if (!artifact || artifact.agent_dir !== "coding") return "";
  const backend = asRecord(artifact.metadata["coding_backend"]);
  const diffPath = metaText(backend, "diff");
  const match = /patch\.(v\d+)\.diff$/.exec(diffPath);
  return match?.[1] ?? "";
}

function workspaceRelativeForAgent(path: string, agentDir: string): string {
  if (!path || path.startsWith("/") || path.includes("..")) return "";
  const prefix = `${agentDir}/`;
  return path.startsWith(prefix) ? path.slice(prefix.length) : path;
}

function numberFromUnknown(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function WorkbenchContextManifestPreview({
  manifest,
  manifestId,
  artifact,
  workspaceFiles,
}: {
  manifest: ContextManifestV2 | null;
  manifestId: string;
  artifact: ArtifactView | null;
  workspaceFiles: WorkspaceFileView[];
}): JSX.Element {
  if (!manifest) {
    return (
      <section className="rounded border border-mars-border bg-mars-bg p-4 text-sm text-slate-400">
        正在读取 {manifestId}…
      </section>
    );
  }
  const upstreamSegments = manifest.segments.filter((segment) => segment.kind === "upstream");
  const selfContextSegments = manifest.segments.filter((segment) => segment.kind === "self_context");
  const currentAgentArtifact = artifact?.agent_dir === manifest.agent ? artifact : null;
  const currentAgentWorkspaceFiles = artifact?.agent_dir === manifest.agent ? workspaceFiles : [];
  return (
    <section className="overflow-hidden rounded border border-mars-border bg-mars-bg">
      <div className="border-b border-mars-border px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-mars-accent/80 px-2 py-0.5 text-[10px] font-semibold uppercase text-white">
            {manifest.agent}
          </span>
          <span className="truncate font-mono text-xs text-slate-300">{manifest.node_key}</span>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          {manifest.purpose} · {manifest.segments.length} segments · tokens {manifest.budget.used}/{manifest.budget.max}
        </p>
      </div>
      <div className="max-h-[620px] space-y-4 overflow-auto p-3">
        <section>
          <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">上游传入</h4>
          <div className="space-y-2">
            {upstreamSegments.length > 0 ? (
              upstreamSegments.map((segment) => (
                <ContextSegmentCard key={segment.id} segment={segment} tone="handoff" />
              ))
            ) : (
              <p className="rounded border border-mars-border bg-mars-panel/25 p-2 text-xs text-slate-500">
                这次调用没有 upstream handoff 段，只有系统/项目/任务/工具等上下文。
              </p>
            )}
          </div>
        </section>

        {currentAgentArtifact || currentAgentWorkspaceFiles.length > 0 ? (
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">本 Agent 当前产出</h4>
            <div className="space-y-2">
              {currentAgentArtifact ? (
                <div className="rounded border border-cyan-500/30 bg-cyan-500/10 p-2">
                  <div className="font-mono text-xs text-cyan-100">
                    {currentAgentArtifact.stem}.{currentAgentArtifact.version}.md
                  </div>
                  <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-xs leading-relaxed text-slate-300">
                    {splitFrontmatter(currentAgentArtifact.text).body.slice(0, 700)}
                  </p>
                </div>
              ) : null}
              {currentAgentWorkspaceFiles.slice(0, 4).map((file) => (
                <div key={file.relative_path} className="rounded border border-mars-border bg-mars-panel/25 p-2">
                  <div className="font-mono text-xs text-slate-200">{file.relative_path}</div>
                  <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-[11px] leading-relaxed text-slate-400">
                    {file.text.slice(0, 360)}
                  </p>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {selfContextSegments.length > 0 ? (
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">自上下文</h4>
            <div className="space-y-2">
              {selfContextSegments.map((segment) => (
                <ContextSegmentCard key={segment.id} segment={segment} />
              ))}
            </div>
          </section>
        ) : null}

        <section>
          <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">消息预览</h4>
          <div className="space-y-2">
            {manifest.messages_preview.map((message, index) => (
              <div key={`${message.role}:${index}`} className="rounded border border-mars-border bg-mars-panel/25 p-2">
                <div className="mb-1 text-[10px] uppercase text-slate-500">{message.role}</div>
                <pre className="max-h-[180px] whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-slate-300">
                  {message.content}
                </pre>
              </div>
            ))}
          </div>
        </section>
        <section>
          <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">上下文段</h4>
          <div className="space-y-2">
            {manifest.segments.map((segment) => (
              <ContextSegmentCard key={segment.id} segment={segment} />
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}

function ContextSegmentCard({
  segment,
  tone = "default",
}: {
  segment: ContextSegment;
  tone?: "default" | "handoff";
}): JSX.Element {
  const toneClass =
    tone === "handoff"
      ? "border-emerald-500/30 bg-emerald-500/10"
      : "border-mars-border bg-mars-panel/25";
  return (
    <div className={`rounded border p-2 ${toneClass}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-xs text-slate-200">{segment.title || segment.id}</span>
        <span className="text-[10px] text-slate-500">
          {segment.kind} · {segment.tokens_estimated} tokens
        </span>
      </div>
      <p className="mt-1 truncate text-[10px] text-slate-500">{segment.source_ref}</p>
      <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-300">
        {segment.text_preview}
      </p>
    </div>
  );
}

function contextSourceLabel(file: AgentContextFile): string {
  if (file.source === "runtime_code") return "运行时代码";
  if (file.source === "system_default") return "系统默认";
  if (file.source === "user_upload") return "用户上传";
  return file.source || file.category;
}

function WorkbenchProcessPanel({
  state,
  copy,
  evaluation,
  activityRows,
  warnings,
  literature,
  debateRows,
  debate,
  spans,
  tools,
  onOpenContext,
  onRetryAgent,
  onOpenTimeline,
  onOpenCommander,
}: {
  state: string;
  copy: { title: string; purpose: string; artifact: string; handoff: string };
  evaluation: ArtifactEvaluationSummary | null;
  activityRows: WorkspaceActivityRow[];
  warnings: string[];
  literature: LiteratureSummaryRow[];
  debateRows: DebateHighlightRow[];
  debate: DebateTranscript | null;
  spans: TraceSpan[];
  tools: ReactToolStep[];
  onOpenContext: () => void;
  onRetryAgent: () => Promise<void>;
  onOpenTimeline: () => void;
  onOpenCommander: () => void;
}): JSX.Element {
  return (
    <div className="flex max-h-[720px] min-h-0 flex-col overflow-hidden">
      <div className="border-b border-mars-border px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase text-slate-400">Agent</h3>
          <StateBadge state={state} />
        </div>
        <h2 className="mt-2 text-sm font-semibold text-slate-100">{copy.title}</h2>
        <p className="mt-1 text-[11px] leading-relaxed text-slate-500">{copy.handoff}</p>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onOpenContext}
            className="rounded border border-mars-border bg-mars-bg/60 px-2 py-1 text-[11px] text-slate-200 hover:bg-mars-panel"
          >
            上下文
          </button>
          <button
            type="button"
            onClick={onOpenTimeline}
            className="rounded border border-mars-border bg-mars-bg/60 px-2 py-1 text-[11px] text-slate-200 hover:bg-mars-panel"
          >
            执行流
          </button>
          {state === "failed" ? (
            <button
              type="button"
              onClick={() => void onRetryAgent()}
              className="rounded bg-emerald-500/80 px-2 py-1 text-[11px] text-white hover:bg-emerald-500"
            >
              重试 Agent
            </button>
          ) : null}
          {state === "failed" ? (
            <button
              type="button"
              onClick={onOpenCommander}
              className="rounded bg-cyan-500/80 px-2 py-1 text-[11px] text-white hover:bg-cyan-500"
            >
              主控诊断
            </button>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
        <section>
          <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">质量</h4>
          <div className="rounded border border-mars-border bg-mars-bg/45 p-2">
            <div className="text-sm font-medium text-slate-100">
              {evaluation
                ? `${decisionLabel(evaluation.decision)} · 分数 ${formatScore(evaluation.overall_score)}`
                : "等待评价"}
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
              {evaluation?.top_findings?.[0]?.message ?? "产物生成后会显示 evaluator 结论和阻塞项。"}
            </p>
          </div>
        </section>

        {warnings.length > 0 ? (
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">需要注意</h4>
            <div className="flex flex-wrap gap-1.5">
              {warnings.map((warning) => (
                <span
                  key={warning}
                  className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-100"
                >
                  {qualityWarningLabel(warning)}
                </span>
              ))}
            </div>
          </section>
        ) : null}

        <section>
          <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">过程流</h4>
          <div className="space-y-1.5">
            {activityRows.length > 0 ? (
              activityRows.map((row) => (
                <div key={row.key} className="rounded border border-mars-border/70 bg-mars-bg/40 px-2 py-1.5">
                  <div className={`text-[11px] font-medium ${workspaceToneClass(row.tone)}`}>
                    {row.label}
                  </div>
                  <p className="mt-0.5 break-words text-[11px] leading-relaxed text-slate-400">
                    {row.detail}
                  </p>
                </div>
              ))
            ) : (
              <p className="text-xs text-slate-500">暂无过程事件。</p>
            )}
          </div>
        </section>

        {literature.length > 0 ? (
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">论文检索</h4>
            <div className="space-y-2">
              {literature.map((summary) => (
                <div key={summary.key} className="rounded border border-mars-border/70 bg-mars-bg/40 px-2 py-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-[10px] text-slate-300">
                      {summary.followUpOf ? "follow-up" : "initial"}
                    </span>
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${literatureStatusClass(summary.status)}`}>
                      {summary.status}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {summary.relevantHits}/{summary.totalHits}
                    </span>
                  </div>
                  {summary.query ? (
                    <p className="mt-1 break-words font-mono text-[10px] text-slate-500">
                      {summary.query}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {debateRows.length > 0 || debate?.exists ? (
          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">讨论</h4>
            <div className="space-y-1.5">
              {debateRows.map((row) => (
                <p key={row.key} className="text-[11px] leading-relaxed text-slate-400">
                  <span className="mr-1 text-slate-500">{row.label}</span>
                  {row.detail}
                </p>
              ))}
              {debate?.exists ? (
                <p className="font-mono text-[10px] text-slate-500">{debate.path}</p>
              ) : null}
            </div>
          </section>
        ) : null}

        <section>
          <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">Trace</h4>
          <p className="text-[11px] text-slate-500">
            spans={spans.length} · tools={tools.length}
          </p>
        </section>
      </div>
    </div>
  );
}

function AgentWorkSummary({
  run,
  agent,
  artifact,
  evaluation,
  debate,
  workspaceFiles,
  workspaceTree,
  trace,
  toolCalls,
  events,
  onOpenContext,
  onOpenTimeline,
  onOpenCommander,
}: {
  run: RunDetail | null;
  agent: string;
  artifact: ArtifactView | null;
  evaluation: ArtifactEvaluationSummary | null;
  debate: DebateTranscript | null;
  workspaceFiles: WorkspaceFileView[];
  workspaceTree: WorkspaceTreeView | null;
  trace: TraceManifest | null;
  toolCalls: ToolAuditEntry[];
  events: WSMessage[];
  onOpenContext: () => void;
  onOpenTimeline: () => void;
  onOpenCommander: () => void;
}): JSX.Element {
  const state = run?.states[agent] ?? "pending";
  const copy = AGENT_WORK_COPY[agent] ?? {
    title: "Agent 工作区",
    purpose: "查看当前 Agent 的产物、上下文、评估和运行证据。",
    artifact: "artifact.v1",
    handoff: "批准后进入下一步",
  };
  const spans = trace?.spans.filter((span) => spanBelongsToAgent(span, agent)) ?? [];
  const tools = buildReactToolSteps(toolCalls, agent);
  const agentEvents = events.filter((event) => metaText(asRecord(event.payload), "agent") === agent);
  const latestSpan = spans.at(-1);
  const latestEvent = agentEvents.at(-1);
  const stateTone = agentSummaryTone(state);
  const stateMessage = agentSummaryMessage({
    state,
    agent,
    artifactName: copy.artifact,
    hasArtifact: Boolean(artifact),
  });
  const artifactStatus = artifact
    ? `${artifact.version} · ${artifact.valid ? "Schema 通过" : "Schema 异常"}`
    : "暂无可审核产物";
  const evaluationStatus = evaluation
    ? `${decisionLabel(evaluation.decision)} · 分数 ${formatScore(evaluation.overall_score)}`
    : artifact
      ? "等待评价"
      : "无产物可评价";
  const evidenceStatus =
    latestSpan || latestEvent
      ? latestSpan
        ? `${traceSpanLabel(latestSpan)} · ${spanDuration(latestSpan)}`
        : metaText(asRecord(latestEvent?.payload), "event", "状态事件")
      : "暂无新增事件";

  return (
    <section className={`rounded border ${stateTone.border} ${stateTone.bg} p-3`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${stateTone.badge}`}>
              {agentLabel(agent)}
            </span>
            <StateBadge state={state} />
            <span className="rounded bg-mars-bg/70 px-2 py-0.5 font-mono text-[10px] text-slate-400">
              {copy.artifact}
            </span>
          </div>
          <h2 className="mt-2 text-base font-semibold text-slate-100">
            {copy.title}
          </h2>
          <p className="mt-1 max-w-4xl text-xs leading-relaxed text-slate-300">
            {stateMessage}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
            {copy.purpose} · {copy.handoff}
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap gap-2">
          {state === "failed" ? (
            <button
              type="button"
              onClick={onOpenCommander}
              className="rounded bg-cyan-500/80 px-3 py-1.5 text-xs font-medium text-white hover:bg-cyan-500"
            >
              查看主控诊断
            </button>
          ) : null}
          <button
            type="button"
            onClick={onOpenContext}
            className="rounded border border-mars-border bg-mars-bg/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-mars-panel"
          >
            上下文
          </button>
          <button
            type="button"
            onClick={onOpenTimeline}
            className="rounded border border-mars-border bg-mars-bg/60 px-3 py-1.5 text-xs text-slate-200 hover:bg-mars-panel"
          >
            执行流
          </button>
        </div>
      </div>

      <div className="mt-3 grid gap-2 md:grid-cols-3">
        <AgentSummaryMetric
          label="产物"
          value={artifactStatus}
          detail={artifact?.path ?? "该 Agent 还没有写出可审阅的 Markdown 产物。"}
          tone={artifact ? (artifact.valid ? "ok" : "danger") : "muted"}
        />
        <AgentSummaryMetric
          label="质量"
          value={evaluationStatus}
          detail={
            evaluation?.top_findings?.[0]?.message ??
            (artifact ? "产物生成后会显示 evaluator 结论和阻塞项。" : "等待产物生成后再评价。")
          }
          tone={evaluation?.blocking ? "danger" : evaluation ? "ok" : "muted"}
        />
        <AgentSummaryMetric
          label="证据"
          value={evidenceStatus}
          detail={`辩论 ${debate?.exists ? "已记录" : "无"} · Trace ${spans.length} · Tool ${tools.length}`}
          tone={state === "failed" ? "warn" : spans.length > 0 || tools.length > 0 ? "info" : "muted"}
        />
      </div>
      <AgentWorkspaceEvidencePanel
        agent={agent}
        artifact={artifact}
        debate={debate}
        workspaceFiles={workspaceFiles}
        workspaceTree={workspaceTree}
        spans={spans}
        tools={tools}
        events={agentEvents}
      />
    </section>
  );
}

function AgentWorkspaceEvidencePanel({
  agent,
  artifact,
  debate,
  workspaceFiles,
  workspaceTree,
  spans,
  tools,
  events,
}: {
  agent: string;
  artifact: ArtifactView | null;
  debate: DebateTranscript | null;
  workspaceFiles: WorkspaceFileView[];
  workspaceTree: WorkspaceTreeView | null;
  spans: TraceSpan[];
  tools: ReactToolStep[];
  events: WSMessage[];
}): JSX.Element | null {
  const activityRows = buildWorkspaceActivityRows({
    agent,
    artifact,
    debate,
    workspaceFiles,
    spans,
    tools,
    events,
  });
  const literature = literatureSummariesFromWorkspace(workspaceFiles);
  const warnings = workspaceQualityWarnings(artifact, literature);
  const debateRows = debateHighlights(artifact, debate);
  const researchSummary = workspaceFiles.find(
    (file) => file.relative_path === "research/research_summary.v1.md" && file.exists,
  );
  const defaultFilePath =
    workspaceTree?.entries.find((entry) => entry.kind === "file" && entry.relative_path === "research/research_summary.v1.md")?.relative_path ??
    workspaceTree?.entries.find((entry) => entry.kind === "file" && entry.relative_path === "research/tool_results.v1.json")?.relative_path ??
    workspaceTree?.entries.find((entry) => entry.kind === "file")?.relative_path ??
    "";
  const [selectedPath, setSelectedPath] = useState(defaultFilePath);
  const [selectedFile, setSelectedFile] = useState<WorkspaceFileView | null>(null);

  useEffect(() => {
    if (!selectedPath && defaultFilePath) {
      setSelectedPath(defaultFilePath);
    }
  }, [defaultFilePath, selectedPath]);

  useEffect(() => {
    let alive = true;
    setSelectedFile(null);
    if (!workspaceTree || !selectedPath) {
      return () => {
        alive = false;
      };
    }
    void getWorkspaceFile(workspaceTree.run_id, workspaceTree.agent_dir, selectedPath)
      .then((file) => {
        if (alive) setSelectedFile(file);
      })
      .catch(() => {
        if (alive) setSelectedFile(null);
      });
    return () => {
      alive = false;
    };
  }, [selectedPath, workspaceTree]);

  const hasSignal =
    activityRows.length > 0 ||
    Boolean(workspaceTree?.entries.length) ||
    literature.length > 0 ||
    warnings.length > 0 ||
    debateRows.length > 0 ||
    Boolean(researchSummary);

  if (!hasSignal) return null;

  return (
    <div className="mt-3 border-t border-mars-border/80 pt-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">工作区动态</h3>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500">
            files={workspaceTree?.entries.filter((entry) => entry.kind === "file").length ?? workspaceFiles.length} tools={tools.length} spans={spans.length}
          </p>
        </div>
        {artifact ? (
          <span className="max-w-full truncate rounded bg-mars-bg/70 px-2 py-1 font-mono text-[10px] text-slate-400">
            {artifact.path}
          </span>
        ) : null}
      </div>

      {warnings.length > 0 ? (
        <div className="mb-3 flex flex-wrap gap-2">
          {warnings.map((warning) => (
            <span
              key={warning}
              className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-100"
            >
              {qualityWarningLabel(warning)}
            </span>
          ))}
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <div className="min-w-0">
          <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">
            运行时间线
          </h4>
          <div className="divide-y divide-mars-border/70 border-y border-mars-border/70">
            {activityRows.length > 0 ? (
              activityRows.map((row) => (
                <div key={row.key} className="grid gap-1 py-2 md:grid-cols-[128px_1fr]">
                  <span className={`text-xs font-medium ${workspaceToneClass(row.tone)}`}>
                    {row.label}
                  </span>
                  <span className="min-w-0 break-words text-xs leading-relaxed text-slate-300">
                    {row.detail}
                  </span>
                </div>
              ))
            ) : (
              <div className="py-2 text-xs text-slate-500">暂无过程事件。</div>
            )}
          </div>
        </div>

        <div className="min-w-0">
          <WorkspaceFolderBrowser
            tree={workspaceTree}
            selectedPath={selectedPath}
            selectedFile={selectedFile}
            onSelect={setSelectedPath}
          />
        </div>
      </div>

      {literature.length > 0 ? (
      <div className="mt-4">
          <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">
            论文命中
          </h4>
          <div className="divide-y divide-mars-border/70 border-y border-mars-border/70">
            {literature.map((summary) => (
              <div key={summary.key} className="py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] text-slate-300">
                    {summary.tool}
                  </span>
                  <span className="rounded bg-mars-bg/70 px-1.5 py-0.5 text-[10px] text-slate-400">
                    {summary.followUpOf ? "follow-up" : "initial"}
                  </span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] ${literatureStatusClass(summary.status)}`}>
                    {summary.status}
                  </span>
                  <span className="font-mono text-[10px] text-slate-500">
                    relevant={summary.relevantHits}/{summary.totalHits}
                  </span>
                </div>
                {summary.query ? (
                  <p className="mt-1 break-words font-mono text-[10px] text-slate-500">
                    query={summary.query}
                  </p>
                ) : null}
                <div className="mt-2 space-y-1">
                  {summary.hits.slice(0, 4).map((hit) => (
                    <div
                      key={`${summary.key}-${hit.index}`}
                      className="grid gap-1 text-[11px] md:grid-cols-[72px_1fr]"
                    >
                      <span className={hit.relevant ? "text-emerald-300" : "text-amber-300"}>
                        {hit.relevant ? "相关" : "低相关"}
                      </span>
                      <span className="min-w-0 break-words text-slate-400">
                        {hit.title || hit.url || "untitled"}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {debateRows.length > 0 ? (
        <div className="mt-4">
          <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">
            讨论摘录
          </h4>
          <div className="space-y-1 border-y border-mars-border/70 py-2">
            {debateRows.map((row) => (
              <p key={row.key} className="text-xs leading-relaxed text-slate-300">
                <span className="mr-2 text-slate-500">{row.label}</span>
                {row.detail}
              </p>
            ))}
          </div>
        </div>
      ) : null}

      {researchSummary ? (
        <details className="mt-4 border-t border-mars-border/70 pt-3">
          <summary className="cursor-pointer text-xs font-semibold uppercase text-slate-500 hover:text-slate-300">
            research_summary.v1.md
          </summary>
          <div className="mt-3 max-h-80 overflow-auto pr-2 text-xs leading-relaxed text-slate-300">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {researchSummary.text.slice(0, 5000)}
            </ReactMarkdown>
          </div>
        </details>
      ) : null}
    </div>
  );
}

type WorkspaceTone = "ok" | "info" | "warn" | "danger" | "muted";

type WorkspaceActivityRow = {
  key: string;
  label: string;
  detail: string;
  tone: WorkspaceTone;
};

type LiteratureHitRow = {
  index: number;
  title: string;
  url: string;
  relevant: boolean;
  matchedConcepts: string[];
};

type LiteratureSummaryRow = {
  key: string;
  tool: string;
  query: string;
  followUpOf: string;
  status: string;
  totalHits: number;
  relevantHits: number;
  hits: LiteratureHitRow[];
};

type DebateHighlightRow = {
  key: string;
  label: string;
  detail: string;
};

function WorkspaceFolderBrowser({
  tree,
  selectedPath,
  selectedFile,
  onSelect,
}: {
  tree: WorkspaceTreeView | null;
  selectedPath: string;
  selectedFile: WorkspaceFileView | null;
  onSelect: (path: string) => void;
}): JSX.Element {
  const entries = tree?.entries ?? [];
  const files = entries.filter((entry) => entry.kind === "file");
  return (
    <div>
      <div className="mb-2 flex min-w-0 items-center justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase text-slate-500">
          工作区文件夹
        </h4>
        {tree ? (
          <span className="truncate font-mono text-[10px] text-slate-500">
            {files.length} files · {tree.root_path}
          </span>
        ) : null}
      </div>
      <div className="grid gap-3 lg:grid-cols-[0.82fr_1.18fr]">
        <div className="max-h-96 overflow-auto border-y border-mars-border/70 py-1">
          {entries.length > 0 ? (
            entries.slice(0, 220).map((entry) => {
              const depth = Math.max(0, entry.relative_path.split("/").length - 1);
              const active = entry.relative_path === selectedPath;
              const isFile = entry.kind === "file";
              return (
                <button
                  key={entry.relative_path}
                  type="button"
                  disabled={!isFile}
                  onClick={() => onSelect(entry.relative_path)}
                  className={`flex w-full min-w-0 items-center gap-1 rounded px-2 py-1 text-left font-mono text-[11px] ${
                    active
                      ? "bg-cyan-500/15 text-cyan-100"
                      : isFile
                        ? "text-slate-300 hover:bg-mars-bg/70"
                        : "cursor-default text-slate-500"
                  }`}
                  style={{ paddingLeft: `${8 + depth * 14}px` }}
                >
                  <span className="shrink-0 text-slate-500">
                    {isFile ? "file" : "dir"}
                  </span>
                  <span className="truncate">{entry.name}</span>
                </button>
              );
            })
          ) : (
            <div className="py-3 text-xs text-slate-500">暂无工作区文件。</div>
          )}
        </div>
        <div className="min-w-0 border-y border-mars-border/70 py-2">
          {selectedFile?.exists ? (
            <>
              <div className="mb-2 flex min-w-0 flex-wrap items-center justify-between gap-2">
                <span className="truncate font-mono text-[11px] text-slate-300">
                  {selectedFile.relative_path}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-slate-500">
                  {Math.ceil(selectedFile.size_bytes / 1024)} KB
                </span>
              </div>
              <div className="max-h-96 overflow-auto rounded bg-mars-bg/60 p-3 text-xs leading-relaxed text-slate-300">
                {selectedFile.content_type === "text/markdown" ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {selectedFile.text.slice(0, 12000)}
                  </ReactMarkdown>
                ) : (
                  <pre className="whitespace-pre-wrap break-words font-mono text-[11px]">
                    {selectedFile.text.slice(0, 12000)}
                  </pre>
                )}
              </div>
            </>
          ) : (
            <div className="py-3 text-xs text-slate-500">
              选择左侧文件查看内容。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function workspaceFilePathsForArtifact(artifact: ArtifactView): string[] {
  const paths = new Set<string>();
  const researchArtifacts = asRecord(artifact.metadata["research_artifacts"]);
  Object.values(researchArtifacts).forEach((value) => {
    if (typeof value === "string" && isWorkspaceRelativePath(value)) {
      paths.add(value);
    }
  });
  const debatePath = metaText(artifact.metadata, "debate_transcript_path");
  if (isWorkspaceRelativePath(debatePath)) {
    paths.add(debatePath);
  }
  return [...paths].slice(0, 12);
}

function isWorkspaceRelativePath(path: string): boolean {
  return Boolean(path) && !path.startsWith("/") && !path.includes("..");
}

function buildWorkspaceActivityRows({
  agent,
  artifact,
  debate,
  workspaceFiles,
  spans,
  tools,
  events,
}: {
  agent: string;
  artifact: ArtifactView | null;
  debate: DebateTranscript | null;
  workspaceFiles: WorkspaceFileView[];
  spans: TraceSpan[];
  tools: ReactToolStep[];
  events: WSMessage[];
}): WorkspaceActivityRow[] {
  const rows: WorkspaceActivityRow[] = [];
  if (artifact) {
    rows.push({
      key: "artifact",
      label: "产物写入",
      detail: `${artifact.stem}.${artifact.version}.md · ${artifact.valid ? "Schema 通过" : "Schema 异常"}`,
      tone: artifact.valid ? "ok" : "danger",
    });
  }
  const existingFiles = workspaceFiles.filter((file) => file.exists);
  if (existingFiles.length > 0) {
    rows.push({
      key: "workspace-files",
      label: "文件沉淀",
      detail: existingFiles.map((file) => file.relative_path).slice(0, 4).join(" · "),
      tone: "info",
    });
  }
  tools.slice(-4).forEach((tool) => {
    rows.push({
      key: `tool-${tool.id}`,
      label: tool.tool,
      detail: toolObservationSummary(tool),
      tone: tool.status === "failed" || tool.status === "error" ? "danger" : "info",
    });
  });
  spans.slice(-3).forEach((span) => {
    rows.push({
      key: `span-${span.span_id}`,
      label: traceSpanLabel(span),
      detail: `${span.status || "running"} · ${spanDuration(span)}`,
      tone: span.status === "error" ? "danger" : "muted",
    });
  });
  events.slice(-2).forEach((event, index) => {
    rows.push({
      key: `event-${index}-${event.channel}`,
      label: event.channel,
      detail: metaText(event.payload, "event", metaText(event.payload, "status", agent)),
      tone: "muted",
    });
  });
  if (debate?.exists) {
    rows.push({
      key: "debate",
      label: "多模型讨论",
      detail: `${debate.path ?? "debate_transcript.v1.md"} · ${debate.text.split("\n").filter((line) => line.startsWith("## ")).length} 段`,
      tone: "info",
    });
  }
  return rows.slice(0, 9);
}

function literatureSummariesFromWorkspace(
  workspaceFiles: WorkspaceFileView[],
): LiteratureSummaryRow[] {
  const toolResultsFile = workspaceFiles.find(
    (file) => file.relative_path === "research/tool_results.v1.json" && file.exists,
  );
  const raw = parseWorkspaceJson(toolResultsFile);
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item, index) => literatureSummaryFromObservation(asRecord(item), index))
    .filter((item): item is LiteratureSummaryRow => item !== null);
}

function literatureSummaryFromObservation(
  observation: Record<string, unknown>,
  index: number,
): LiteratureSummaryRow | null {
  const tool = metaText(observation, "tool", "tool");
  if (tool !== "search.arxiv_search") return null;
  const args = asRecord(observation["args"]);
  const output = asRecord(observation["output"]);
  const query = metaText(args, "query", metaText(args, "q", metaText(output, "query")));
  const followUpOf = metaText(observation, "follow_up_of", metaText(args, "follow_up_of"));
  const quality = asRecord(asRecord(observation["quality"])["literature_relevance"]);
  const qualityHits = asRecordList(quality["hits"]);
  if (qualityHits.length > 0) {
    return {
      key: `${tool}-${index}`,
      tool,
      query,
      followUpOf,
      status: metaText(quality, "status", "unknown"),
      totalHits: Number(quality["total_hits"] ?? qualityHits.length),
      relevantHits: Number(quality["relevant_hits"] ?? 0),
      hits: qualityHits.map((hit, hitIndex) => ({
        index: Number(hit["index"] ?? hitIndex + 1),
        title: metaText(hit, "title"),
        url: metaText(hit, "url"),
        relevant: hit["relevant"] === true,
        matchedConcepts: asStringList(hit["matched_concepts"]),
      })),
    };
  }

  const hits = asRecordList(output["hits"]);
  const inferredHits = hits.map((hit, hitIndex) => {
    const relevance = inferLiteratureRelevance(hit);
    return {
      index: hitIndex + 1,
      title: metaText(hit, "title", metaText(hit, "id")),
      url: metaText(hit, "url", metaText(hit, "id")),
      relevant: relevance.relevant,
      matchedConcepts: relevance.matchedConcepts,
    };
  });
  const relevantHits = inferredHits.filter((hit) => hit.relevant).length;
  const status = relevantHits === 0 ? "no_relevant_hits" : relevantHits / inferredHits.length < 0.4 ? "low_relevance" : "pass";
  return {
    key: `${tool}-${index}`,
    tool,
    query,
    followUpOf,
    status,
    totalHits: inferredHits.length,
    relevantHits,
    hits: inferredHits,
  };
}

function inferLiteratureRelevance(hit: Record<string, unknown>): {
  relevant: boolean;
  matchedConcepts: string[];
} {
  const text = [
    metaText(hit, "title"),
    metaText(hit, "summary"),
    metaText(hit, "excerpt"),
    JSON.stringify(asRecord(hit["metadata"])),
  ].join(" ").toLowerCase();
  const groups = [
    ["passive_intermodulation", ["passive intermodulation", "intermodulation cancellation", "pim cancellation", "pimc"], 3],
    ["massive_mimo", ["massive mimo", "mimo", "multi-antenna", "antenna array"], 2],
    ["rf_nonlinearity", ["digital predistortion", "predistortion", "power amplifier", "rf", "nonlinear distortion"], 2],
    ["beam_layer_switching", ["beamforming", "beam switching", "layer switching", "fdd"], 1],
    ["pimc_modeling", ["memory polynomial", "volterra", "low-rank", "group convolution", "sparse routing", "mixture of experts"], 1],
  ] as const;
  let score = 0;
  const matchedConcepts: string[] = [];
  groups.forEach(([name, terms, weight]) => {
    if (terms.some((term) => text.includes(term))) {
      score += weight;
      matchedConcepts.push(name);
    }
  });
  const conceptSet = new Set(matchedConcepts);
  const relevant =
    (conceptSet.has("passive_intermodulation") &&
      (conceptSet.has("massive_mimo") ||
        conceptSet.has("rf_nonlinearity") ||
        conceptSet.has("beam_layer_switching"))) ||
    (score >= 4 && matchedConcepts.length >= 2 && (
      conceptSet.has("passive_intermodulation") || conceptSet.has("massive_mimo")
    ));
  return { relevant, matchedConcepts };
}

function workspaceQualityWarnings(
  artifact: ArtifactView | null,
  literature: LiteratureSummaryRow[],
): string[] {
  const warnings = new Set<string>();
  asStringList(artifact?.metadata["quality_warnings"]).forEach((warning) => warnings.add(warning));
  if (artifact && hasPlaceholderCitation(artifact.metadata["related_literature"])) {
    warnings.add("related_literature_placeholder");
  }
  const hasRelevantLiterature = literature.some((summary) => summary.status === "pass");
  const hasLowLiterature = literature.some((summary) =>
    ["low_relevance", "no_relevant_hits", "no_hits"].includes(summary.status),
  );
  if (hasLowLiterature && !hasRelevantLiterature) {
    warnings.add("literature_relevance_low");
  }
  return [...warnings];
}

function hasPlaceholderCitation(value: unknown): boolean {
  const text = JSON.stringify(value ?? "").toLowerCase();
  return ["1234567", "2103.00000", "0000.00000", "placeholder", "example.com", "待补", "占位"].some(
    (marker) => text.includes(marker),
  );
}

function debateHighlights(
  artifact: ArtifactView | null,
  debate: DebateTranscript | null,
): DebateHighlightRow[] {
  const rows: DebateHighlightRow[] = [];
  const summary = asRecord(artifact?.metadata["debate_summary"]);
  const consensus = metaText(summary, "consensus");
  if (consensus) {
    rows.push({ key: "consensus", label: "共识", detail: consensus });
  }
  asStringList(summary["disagreements"]).slice(0, 2).forEach((item, index) => {
    rows.push({ key: `disagreement-${index}`, label: "分歧", detail: item });
  });
  asStringList(summary["risks"]).slice(0, 2).forEach((item, index) => {
    rows.push({ key: `risk-${index}`, label: "风险", detail: item });
  });
  if (rows.length === 0 && debate?.exists) {
    debate.text
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("## "))
      .slice(0, 3)
      .forEach((line, index) => {
        rows.push({ key: `debate-line-${index}`, label: "记录", detail: line.replace(/^##\s*/, "") });
      });
  }
  return rows.slice(0, 5);
}

function parseWorkspaceJson(file: WorkspaceFileView | undefined): unknown {
  if (!file?.exists) return null;
  try {
    return JSON.parse(file.text);
  } catch {
    return null;
  }
}

function qualityWarningLabel(warning: string): string {
  const labels: Record<string, string> = {
    literature_relevance_low: "论文命中低相关",
    related_literature_placeholder: "引用疑似占位",
    evidence_refs_not_in_research_index: "证据引用未锚定",
    missing_evidence_refs: "缺少证据引用",
    hypothesis_not_testable: "假设不可证伪",
  };
  return labels[warning] ?? warning;
}

function workspaceToneClass(tone: WorkspaceTone): string {
  const classes: Record<WorkspaceTone, string> = {
    ok: "text-emerald-300",
    info: "text-cyan-300",
    warn: "text-amber-300",
    danger: "text-red-300",
    muted: "text-slate-500",
  };
  return classes[tone];
}

function literatureStatusClass(status: string): string {
  if (status === "pass") return "bg-emerald-500/15 text-emerald-200";
  if (status === "low_relevance" || status === "no_relevant_hits" || status === "no_hits") {
    return "bg-amber-500/15 text-amber-200";
  }
  return "bg-slate-700 text-slate-300";
}

function AgentSummaryMetric({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: "ok" | "info" | "warn" | "danger" | "muted";
}): JSX.Element {
  const toneClass: Record<"ok" | "info" | "warn" | "danger" | "muted", string> = {
    ok: "border-emerald-500/25 bg-emerald-500/10 text-emerald-100",
    info: "border-cyan-500/25 bg-cyan-500/10 text-cyan-100",
    warn: "border-amber-500/25 bg-amber-500/10 text-amber-100",
    danger: "border-red-500/30 bg-red-500/10 text-red-100",
    muted: "border-mars-border bg-mars-bg/55 text-slate-200",
  };
  return (
    <div className={`min-w-0 rounded border px-3 py-2 ${toneClass[tone]}`}>
      <p className="text-[10px] uppercase text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-medium">{value}</p>
      <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-slate-500">
        {detail}
      </p>
    </div>
  );
}

function agentSummaryTone(state: string): {
  border: string;
  bg: string;
  badge: string;
} {
  if (state === "failed") {
    return {
      border: "border-red-500/35",
      bg: "bg-red-500/10",
      badge: "bg-red-500/20 text-red-100",
    };
  }
  if (state === "waiting_review") {
    return {
      border: "border-fuchsia-500/35",
      bg: "bg-fuchsia-500/10",
      badge: "bg-fuchsia-500/20 text-fuchsia-100",
    };
  }
  if (state === "running") {
    return {
      border: "border-amber-500/35",
      bg: "bg-amber-500/10",
      badge: "bg-amber-500/20 text-amber-100",
    };
  }
  if (state === "done" || state === "approved") {
    return {
      border: "border-emerald-500/30",
      bg: "bg-emerald-500/10",
      badge: "bg-emerald-500/20 text-emerald-100",
    };
  }
  return {
    border: "border-mars-border",
    bg: "bg-mars-panel/45",
    badge: "bg-slate-700 text-slate-200",
  };
}

function agentSummaryMessage({
  state,
  agent,
  artifactName,
  hasArtifact,
}: {
  state: string;
  agent: string;
  artifactName: string;
  hasArtifact: boolean;
}): string {
  if (state === "failed" && !hasArtifact) {
    return `${agentLabel(agent)} 未生成 ${artifactName}。先看主控诊断确认失败原因，再决定是补上下文、重跑该 Agent，还是回退到上一阶段。`;
  }
  if (state === "failed") {
    return `${agentLabel(agent)} 已失败，但页面保留了最近产物和运行证据，建议先核对评价发现与执行流。`;
  }
  if (state === "running") {
    return `${agentLabel(agent)} 正在运行。这里会持续汇总产物、辩论记录、Trace 和工具调用。`;
  }
  if (state === "waiting_review") {
    return `${agentLabel(agent)} 已产出 ${artifactName}，需要人工审阅后才能交给下一阶段。`;
  }
  if (state === "done" || state === "approved") {
    return `${agentLabel(agent)} 已完成，当前产物可作为后续 Agent 的输入。`;
  }
  return `${agentLabel(agent)} 尚未开始。可以先检查上下文配置，再启动或等待上游 Agent 交接。`;
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
  const isFailed = agentState === "failed";

  if (debate?.exists && debate.text) {
    return (
      <>
        {isRunning ? (
          <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            {t("run.empty.runningWithDebate")}
          </div>
        ) : null}
        {isFailed ? (
          <div className="rounded border border-red-500/35 bg-red-500/10 px-3 py-2 text-xs leading-relaxed text-red-100">
            当前 Agent 已失败，尚未形成可审核产物。下面保留辩论转录用于定位失败点；更完整的回溯入口在主控诊断和执行流里。
          </div>
        ) : null}
        <DebatePanel debate={debate} open={open} onToggle={onToggle} modeFromMeta="" />
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
  if (isFailed) {
    return (
      <div className="rounded border border-red-500/35 bg-red-500/10 p-4 text-sm leading-relaxed text-red-100">
        Agent 执行失败，未生成可审核产物。请优先查看上方摘要中的主控诊断或执行流，确认是上下文、模型调用、Schema 还是工具链问题。
      </div>
    );
  }
  return (
    <div className="rounded border border-dashed border-mars-border bg-mars-panel/35 p-4 text-sm text-slate-500">
      {t("run.empty.noArtifact")}
    </div>
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

function scorecardHasSignal(scorecard: EvaluationScorecard): boolean {
  const hasCounts = Object.values(scorecard.counts).some((count) => count > 0);
  if (
    scorecard.report_count === 0 &&
    !hasCounts &&
    scorecard.finding_count === 0 &&
    scorecard.top_findings.length === 0 &&
    scorecard.overall_score === null
  ) {
    return false;
  }
  const hasQualityGate =
    Boolean(scorecard.quality_gate) &&
    ((scorecard.quality_gate?.reasons.length ?? 0) > 0 ||
      (scorecard.quality_gate?.gate !== "pass" && scorecard.quality_gate?.gate !== undefined));
  return (
    hasCounts ||
    scorecard.finding_count > 0 ||
    scorecard.top_findings.length > 0 ||
    scorecard.overall_score !== null ||
    hasQualityGate
  );
}

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
  const [open, setOpen] = useState(agentState === "running");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setOpen(agentState === "running");
  }, [activeAgent, agentState]);

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
  const files = parsePatchFiles(patch.text);
  const insertions = files.reduce((total, file) => total + file.insertions, 0);
  const deletions = files.reduce((total, file) => total + file.deletions, 0);
  return (
    <section className="rounded border border-cyan-500/30 bg-cyan-500/5">
      <div className="flex items-center justify-between border-b border-cyan-500/20 px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-cyan-100">Patch {patch.version}</h3>
          {files.length > 0 ? (
            <span className="rounded bg-mars-bg/70 px-2 py-0.5 font-mono text-[10px] text-slate-300">
              {files.length} files
            </span>
          ) : null}
          <span className="font-mono text-[11px] text-emerald-300">+{insertions}</span>
          <span className="font-mono text-[11px] text-red-300">-{deletions}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="max-w-72 truncate rounded bg-mars-bg/70 px-2 py-0.5 font-mono text-[10px] text-slate-400" title={patch.path}>
            {patch.path}
          </span>
          <span className="rounded bg-mars-bg/70 px-2 py-0.5 text-[10px] uppercase text-cyan-200">
            {patch.approved ? t("patch.status.approved") : t("patch.status.pending")}
          </span>
        </div>
      </div>
      {files.length > 0 ? (
        <div className="max-h-[34rem] overflow-auto">
          {files.map((file) => (
            <div key={`${file.oldPath}:${file.newPath}`} className="border-b border-cyan-500/10 last:border-b-0">
              <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-2 border-b border-cyan-500/10 bg-mars-bg/95 px-3 py-2">
                <span className="min-w-0 truncate font-mono text-xs font-semibold text-slate-100">
                  {file.path}
                </span>
                <div className="flex shrink-0 items-center gap-2 font-mono text-[11px]">
                  <span className="text-emerald-300">+{file.insertions}</span>
                  <span className="text-red-300">-{file.deletions}</span>
                </div>
              </div>
              <pre className="overflow-x-auto bg-mars-bg/60 py-2 font-mono text-[11px] leading-5">
                {file.lines.map((line, index) => (
                  <code
                    key={`${file.path}:${index}`}
                    className={`block whitespace-pre px-3 ${patchLineClassName(line.kind)}`}
                  >
                    {line.text || " "}
                  </code>
                ))}
              </pre>
            </div>
          ))}
        </div>
      ) : (
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-[11px] leading-relaxed text-slate-200">
          {patch.text}
        </pre>
      )}
    </section>
  );
}

function patchLineClassName(kind: PatchLineKind): string {
  if (kind === "add") {
    return "border-l-2 border-emerald-400/80 bg-emerald-500/10 text-emerald-100";
  }
  if (kind === "delete") {
    return "border-l-2 border-red-400/80 bg-red-500/10 text-red-100";
  }
  if (kind === "hunk") {
    return "border-l-2 border-cyan-400/70 bg-cyan-500/10 text-cyan-200";
  }
  return "border-l-2 border-transparent text-slate-300";
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

type RouteParams = { id?: string | string[] };

function routeRunId(params: Promise<RouteParams> | undefined): string {
  if (!params) return "";
  return "";
}

export default function RunDetailPage({
  params,
}: {
  params?: Promise<RouteParams>;
}): JSX.Element {
  const initialRunId = routeRunId(params);
  return (
    <Suspense fallback={<div className="p-8 text-sm text-slate-400">加载中…</div>}>
      <RunDetailPageInner initialRunId={initialRunId} />
    </Suspense>
  );
}
