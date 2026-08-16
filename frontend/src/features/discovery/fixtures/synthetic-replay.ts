import type {
  ArchiveSnapshot,
  CandidateEvaluation,
  CandidateRecord,
  CandidateStatus,
  DiscoveryCheckpoint,
  DiscoveryEvent,
  DiscoveryReplayView,
  FidelityLevel,
} from "../types";

const runId = "synthetic-preview";
const createdAt = "2026-08-16T08:00:00.000Z";
const paretoIds = ["candidate-003", "candidate-008", "candidate-014"];

function candidateStatus(index: number): CandidateStatus {
  if (index === 17) return "quarantined";
  if (index === 19) return "failed";
  return paretoIds.includes(`candidate-${String(index).padStart(3, "0")}`)
    ? "elite"
    : "dominated";
}

function candidate(index: number): CandidateRecord {
  const id = `candidate-${String(index).padStart(3, "0")}`;
  const parentIndex = index > 10 ? index - 10 : 0;
  const status = candidateStatus(index);
  return {
    schema_id: "candidate.v1",
    candidate_id: id,
    run_id: runId,
    parent_ids: parentIndex > 0 ? [`candidate-${String(parentIndex).padStart(3, "0")}`] : [],
    generation: index > 10 ? 1 : 0,
    iteration: 0,
    creator: index % 3 === 0 ? "proposal-agent-b" : "proposal-agent-a",
    model_provider: "mock-provider",
    model_name: "deterministic-model",
    prompt_hash: `prompt-${String(index).padStart(3, "0")}`,
    context_manifest_ref: "context/manifest.v1.json",
    operator: index > 10 ? "mutate" : "generate",
    genome: {
      schema_id: "model_genome.v1",
      family: "synthetic_regression",
      structure: { depth: 2 + (index % 4), width: 16 + index * 2 },
      hyperparameters: { learning_rate: Number((0.001 + index * 0.0001).toFixed(4)) },
      recipe: { schedule: index % 2 === 0 ? "cosine" : "constant" },
      mutable_zones: ["structure", "hyperparameters"],
    },
    artifact_refs: {
      diff: `artifacts/${id}/change.diff`,
      logs: `artifacts/${id}/execution.log`,
      metrics: `artifacts/${id}/metrics.json`,
    },
    fingerprints: { exact: `fingerprint-${String(index).padStart(3, "0")}` },
    status,
    idempotency_key: `proposal:0:${index - 1}`,
    failure_reason:
      status === "quarantined"
        ? "required artifact checksum did not match"
        : status === "failed"
          ? "evaluation process returned a non-zero status"
          : "",
    created_at: new Date(Date.parse(createdAt) + index * 30_000).toISOString(),
    updated_at: new Date(Date.parse(createdAt) + index * 30_000 + 15_000).toISOString(),
    metadata: { ordinal: index - 1, proposal_label: `variant-${index}` },
  };
}

function fidelity(index: number): FidelityLevel {
  if (index === 14) return "F3";
  if (index === 8) return "F2";
  if (index === 3) return "F1";
  return "F0";
}

function evaluation(index: number): CandidateEvaluation | null {
  const record = candidate(index);
  if (record.status === "quarantined" || record.status === "failed") return null;
  const validationScore = Number((0.86 - index * 0.009 + (index % 4) * 0.006).toFixed(4));
  const latency = Number((18 + index * 0.7 - (index % 5) * 1.1).toFixed(2));
  return {
    schema_id: "candidate_evaluation.v1",
    evaluation_id: `evaluation-${String(index).padStart(3, "0")}`,
    candidate_id: record.candidate_id,
    run_id: runId,
    fidelity: fidelity(index),
    seed: 4100 + index,
    evaluator_hash: "evaluator-synthetic-v1",
    dataset_hash: "dataset-synthetic-v1",
    environment_hash: "environment-synthetic-v1",
    hardware_hash: "hardware-synthetic-v1",
    raw_metrics: { validation_score: validationScore, latency_ms: latency },
    canonical_metrics: {
      validation_score: { value: validationScore, unit: "score", direction: "maximize" },
      latency: { value: latency, unit: "ms", direction: "minimize" },
    },
    hard_constraints_passed: index !== 16,
    evidence_refs: [
      `evidence/${record.candidate_id}/summary.json`,
      `evidence/${record.candidate_id}/metrics.json`,
    ],
    resource_usage: {
      llm_tokens: 900 + index * 37,
      wall_seconds: 22 + index * 1.5,
      api_cost: Number((0.02 + index * 0.001).toFixed(3)),
    },
    findings:
      index === 16
        ? ["resource threshold exceeded"]
        : ["deterministic evaluation completed", "metrics normalized"],
    created_at: new Date(Date.parse(createdAt) + index * 30_000 + 20_000).toISOString(),
  };
}

const candidates = Array.from({ length: 20 }, (_, index) => candidate(index + 1));
const evaluations = Array.from({ length: 20 }, (_, index) => evaluation(index + 1)).filter(
  (item): item is CandidateEvaluation => item !== null,
);

const archive: ArchiveSnapshot = {
  schema_id: "archive_snapshot.v1",
  snapshot_id: "archive-0001",
  run_id: runId,
  iteration: 0,
  pareto_candidate_ids: paretoIds,
  niche_elites: {
    "best-validation": "candidate-014",
    "lowest-latency": "candidate-003",
    "balanced": "candidate-008",
  },
  negative_candidate_ids: ["candidate-016", "candidate-019"],
  quarantined_candidate_ids: ["candidate-017"],
  lineage_refs: candidates.map((item) => `lineage/${item.candidate_id}.json`),
  budget_snapshot: { proposals: 20, llm_tokens: 22630, wall_seconds: 652 },
  stop_reason: "",
  snapshot_hash: "archive-synthetic-hash",
  created_at: "2026-08-16T08:16:00.000Z",
};
const checkpoints: DiscoveryCheckpoint[] = [
  {
    schema_id: "discovery_checkpoint.v1",
    run_id: runId,
    sequence: 1,
    phase: "created",
    iteration: 0,
    status: "running",
    state: { lifecycle: "created" },
    reason: "",
    idempotency_key: "run-created",
    previous_hash: "",
    checkpoint_hash: "checkpoint-0001",
    created_at: createdAt,
  },
  {
    schema_id: "discovery_checkpoint.v1",
    run_id: runId,
    sequence: 22,
    phase: "waiting_hitl",
    iteration: 1,
    status: "paused",
    state: { lifecycle: "waiting_hitl", hitl_pending: true },
    reason: "waiting_hitl",
    idempotency_key: "waiting-hitl",
    previous_hash: "checkpoint-0021",
    checkpoint_hash: "checkpoint-0022",
    created_at: "2026-08-16T08:17:00.000Z",
  },
];

function event(
  sequence: number,
  name: string,
  candidateId = "",
  payload: Record<string, unknown> = {},
): DiscoveryEvent {
  return {
    schema_id: "discovery_event.v1",
    event_id: `event-${String(sequence).padStart(20, "0")}`,
    sequence,
    name,
    run_id: runId,
    iteration: 0,
    child_run_id: "synthetic-preview-iteration-0000",
    candidate_id: candidateId,
    error_code:
      name === "discovery.candidate.quarantined" ? "discovery.preflight_rejected" : null,
    payload,
    created_at: new Date(Date.parse(createdAt) + sequence * 20_000).toISOString(),
  };
}

const events: DiscoveryEvent[] = [
  event(1, "discovery.run.created"),
  event(2, "discovery.run.started"),
  ...candidates.flatMap((item, index) => {
    const sequence = 3 + index * 3;
    if (item.status === "quarantined") {
      return [
        event(sequence, "discovery.candidate.created", item.candidate_id),
        event(sequence + 1, "discovery.candidate.quarantined", item.candidate_id, {
          reason: item.failure_reason,
        }),
      ];
    }
    if (item.status === "failed") {
      return [
        event(sequence, "discovery.candidate.created", item.candidate_id),
        event(sequence + 1, "discovery.candidate.transitioned", item.candidate_id, {
          from: "running",
          to: "failed",
          reason: item.failure_reason,
        }),
      ];
    }
    return [
      event(sequence, "discovery.candidate.created", item.candidate_id),
      event(sequence + 1, "discovery.candidate.transitioned", item.candidate_id, {
        from: "draft",
        to: "validated",
        reason: "",
      }),
      event(sequence + 2, "discovery.candidate.evaluated", item.candidate_id, {
        evaluation_id: `evaluation-${item.candidate_id.slice(-3)}`,
      }),
    ];
  }),
  event(65, "discovery.archive.updated", "", { snapshot_id: archive.snapshot_id }),
  event(66, "discovery.hitl.requested"),
];

export const syntheticDiscoveryReplay: DiscoveryReplayView = {
  run: {
    run_id: runId,
    project: "synthetic_regression",
    objective: "maximize validation score while reducing evaluation latency",
    lifecycle: "waiting_hitl",
    checkpoint_sequence: 22,
    next_iteration: 1,
    next_ordinal: 0,
    candidate_count: candidates.length,
    evaluated_count: evaluations.length,
    failed_count: 1,
    quarantined_count: 1,
    hitl_pending: true,
    selected_candidate_id: "",
    iteration_nodes: [
      {
        iteration: 0,
        child_run_id: "synthetic-preview-iteration-0000",
        parent_run_id: runId,
        depends_on_run_ids: [],
        status: "completed",
      },
    ],
    budget: {
      schema_id: "budget_snapshot.v1",
      run_id: runId,
      limits: {
        proposals: 20,
        llm_tokens: 40000,
        gpu_seconds: 0,
        wall_seconds: 1200,
        api_cost: 1,
        max_parallel: 4,
      },
      used: {
        proposals: 20,
        llm_tokens: 22630,
        gpu_seconds: 0,
        wall_seconds: 652,
        api_cost: 0.531,
      },
      remaining: {
        proposals: 0,
        llm_tokens: 17370,
        gpu_seconds: 0,
        wall_seconds: 548,
        api_cost: 0.469,
      },
      active_slots: [],
    },
    latest_archive: archive,
    stop_reason: "",
  },
  checkpoints,
  candidates,
  evaluations,
  archives: [archive],
  events,
};
