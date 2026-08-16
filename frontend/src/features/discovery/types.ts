export type DiscoveryLifecycle =
  | "created"
  | "running"
  | "paused"
  | "waiting_hitl"
  | "completed"
  | "stopped"
  | "failed";

export type CandidateStatus =
  | "draft"
  | "validated"
  | "queued"
  | "running"
  | "evaluated"
  | "dominated"
  | "elite"
  | "quarantined"
  | "rejected"
  | "failed"
  | "promoted";

export type FidelityLevel = "F0" | "F1" | "F2" | "F3" | "F4";
export type ObjectiveDirection = "minimize" | "maximize";

export interface MetricValue {
  value: number;
  unit: string;
  direction: ObjectiveDirection;
}

export interface BudgetLimits {
  proposals: number;
  llm_tokens: number;
  gpu_seconds: number;
  wall_seconds: number;
  api_cost: number;
  max_parallel: number;
}

export interface ObjectiveSpec {
  name: string;
  direction: ObjectiveDirection;
  unit?: string;
  hard_constraint?: number | null;
}

export interface DiscoveryRunSpec {
  task: string;
  project: string;
  objective: string;
  allowed_paths?: string[];
  forbidden_paths?: string[];
  evolution_zones?: string[];
  dataset_ref?: string;
  dataset_hash?: string;
  baseline_ref?: string;
  baseline_hash?: string;
  evaluator_ref?: string;
  evaluator_hash?: string;
  objectives: ObjectiveSpec[];
  budget?: Partial<BudgetLimits>;
  seed?: number;
  promotion_policy?: Record<string, unknown>;
  stop_policy?: Record<string, unknown>;
  owner?: string;
  reviewer?: string;
  candidates_per_iteration?: number;
  max_iterations?: number;
  auto_approve?: boolean;
  idea_mode?: "fast" | "auto";
  project_inputs: Record<string, unknown>;
}

export interface CreateDiscoveryRunRequest {
  spec: DiscoveryRunSpec;
  idempotency_key: string;
}

export interface BudgetUsage {
  proposals: number;
  llm_tokens: number;
  gpu_seconds: number;
  wall_seconds: number;
  api_cost: number;
}

export interface SlotLease {
  schema_id: "discovery_slot_lease.v1";
  run_id: string;
  lease_id: string;
  candidate_id: string;
  acquired_at: string;
}

export interface BudgetSnapshot {
  schema_id: "budget_snapshot.v1";
  run_id: string;
  limits: BudgetLimits;
  used: BudgetUsage;
  remaining: BudgetUsage;
  active_slots: SlotLease[];
}

export interface ModelGenome {
  schema_id: "model_genome.v1";
  family: string;
  structure: Record<string, unknown>;
  hyperparameters: Record<string, unknown>;
  recipe: Record<string, unknown>;
  mutable_zones: string[];
}

export interface CandidateRecord {
  schema_id: "candidate.v1";
  candidate_id: string;
  run_id: string;
  parent_ids: string[];
  generation: number;
  iteration: number;
  creator: string;
  model_provider: string;
  model_name: string;
  prompt_hash: string;
  context_manifest_ref: string;
  operator: string;
  genome: ModelGenome;
  artifact_refs: Record<string, string>;
  fingerprints: Record<string, string>;
  status: CandidateStatus;
  idempotency_key: string;
  failure_reason: string;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface CandidateEvaluation {
  schema_id: "candidate_evaluation.v1";
  evaluation_id: string;
  candidate_id: string;
  run_id: string;
  fidelity: FidelityLevel;
  seed: number;
  evaluator_hash: string;
  dataset_hash: string;
  environment_hash: string;
  hardware_hash: string;
  raw_metrics: Record<string, unknown>;
  canonical_metrics: Record<string, MetricValue>;
  hard_constraints_passed: boolean;
  evidence_refs: string[];
  resource_usage: Record<string, number>;
  findings: string[];
  created_at: string;
}

export interface ArchiveSnapshot {
  schema_id: "archive_snapshot.v1";
  snapshot_id: string;
  run_id: string;
  iteration: number;
  pareto_candidate_ids: string[];
  niche_elites: Record<string, string>;
  negative_candidate_ids: string[];
  quarantined_candidate_ids: string[];
  lineage_refs: string[];
  budget_snapshot: Record<string, number>;
  stop_reason: string;
  snapshot_hash: string;
  created_at: string;
}

export interface IterationNode {
  iteration: number;
  child_run_id: string;
  parent_run_id: string;
  depends_on_run_ids: string[];
  status: string;
}

export interface DiscoveryRunView {
  run_id: string;
  project: string;
  objective: string;
  lifecycle: DiscoveryLifecycle;
  checkpoint_sequence: number;
  next_iteration: number;
  next_ordinal: number;
  candidate_count: number;
  evaluated_count: number;
  failed_count: number;
  quarantined_count: number;
  hitl_pending: boolean;
  selected_candidate_id: string;
  iteration_nodes: IterationNode[];
  budget: BudgetSnapshot;
  latest_archive: ArchiveSnapshot | null;
  stop_reason: string;
}

export interface DiscoveryCheckpoint {
  schema_id: "discovery_checkpoint.v1";
  run_id: string;
  sequence: number;
  phase: string;
  iteration: number;
  status: "running" | "paused" | "completed" | "failed";
  state: Record<string, unknown>;
  reason: string;
  idempotency_key: string;
  previous_hash: string;
  checkpoint_hash: string;
  created_at: string;
}

export interface DiscoveryEvent {
  schema_id: "discovery_event.v1";
  event_id: string;
  sequence: number;
  name: string;
  run_id: string;
  iteration: number | null;
  child_run_id: string;
  candidate_id: string;
  error_code: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface DiscoveryReplayView {
  run: DiscoveryRunView;
  checkpoints: DiscoveryCheckpoint[];
  candidates: CandidateRecord[];
  evaluations: CandidateEvaluation[];
  archives: ArchiveSnapshot[];
  events: DiscoveryEvent[];
}

export type DiscoveryDataSource = "rest" | "contract_fixture";

export interface DiscoverySnapshot {
  source: DiscoveryDataSource;
  replay: DiscoveryReplayView;
  loaded_at: string;
}

export type RunMutation = "start" | "pause" | "resume" | "stop";
