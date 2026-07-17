// Thin REST client. By default requests stay same-origin and Next rewrites
// /api/* to the backend; NEXT_PUBLIC_BACKEND_URL remains available for
// deployments that need a direct backend origin.
const CONFIGURED_BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
const BASE = CONFIGURED_BACKEND_URL;

function apiUrl(path: string): URL {
  const origin =
    BASE ||
    (typeof window === "undefined" ? "http://127.0.0.1:3001" : window.location.origin);
  return new URL(path, origin);
}

export const STAGE_ORDER = ["idea", "experiment", "coding", "execution", "writing"] as const;
export type Stage = (typeof STAGE_ORDER)[number];

export const STAGE_TO_TIER: Record<Stage, 1 | 2 | 3 | 4 | 5> = {
  idea: 1,
  experiment: 2,
  coding: 3,
  execution: 4,
  writing: 5,
};

export const STAGE_TO_STEM: Record<Stage, string> = {
  idea: "idea_proposal",
  experiment: "experiment_plan",
  coding: "code_spec",
  execution: "run_log",
  writing: "research_report",
};

export type RunSummary = {
  run_id: string;
  project: string;
  task: string;
  entrypoint: string;
  created_at: string;
};

export type TrashRunSummary = RunSummary & {
  deleted_at: string;
  expires_at: string;
  days_remaining: number;
};

export type GraphNode = { key: string; kind: string; state: string; metadata: Record<string, unknown> };
export type GraphEdge = { src: string; dst: string };
export type RunDetail = RunSummary & {
  states: Record<string, string>;
  graph: { nodes: GraphNode[]; edges: GraphEdge[]; entrypoints: string[] };
};

export type DataSourceProfile = {
  id: string;
  project: string;
  kind: string;
  original_name: string;
  stored_path: string;
  size_bytes: number;
  checksum: string;
  fs_mhz: number | null;
  sample_rate_hz: number | null;
  channel_count: number | null;
  description: string;
  format: string;
  shape: number[] | null;
  dtype: string | null;
  preview_key: string;
  sample_points: number;
  dict_entries: {
    key: string;
    shape?: number[] | null;
    dtype?: string | null;
    sample_points?: number;
  }[];
  spectrum_available: boolean;
  spectrum_path: string;
  warnings: string[];
  created_at: string;
  is_default: boolean;
};

export type ArtifactView = {
  run_id: string;
  agent_dir: string;
  stem: string;
  version: string;
  path: string;
  text: string;
  metadata: Record<string, unknown>;
  schema_id: string | null;
  valid: boolean;
  errors: { path: string; message: string }[];
};

export type WorkspaceFileView = {
  run_id: string;
  agent_dir: string;
  relative_path: string;
  path: string;
  exists: boolean;
  text: string;
  size_bytes: number;
  content_type: string;
};

export type WorkspaceTreeEntry = {
  relative_path: string;
  name: string;
  kind: "file" | "directory" | string;
  path: string;
  size_bytes: number;
  content_type: string;
};

export type WorkspaceTreeView = {
  run_id: string;
  agent_dir: string;
  root_path: string;
  entries: WorkspaceTreeEntry[];
};

export type DiagnosisView = {
  run_id: string;
  version: string;
  path: string;
  text: string;
  metadata: Record<string, unknown>;
};

export type FeedbackPacketView = {
  run_id: string;
  attempt: number;
  path: string;
  text: string;
  metadata: Record<string, unknown>;
};

export type RunMemoryEventView = {
  run_id: string;
  path: string;
  items: Record<string, unknown>[];
};

export type SelfEvolutionLeverItem = {
  id: string;
  lever_type: string;
  agent: string;
  title: string;
  source: string;
  source_path: string;
  status: string;
  text_preview: string;
  evidence_refs: string[];
  suggested_action: string;
};

export type SelfEvolutionLeversView = {
  schema: string;
  run_id: string;
  project: string;
  mutation_mode: string;
  allowed_actions: string[];
  levers: Record<string, SelfEvolutionLeverItem[]>;
  counts: Record<string, number>;
};

export type SelfEvolutionMutation = Record<string, unknown> & {
  id: string;
  agent: string;
  path: string;
  status: string;
  lever_id: string;
  lever_type: string;
  text_preview: string;
};

export type SelfEvolutionMutationDecision = {
  mutation_id: string;
  agent: string;
  path: string;
  status: string;
  applied_path: string;
};

export type CommanderObservabilityView = {
  schema: string;
  run_id: string;
  project: string;
  attempt_count: number;
  latest: Record<string, unknown> | null;
  attempts: Record<string, unknown>[];
  feedback_packets: Record<string, unknown>[];
  episode_memory: Record<string, unknown>[];
  memory_candidates: Record<string, unknown>[];
  attempt_ledger: Record<string, unknown>;
  checks: Record<string, number>;
};

export type CommanderAttributionEvalView = {
  schema: string;
  project: string;
  case_count: number;
  passed: number;
  failed: number;
  accuracy: number;
  target_accuracy: number;
  continuation_accuracy: number;
  human_pause_accuracy: number;
  cases: Record<string, unknown>[];
};

export type RunObservabilityView = {
  schema: string;
  run_id: string;
  project: string;
  task: string;
  entrypoint: string;
  status: string;
  states: Record<string, string>;
  health: Record<string, unknown>;
  latest_event_at: string;
  event_streams: Record<string, Record<string, unknown>>;
  timeline: Record<string, unknown>[];
  trace: Record<string, unknown>;
  execution: Record<string, unknown>;
  audit: Record<string, unknown>;
};

export type FeedbackLoopStartResult = {
  status: string;
  target?: string;
  attempt?: number;
  feedback_packet_ref?: string;
  confidence?: number;
  reason?: string;
};

export type MemoryPromotionView = {
  candidate_id: string;
  agent: string;
  memory_id: string;
  status: string;
};

export type PatchView = {
  run_id: string;
  version: string;
  path: string;
  text: string;
  approved: boolean;
  application: Record<string, unknown> | null;
};

export type TraceSpan = {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  kind: string;
  started_at: string;
  ended_at: string | null;
  status: string;
  attributes: Record<string, unknown>;
};

export type TraceManifest = {
  schema: string;
  run_id: string;
  trace_id: string;
  root_span_id: string;
  created_at: string;
  updated_at: string;
  spans: TraceSpan[];
  event_index: Record<string, unknown>[];
};

export type ContextSegment = {
  id: string;
  kind: string;
  title: string;
  source_ref: string;
  content_hash: string;
  tokens_estimated: number;
  priority: string;
  selection_reason: string;
  compression: string;
  risk_flags: string[];
  text_preview: string;
  raw_ref: string | null;
};

export type ContextManifestV2 = {
  schema: "context_manifest.v2";
  manifest_id: string;
  run_id: string;
  agent: string;
  node_key: string;
  project: string;
  output_schema: string;
  purpose: string;
  created_at: string;
  budget: {
    max: number;
    target: number;
    used: number;
    over_budget: boolean;
  };
  segments: ContextSegment[];
  render_order: string[];
  messages_preview: { role: string; content: string }[];
  diagnostics: Record<string, unknown>;
  raw_refs: string[];
};

export type ContextManifestSummary = {
  manifest_id: string;
  agent: string;
  node_key: string;
  purpose: string;
  created_at: string;
  path: string;
  budget: Record<string, unknown>;
  segment_count: number;
  risk_counts: Record<string, number>;
};

export type ContextRunView = {
  run_id: string;
  project: string;
  agents: string[];
  manifests: ContextManifestSummary[];
  budget_summary: {
    manifest_count: number;
    used_tokens: number;
    over_budget_count: number;
  };
  risk_summary: Record<string, number>;
};

export type ContextRawView = {
  raw_ref: string;
  path: string;
  size_chars: number;
  truncated: boolean;
  content: string;
};

export type Stats = {
  agents_registered: number;
  runs_total: number;
  runs_running: number;
  runs_failed: number;
  runs_waiting_review: number;
  artifacts_total: number;
  kb_total: number;
  kb_per_zone: Record<string, number>;
  states: Record<string, number>;
  waiting_review_runs: { run_id: string; task: string; agent: string }[];
};

export type ReadinessCheck = {
  name: string;
  ready: boolean;
  severity: string;
  message: string;
  details: Record<string, unknown>;
};

export type Readiness = {
  ready: boolean;
  runtime_mode: string;
  mock_mode: string;
  execution_backend: string;
  project: string;
  checks: ReadinessCheck[];
};

export type GpuDevice = {
  index: number;
  name: string;
  memory_total_mb: number;
  memory_used_mb: number;
  utilization_gpu_percent: number;
  temperature_c: number;
  power_draw_w: number;
};

export type RuntimeStatus = {
  schema: "runtime_status.v1";
  generated_at: string;
  project: string;
  readiness: Readiness;
  resources: {
    gpu: {
      available: boolean;
      source: string;
      message: string;
      devices: GpuDevice[];
      summary: {
        count: number;
        memory_total_mb: number;
        memory_used_mb: number;
        utilization_gpu_percent?: number;
      };
    };
    execution: {
      backend: string;
      mock_mode: string;
      max_concurrency: number;
      batch_steps: number;
      command_timeout_seconds: number;
      allow_real_patch_apply: boolean;
      local_command_count: number;
      remote_gpu: {
        enabled: boolean;
        configured: boolean;
      };
      paper_static: {
        enabled: boolean;
        python: string;
        python_exists: boolean;
        repo_path: string;
        repo_exists: boolean;
        config_path: string;
        config_exists: boolean;
        data_path: string;
        data_exists: boolean;
        default_max_iters: number;
        default_dry_run: boolean;
      };
      code_checks: {
        lint_enabled: boolean;
        test_enabled: boolean;
      };
    };
  };
  observability: {
    langsmith: {
      enabled: boolean;
      configured: boolean;
      package_available: boolean;
      project: string;
      endpoint: string;
      timeout_ms: number;
      ui_url: string;
      embed_url: string;
      message: string;
    };
    tracing: {
      enabled: boolean;
      exporter: string;
      manifest_path: string;
      file_sink: boolean;
      websocket_sink: boolean;
    };
  };
  config: {
    runtime: {
      mode: string;
      mock_mode: string;
      default_project: string;
      llm_timeout_seconds: number;
    };
    llm: {
      available_providers: string[];
      agents_configured: number;
      secrets_configured: Record<string, boolean>;
    };
    tools: {
      total: number;
      enabled: number;
      disabled: number;
      network_defined: number;
      network_runtime_enabled: boolean;
      web_search_provider: string;
    };
    context: {
      max_tokens: number;
      target_tokens: number;
      auto_compress: boolean;
      tool_raw_externalize: boolean;
      workbench_enabled: boolean;
    };
    mcp: Record<string, boolean>;
  };
};

export type AgentContextFile = {
  agent: string;
  path: string;
  category: string;
  source: string;
  editable: boolean;
  deletable: boolean;
  size_chars: number;
  content: string;
};

export type AgentResearchSite = {
  id: string;
  label: string;
  url: string;
  enabled: boolean;
  source: string;
};

export type AgentCodeRepository = {
  project: string;
  label: string;
  repo_mode: string;
  repo_path: string;
  exists: boolean;
  read_only: boolean;
  sync_strategy: string;
  allowed_paths: string[];
  protected_paths: string[];
  ignore_patterns: string[];
  baseline_rules_file: string;
};

export type AgentContextBlueprintItem = {
  order: number;
  layer: string;
  content: string;
  storage: string[];
  required: string;
  risk: string;
  strategy: string;
  packing_position: string;
};

export type AgentContextStorageLayout = {
  long_term_root: string;
  run_root: string;
  agent_root: string;
  manifests: string;
  raw: string;
  packed: string;
  memory: string;
  research: string;
  debate: string;
  tool_results: string;
};

export type AgentContextBlueprint = {
  agent: string;
  goal: string;
  storage_layout: AgentContextStorageLayout;
  items: AgentContextBlueprintItem[];
  packing_order: string[];
};

export type AgentContextView = {
  agent: string;
  files: AgentContextFile[];
  research_sites: AgentResearchSite[];
  code_repositories: AgentCodeRepository[];
  blueprint: AgentContextBlueprint;
  defaults: {
    editable_categories?: string[];
    read_only_sources?: string[];
  };
};

export type CodeSource = {
  id: string;
  label: string;
  path: string;
  exists: boolean;
  read_only: boolean;
  kind: string;
};

export type CodeTreeItem = {
  path: string;
  name: string;
  kind: "directory" | "file";
  depth: number;
  size_chars: number;
  language: string;
};

export type CodeFileContent = {
  source_id: string;
  path: string;
  language: string;
  size_chars: number;
  truncated: boolean;
  content: string;
};

export type UpstreamContextItem = {
  id: string;
  agent: string;
  title: string;
  path: string;
  kind: string;
  content: string;
};

export type CodingMemoryItem = {
  id: string;
  label: string;
  text: string;
  enabled: boolean;
  source: string;
  editable: boolean;
};

export type CodingWorkspace = {
  project: string;
  selected_source: string;
  sources: CodeSource[];
  files: CodeTreeItem[];
  upstream_context: UpstreamContextItem[];
  memory_items: CodingMemoryItem[];
  kb_memory_items: CodingMemoryItem[];
};

export async function getReadiness(project?: string): Promise<Readiness> {
  const url = apiUrl(`${BASE}/api/readiness`);
  if (project) {
    url.searchParams.set("project", project);
  }
  return jsonOrThrow(await fetch(url));
}

export async function getRuntimeStatus(project?: string): Promise<RuntimeStatus> {
  const url = apiUrl(`${BASE}/api/runtime/status`);
  if (project) {
    url.searchParams.set("project", project);
  }
  return jsonOrThrow(await fetch(url));
}

export type Template = { schema_id: string; text: string };
export async function getTemplate(schemaId: string): Promise<Template> {
  return jsonOrThrow(await fetch(`${BASE}/api/templates/${schemaId}`));
}
export async function getTemplateByAgent(agent: string): Promise<Template> {
  return jsonOrThrow(await fetch(`${BASE}/api/templates/by_agent/${agent}`));
}

export type ZoneSummary = { name: string; label_zh: string; count: number };

export type KBItem = {
  id: string;
  zone: string;
  text: string;
  text_excerpt: string;
  metadata: Record<string, unknown>;
};

export type KnowledgeMemoryType = "semantic" | "episodic" | "procedural";
export type KnowledgeProfile = "dev_e2e" | "research" | "hardware";

export type KnowledgeSearchHit = {
  score: number;
  item: KBItem;
};

export type KnowledgeSearchParams = {
  q: string;
  topK?: number;
  zone?: string;
  zones?: string[];
  project?: string;
  memoryType?: KnowledgeMemoryType;
  includeMock?: boolean;
  includeSuperseded?: boolean;
  profile?: KnowledgeProfile;
};

export type ProjectSummary = {
  name: string;
  description: string;
  domain: string;
  tags: string[];
  repo_path: string;
  repo_exists: boolean;
};

export type EventEntry = {
  run_id: string;
  channel: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

export type EvaluationDecision = "pass" | "warn" | "revise" | "block" | "fail";

export type EvaluationFinding = {
  id?: string;
  severity?: string;
  category?: string;
  message?: string;
  evidence_refs?: string[];
  target_ref?: string;
  evaluator?: string;
};

export type EvaluationReportItem = {
  path?: string;
  target_ref?: string;
  target_schema?: string;
  evaluator?: string;
  decision?: EvaluationDecision | string;
  blocking?: boolean;
  overall_score?: number | null;
  finding_count?: number;
  findings?: EvaluationFinding[];
};

export type EvaluationPolicyDecision = {
  schema: string;
  scope: "artifact" | "run" | string;
  gate: "pass" | "warn" | "revise" | "block" | string;
  action: string;
  review_priority: "normal" | "elevated" | "high" | "critical" | string;
  auto_approval_allowed?: boolean;
  auto_approval_enforced?: boolean;
  completion_allowed?: boolean;
  enforcement_mode?: string;
  thresholds?: Record<string, unknown>;
  reasons: string[];
};

export type ArtifactEvaluationSummary = {
  agent: string;
  node: string;
  artifact_ref: string;
  artifact_id: string;
  stem: string;
  version: string;
  decision: EvaluationDecision | string;
  blocking: boolean;
  report_count: number;
  overall_score: number | null;
  top_findings: EvaluationFinding[];
  reports: EvaluationReportItem[];
  policy?: EvaluationPolicyDecision;
};

export type ArtifactEvaluationReport = {
  path: string;
  filename: string;
  evaluator_slug: string;
  metadata: {
    target_ref?: string;
    target_schema?: string;
    evaluator?: string;
    decision?: EvaluationDecision | string;
    blocking?: boolean;
    overall_score?: number | null;
    findings?: EvaluationFinding[];
    recommended_actions?: string[];
    created?: string;
    [key: string]: unknown;
  };
  text: string;
};

export type EvaluationScorecard = {
  schema: string;
  run_id: string;
  project: string;
  created: string;
  overall_decision: EvaluationDecision | string;
  overall_score: number | null;
  counts: Record<string, number>;
  report_count: number;
  finding_count: number;
  top_findings: EvaluationFinding[];
  reports: EvaluationReportItem[];
  quality_gate?: EvaluationPolicyDecision;
};

export type PostTrainingExportRecord = {
  schema: string;
  created: string;
  run_id: string;
  project: string;
  artifact: Record<string, unknown>;
  output: Record<string, unknown>;
  evaluation: Record<string, unknown>;
  labels: Record<string, Record<string, unknown>>;
  preference_candidate: Record<string, unknown>;
  training_eligible: boolean;
};

export type PostTrainingExportManifest = {
  schema: string;
  run_id: string;
  project: string;
  created: string;
  path: string;
  record_count: number;
  eligible_count: number;
  include_drafts: boolean;
  min_artifact_score: number;
  allowed_decisions: string[];
  records_preview: PostTrainingExportRecord[];
};

export type ExecutionPlot = {
  filename: string;
  experiment_id: string;
  metric: string;
  url: string;
  updated_at: number;
  size_bytes: number;
};

export type McpAdapterStatus = {
  kind: "chroma" | "filesystem" | "git" | "github";
  configured: boolean;
  available: boolean;
  detail: string;
  fallback: string;
  tools: string[];
};

export type ToolSpecView = {
  name: string;
  namespace: string;
  description: string;
  policy: Record<string, unknown>;
  bridge_only: boolean;
  mcp_adapter: McpAdapterStatus | null;
};

export type ToolAuditEntry = Record<string, unknown> & {
  call_id?: string;
  tool?: string;
  status?: string;
  agent?: string;
  duration_ms?: number;
  error?: string | null;
  rollback_ref?: string | null;
};

export type ToolAuditFilters = {
  tool?: string;
  status?: string;
  callId?: string;
  event?: string;
  limit?: number;
};

export type ToolApprovalRecord = Record<string, unknown> & {
  approval_id: string;
  tool: string;
  status: string;
  reason?: string;
  created_at?: string;
};

export type TimelineItem = {
  id: string;
  timestamp: string;
  source: string;
  kind: string;
  title: string;
  summary: string;
  status: string;
  agent: string;
  node: string;
  payload: Record<string, unknown>;
};

export type WorkLogItem = {
  id: string;
  timestamp: string;
  elapsed_seconds: number | null;
  agent: string;
  kind: string;
  status: string;
  title: string;
  detail: string;
  next_action: string;
  evidence_refs: string[];
};

export type WorkLogView = {
  run_id: string;
  project: string;
  agent: string;
  status: string;
  started_at: string;
  latest_at: string;
  elapsed_seconds: number | null;
  items: WorkLogItem[];
};

export type ReportDeliverable = {
  kind: "markdown" | "excel" | "word" | "powerpoint" | string;
  path: string;
  status: "completed" | "failed" | "skipped" | string;
  error?: string;
  bytes?: number;
};

export type ReportQaStatus = {
  status: "passed" | "degraded" | "failed" | string;
  checks: { name: string; status: string; detail?: string }[];
};

export type ReportBundle = {
  exists: boolean;
  run_id?: string;
  manifest?: string;
  metadata?: {
    schema?: string;
    project?: string;
    agent?: string;
    run_id?: string;
    created_at?: string;
    data_pack?: string;
    deliverables?: ReportDeliverable[];
    source_refs?: string[];
    qa_status?: ReportQaStatus;
    generation_errors?: string[];
    [key: string]: unknown;
  };
  body?: string;
};

export type ConfigFile = {
  name: string;
  path: string;
  text: string;
  data: Record<string, unknown>;
  high_risk: boolean;
};

export type ConfigSnapshot = {
  files: ConfigFile[];
};

export type ConfigValidationResult = {
  valid: boolean;
  errors: string[];
  data: Record<string, unknown>;
};

export type ConfigDiffResult = {
  name: string;
  diff: string;
  valid: boolean;
  errors: string[];
  high_risk: boolean;
};

export type ProviderDefaultView = {
  api_key_env: string;
  base_url: string;
  base_url_env: string;
  configured: boolean;
  base_url_configured: boolean;
};

export type AgentLlmConfigRow = {
  agent: string;
  enabled: boolean;
  provider: string;
  model: string;
  temperature: number;
  max_tokens: number;
  api_key_env: string;
  api_key_configured: boolean;
  base_url: string;
  base_url_env: string;
  base_url_configured: boolean;
};

export type AgentLlmConfigView = {
  agents: AgentLlmConfigRow[];
  providers: string[];
  provider_defaults: Record<string, ProviderDefaultView>;
  secrets_path: string;
  note: string;
};

export type AgentLlmUpdateRow = {
  agent: string;
  enabled: boolean;
  provider: string;
  model: string;
  temperature: number;
  max_tokens: number;
  api_key_env: string;
  api_key?: string;
  base_url: string;
  base_url_env: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorDetailText(status: number, body: string): string {
  try {
    const parsed = JSON.parse(body) as unknown;
    const detail = isRecord(parsed) ? parsed.detail : parsed;
    if (typeof detail === "string") return `HTTP ${status}: ${detail}`;
    if (isRecord(detail)) {
      const error = typeof detail.error === "string" ? detail.error.trim() : "";
      const patchVersion =
        typeof detail.patch_version === "string" ? `patch ${detail.patch_version}: ` : "";
      const blockedByGate =
        typeof detail.blocked_by_gate === "string" && detail.blocked_by_gate
          ? ` blocked by ${detail.blocked_by_gate}.`
          : "";
      const command = isRecord(detail.command_result) ? detail.command_result : null;
      const stderr = typeof command?.stderr === "string" ? command.stderr.trim() : "";
      const stdout = typeof command?.stdout === "string" ? command.stdout.trim() : "";
      const message = error || stderr || stdout || body;
      return `HTTP ${status}: ${patchVersion}${message}${blockedByGate}`;
    }
  } catch {
    // Fall through to the raw body.
  }
  return `HTTP ${status}: ${body}`;
}

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const text = await r.text();
    throw new Error(errorDetailText(r.status, text));
  }
  return (await r.json()) as T;
}

// ---------- runs ----------
export async function listRuns(project?: string): Promise<RunSummary[]> {
  const url = apiUrl(`${BASE}/api/runs`);
  if (project) {
    url.searchParams.set("project", project);
  }
  return jsonOrThrow(await fetch(url));
}
export async function listTrashedRuns(project?: string): Promise<TrashRunSummary[]> {
  const url = apiUrl(`${BASE}/api/runs/trash`);
  if (project) {
    url.searchParams.set("project", project);
  }
  return jsonOrThrow(await fetch(url));
}
export async function getRun(runId: string): Promise<RunDetail> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}`));
}
export async function createRun(body: {
  task: string;
  project: string;
  entrypoint?: string;
  user_request?: string;
  standalone?: boolean;
  seed_artifact?: string;
  data_source?: {
    id: string;
    fs_mhz?: number | null;
    kind?: string | null;
    channel_count?: number | null;
    description?: string | null;
  };
}): Promise<RunDetail> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        entrypoint: "pipeline",
        standalone: false,
        user_request: "",
        ...body,
      }),
    }),
  );
}
export async function startRun(runId: string): Promise<{ status: string }> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}/start`, { method: "POST" }));
}

export async function deleteRun(runId: string): Promise<TrashRunSummary> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),
  );
}

export async function restoreRun(runId: string): Promise<RunSummary> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${encodeURIComponent(runId)}/restore`, { method: "POST" }),
  );
}

export async function permanentlyDeleteRun(runId: string): Promise<void> {
  const response = await fetch(`${BASE}/api/runs/trash/${encodeURIComponent(runId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(errorDetailText(response.status, text));
  }
}

export async function uploadDataSource(params: {
  file: File;
  project: string;
  fsMhz?: number | null;
  kind?: string;
  channelCount?: number | null;
  description?: string;
}): Promise<DataSourceProfile> {
  const url = apiUrl(`${BASE}/api/data-sources/upload`);
  url.searchParams.set("filename", params.file.name);
  url.searchParams.set("project", params.project);
  if (params.fsMhz !== undefined && params.fsMhz !== null) {
    url.searchParams.set("fs_mhz", String(params.fsMhz));
  }
  if (params.kind) {
    url.searchParams.set("kind", params.kind);
  }
  if (params.channelCount !== undefined && params.channelCount !== null) {
    url.searchParams.set("channel_count", String(params.channelCount));
  }
  if (params.description) {
    url.searchParams.set("description", params.description);
  }
  return jsonOrThrow(
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: params.file,
    }),
  );
}

export async function listDataSources(project?: string): Promise<DataSourceProfile[]> {
  const url = apiUrl(`${BASE}/api/data-sources`);
  if (project) {
    url.searchParams.set("project", project);
  }
  return jsonOrThrow(await fetch(url));
}

export async function getDataSource(id: string): Promise<DataSourceProfile> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/data-sources/${encodeURIComponent(id)}`),
  );
}

export async function getDefaultDataSource(project: string): Promise<DataSourceProfile> {
  const url = apiUrl(`${BASE}/api/data-sources/default`);
  url.searchParams.set("project", project);
  return jsonOrThrow(await fetch(url));
}

export async function setDefaultDataSource(
  project: string,
  id: string,
): Promise<DataSourceProfile> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/data-sources/default`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, id }),
    }),
  );
}

export async function updateDataSource(
  id: string,
  body: {
    fs_mhz?: number | null;
    kind?: string | null;
    channel_count?: number | null;
    description?: string | null;
  },
): Promise<DataSourceProfile> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/data-sources/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export function dataSourceSpectrumUrl(id: string): string {
  return `${BASE}/api/data-sources/${encodeURIComponent(id)}/spectrum`;
}

export async function retryAgent(
  runId: string,
  agent: string,
  reason: string,
): Promise<{ status: string; run_id: string; agent: string; node: string }> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${runId}/agents/${agent}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    }),
  );
}

// ---------- tools ----------
export async function listTools(): Promise<ToolSpecView[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/tools`));
}

export async function listToolAdapters(): Promise<McpAdapterStatus[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/tools/adapters`));
}

export async function listRunToolCalls(
  runId: string,
  filters: ToolAuditFilters = {},
): Promise<ToolAuditEntry[]> {
  const url = apiUrl(`${BASE}/api/runs/${runId}/tools`);
  if (filters.tool) url.searchParams.set("tool", filters.tool);
  if (filters.status) url.searchParams.set("status", filters.status);
  if (filters.callId) url.searchParams.set("call_id", filters.callId);
  if (filters.event) url.searchParams.set("event", filters.event);
  if (filters.limit) url.searchParams.set("limit", String(filters.limit));
  return jsonOrThrow(await fetch(url.toString()));
}

export async function listToolApprovals(runId: string): Promise<ToolApprovalRecord[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}/tools/approvals`));
}

export async function approveToolCall(
  runId: string,
  callId: string,
): Promise<{ ok: boolean; approval_id: string; status: string; result: unknown }> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${runId}/tools/${callId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: "ui" }),
    }),
  );
}

export async function rejectToolCall(
  runId: string,
  callId: string,
): Promise<{ ok: boolean; approval_id: string; status: string }> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${runId}/tools/${callId}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: "ui" }),
    }),
  );
}

export async function rollbackToolCall(
  runId: string,
  callId: string,
): Promise<{ ok: boolean; result: unknown }> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${runId}/tools/${callId}/rollback`, {
      method: "POST",
    }),
  );
}

// ---------- artifacts ----------
export async function listVersions(runId: string, agentDir: string, stem: string) {
  return jsonOrThrow<{ version: string; path: string; filename: string }[]>(
    await fetch(`${BASE}/api/artifacts/${runId}/${agentDir}/${stem}/versions`),
  );
}
export async function getArtifact(
  runId: string,
  agentDir: string,
  stem: string,
  version: string,
): Promise<ArtifactView> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/artifacts/${runId}/${agentDir}/${stem}/${version}`),
  );
}
export async function getWorkspaceFile(
  runId: string,
  agentDir: string,
  path: string,
): Promise<WorkspaceFileView> {
  const url = apiUrl(`${BASE}/api/artifacts/${runId}/${agentDir}/workspace-file`);
  url.searchParams.set("path", path);
  return jsonOrThrow(await fetch(url));
}
export async function getWorkspaceTree(
  runId: string,
  agentDir: string,
): Promise<WorkspaceTreeView> {
  return jsonOrThrow(await fetch(`${BASE}/api/artifacts/${runId}/${agentDir}/workspace-tree`));
}
export async function diffVersions(
  runId: string,
  agentDir: string,
  stem: string,
  from: string,
  to: string,
): Promise<{ diff: string }> {
  const url = apiUrl(`${BASE}/api/artifacts/${runId}/${agentDir}/${stem}/diff`);
  url.searchParams.set("from_", from);
  url.searchParams.set("to", to);
  return jsonOrThrow(await fetch(url));
}
export async function editArtifact(
  runId: string,
  agentDir: string,
  stem: string,
  version: string,
  payload: { body?: string; metadata_patch?: Record<string, unknown> },
): Promise<ArtifactView> {
  return jsonOrThrow(
    await fetch(
      `${BASE}/api/artifacts/${runId}/${agentDir}/${stem}/${version}/edit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    ),
  );
}
export async function approveArtifact(
  runId: string,
  agentDir: string,
  stem: string,
  version: string,
): Promise<ArtifactView> {
  return jsonOrThrow(
    await fetch(
      `${BASE}/api/artifacts/${runId}/${agentDir}/${stem}/${version}/approve`,
      { method: "POST" },
    ),
  );
}
export async function rejectArtifact(
  runId: string,
  agentDir: string,
  stem: string,
  reason: string,
): Promise<{ status: string }> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/artifacts/${runId}/${agentDir}/${stem}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    }),
  );
}
export async function getPatch(runId: string, version: string): Promise<PatchView> {
  return jsonOrThrow(await fetch(`${BASE}/api/artifacts/${runId}/coding/patch/${version}`));
}
export async function approvePatch(runId: string, version: string): Promise<ArtifactView> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/artifacts/${runId}/coding/patch/${version}/approve`, {
      method: "POST",
    }),
  );
}
export async function rejectPatch(
  runId: string,
  version: string,
  reason: string,
): Promise<{ status: string }> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/artifacts/${runId}/coding/patch/${version}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    }),
  );
}
export async function pendingReviews(runId: string) {
  return jsonOrThrow<
    {
      run_id: string;
      agent: string;
      artifact_path: string;
      version: string;
      decision: string | null;
    }[]
  >(await fetch(`${BASE}/api/artifacts/${runId}/pending`));
}

export async function listDiagnoses(runId: string): Promise<DiagnosisView[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}/diagnoses`));
}

export async function listFeedbackPackets(runId: string): Promise<FeedbackPacketView[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}/feedback-packets`));
}

export async function listMemoryCandidates(runId: string): Promise<RunMemoryEventView> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}/memory-candidates`));
}

export async function listEpisodeMemory(runId: string): Promise<RunMemoryEventView> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}/episode-memory`));
}

export async function getSelfEvolutionLevers(runId: string): Promise<SelfEvolutionLeversView> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}/self-evolution/levers`));
}

export async function listSelfEvolutionMutations(
  runId: string,
): Promise<RunMemoryEventView> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}/self-evolution/mutations`));
}

export async function createSelfEvolutionMutation(
  runId: string,
  body: {
    lever_id: string;
    agent: string;
    path: string;
    proposed_content: string;
    rationale?: string;
  },
): Promise<SelfEvolutionMutation> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${runId}/self-evolution/mutations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function approveSelfEvolutionMutation(
  runId: string,
  mutationId: string,
  reviewerNote = "approved from Commander UI",
): Promise<SelfEvolutionMutationDecision> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${runId}/self-evolution/mutations/${encodeURIComponent(mutationId)}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer_note: reviewerNote }),
    }),
  );
}

export async function rejectSelfEvolutionMutation(
  runId: string,
  mutationId: string,
  reviewerNote = "rejected from Commander UI",
): Promise<SelfEvolutionMutationDecision> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${runId}/self-evolution/mutations/${encodeURIComponent(mutationId)}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer_note: reviewerNote }),
    }),
  );
}

export async function getCommanderObservability(
  runId: string,
): Promise<CommanderObservabilityView> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs/${runId}/commander-observability`));
}

export async function getCommanderAttributionEval(
  project = "pimc",
): Promise<CommanderAttributionEvalView> {
  const url = apiUrl(`${BASE}/api/evaluation/commander-attribution`);
  url.searchParams.set("project", project);
  return jsonOrThrow(await fetch(url));
}

export async function getEvaluationScorecard(runId: string): Promise<EvaluationScorecard> {
  return jsonOrThrow(await fetch(`${BASE}/api/evaluation/runs/${runId}/scorecard`));
}

export async function getPostTrainingExport(
  runId: string,
): Promise<PostTrainingExportManifest> {
  return jsonOrThrow(await fetch(`${BASE}/api/evaluation/runs/${runId}/post-training-export`));
}

export async function createPostTrainingExport(
  runId: string,
  includeDrafts = false,
): Promise<PostTrainingExportManifest> {
  const url = apiUrl(`${BASE}/api/evaluation/runs/${runId}/post-training-export`);
  url.searchParams.set("include_drafts", includeDrafts ? "true" : "false");
  return jsonOrThrow(await fetch(url, { method: "POST" }));
}

export async function getArtifactEvaluationSummary(
  runId: string,
  agentDir: string,
  stem: string,
  version: string,
): Promise<ArtifactEvaluationSummary> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/evaluation/runs/${runId}/artifacts/${agentDir}/${stem}/${version}/summary`),
  );
}

export async function listArtifactEvaluations(
  runId: string,
  agentDir: string,
  stem: string,
  version: string,
): Promise<ArtifactEvaluationReport[]> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/evaluation/runs/${runId}/artifacts/${agentDir}/${stem}/${version}`),
  );
}

export async function getRunObservability(
  runId: string,
  limit = 120,
): Promise<RunObservabilityView> {
  const url = apiUrl(`${BASE}/api/runs/${runId}/observability`);
  url.searchParams.set("limit", String(limit));
  return jsonOrThrow(await fetch(url));
}

export async function startFeedbackLoop(
  runId: string,
  diagnosisVersion: string,
): Promise<FeedbackLoopStartResult> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/runs/${runId}/feedback-loop/${diagnosisVersion}/start`, {
      method: "POST",
    }),
  );
}

export async function approveMemoryCandidate(
  runId: string,
  candidateId: string,
): Promise<MemoryPromotionView> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/agents/memory-candidates/${runId}/${encodeURIComponent(candidateId)}/approve`, {
      method: "POST",
    }),
  );
}

export async function rejectMemoryCandidate(
  runId: string,
  candidateId: string,
  reviewerNote = "rejected from Commander UI",
): Promise<MemoryPromotionView> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/agents/memory-candidates/${runId}/${encodeURIComponent(candidateId)}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer_note: reviewerNote }),
    }),
  );
}

export async function markMemoryCandidateStale(
  runId: string,
  candidateId: string,
  reviewerNote = "marked stale from Commander UI",
): Promise<MemoryPromotionView> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/agents/memory-candidates/${runId}/${encodeURIComponent(candidateId)}/stale`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer_note: reviewerNote }),
    }),
  );
}

export async function supersedeMemoryCandidate(
  runId: string,
  candidateId: string,
  supersededBy = "",
  reviewerNote = "superseded from Commander UI",
): Promise<MemoryPromotionView> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/agents/memory-candidates/${runId}/${encodeURIComponent(candidateId)}/supersede`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer_note: reviewerNote, superseded_by: supersededBy }),
    }),
  );
}

export async function getTrace(runId: string): Promise<TraceManifest> {
  return jsonOrThrow(await fetch(`${BASE}/api/traces/${runId}`));
}

export async function getContextRun(runId: string): Promise<ContextRunView> {
  return jsonOrThrow(await fetch(`${BASE}/api/context/runs/${runId}`));
}

export async function getContextManifest(
  runId: string,
  manifestId: string,
): Promise<ContextManifestV2> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/context/runs/${runId}/manifests/${manifestId}`),
  );
}

export async function getContextRaw(
  runId: string,
  rawRef: string,
): Promise<ContextRawView> {
  const encodedRef = rawRef.split("/").map(encodeURIComponent).join("/");
  return jsonOrThrow(
    await fetch(`${BASE}/api/context/runs/${runId}/raw/${encodedRef}`),
  );
}

export async function previewContext(body: {
  agent: string;
  project: string;
  task: string;
  upstream?: Record<string, string>;
}): Promise<ContextManifestV2> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/context/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export type DebateTranscript = {
  exists: boolean;
  agent: string;
  path?: string;
  text: string;
};

export async function getDebateTranscript(
  runId: string,
  agentDir: string,
): Promise<DebateTranscript> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/artifacts/${runId}/${agentDir}/debate`),
  );
}

// ---------- agent context configuration ----------
export async function getAgentContext(agent: string, project?: string): Promise<AgentContextView> {
  const suffix = project ? `?project=${encodeURIComponent(project)}` : "";
  return jsonOrThrow(await fetch(`${BASE}/api/agents/${agent}/context${suffix}`));
}

export async function createAgentContextItem(
  agent: string,
  body: { category: string; filename: string; content: string },
): Promise<AgentContextFile> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/agents/${agent}/context/items`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function updateAgentContextItem(
  agent: string,
  body: { path: string; content: string },
): Promise<AgentContextFile> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/agents/${agent}/context/items`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function deleteAgentContextItem(
  agent: string,
  path: string,
): Promise<{ status: string; path: string }> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/agents/${agent}/context/items`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),
  );
}

export async function updateAgentResearchSites(
  agent: string,
  sites: AgentResearchSite[],
): Promise<AgentResearchSite[]> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/agents/${agent}/context/research-sites`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sites }),
    }),
  );
}

export async function updateAgentCodeRepositories(
  agent: string,
  project: string,
  repositories: AgentCodeRepository[],
): Promise<AgentCodeRepository[]> {
  return jsonOrThrow(
    await fetch(
      `${BASE}/api/agents/${agent}/context/code-repositories?project=${encodeURIComponent(project)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repositories }),
      },
    ),
  );
}

export async function getCodingWorkspace(params: {
  project: string;
  runId?: string;
  source?: string;
}): Promise<CodingWorkspace> {
  const url = apiUrl(`${BASE}/api/agents/coding/workspace`);
  url.searchParams.set("project", params.project);
  url.searchParams.set("source", params.source ?? "auto");
  if (params.runId) {
    url.searchParams.set("run_id", params.runId);
  }
  return jsonOrThrow(await fetch(url));
}

export async function getCodingWorkspaceFile(params: {
  project: string;
  source: string;
  path: string;
}): Promise<CodeFileContent> {
  const url = apiUrl(`${BASE}/api/agents/coding/workspace/file`);
  url.searchParams.set("project", params.project);
  url.searchParams.set("source", params.source);
  url.searchParams.set("path", params.path);
  return jsonOrThrow(await fetch(url));
}

export async function updateCodingMemoryItems(
  items: CodingMemoryItem[],
): Promise<CodingMemoryItem[]> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/agents/coding/workspace/memory`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    }),
  );
}

// ---------- new endpoints ----------
export async function getStats(): Promise<Stats> {
  return jsonOrThrow(await fetch(`${BASE}/api/stats`));
}
export async function listZones(): Promise<ZoneSummary[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/knowledge/zones`));
}
export async function listZoneItems(zone: string, limit = 20): Promise<KBItem[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/knowledge/${zone}/items?limit=${limit}`));
}
export async function listQuarantineItems(params?: {
  limit?: number;
  project?: string;
  memoryType?: KnowledgeMemoryType;
  includeMock?: boolean;
  includeSuperseded?: boolean;
}): Promise<KBItem[]> {
  const url = apiUrl(`${BASE}/api/knowledge/quarantine/items`);
  url.searchParams.set("limit", String(params?.limit ?? 20));
  addKnowledgeParams(url, {
    project: params?.project,
    memoryType: params?.memoryType,
    includeMock: params?.includeMock,
    includeSuperseded: params?.includeSuperseded,
  });
  return jsonOrThrow(await fetch(url));
}
export async function searchKnowledge(params: KnowledgeSearchParams): Promise<KnowledgeSearchHit[]> {
  const url = apiUrl(`${BASE}/api/knowledge/search`);
  addKnowledgeParams(url, params);
  return jsonOrThrow(await fetch(url));
}
export async function searchQuarantine(
  params: Omit<KnowledgeSearchParams, "zone" | "zones" | "profile">,
): Promise<KnowledgeSearchHit[]> {
  const url = apiUrl(`${BASE}/api/knowledge/quarantine/search`);
  addKnowledgeParams(url, params);
  return jsonOrThrow(await fetch(url));
}
export async function searchZone(
  zone: string,
  q: string,
  topK = 5,
): Promise<KnowledgeSearchHit[]> {
  const url = apiUrl(`${BASE}/api/knowledge/${zone}/search`);
  url.searchParams.set("q", q);
  url.searchParams.set("top_k", String(topK));
  return jsonOrThrow(await fetch(url));
}
function addKnowledgeParams(
  url: URL,
  params: Partial<KnowledgeSearchParams> & {
    limit?: number;
    memoryType?: KnowledgeMemoryType;
  },
): void {
  if (params.q !== undefined) {
    url.searchParams.set("q", params.q);
  }
  if (params.topK !== undefined) {
    url.searchParams.set("top_k", String(params.topK));
  }
  if (params.zone) {
    url.searchParams.set("zone", params.zone);
  }
  if (params.zones?.length) {
    url.searchParams.set("zones", params.zones.join(","));
  }
  if (params.project) {
    url.searchParams.set("project", params.project);
  }
  if (params.memoryType) {
    url.searchParams.set("memory_type", params.memoryType);
  }
  if (params.includeMock !== undefined) {
    url.searchParams.set("include_mock", String(params.includeMock));
  }
  if (params.includeSuperseded !== undefined) {
    url.searchParams.set("include_superseded", String(params.includeSuperseded));
  }
  if (params.profile) {
    url.searchParams.set("profile", params.profile);
  }
}
export async function listProjects(): Promise<ProjectSummary[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/projects`));
}
export async function getProject(name: string): Promise<ProjectSummary> {
  return jsonOrThrow(await fetch(`${BASE}/api/projects/${name}`));
}
export async function listEvents(limit = 80): Promise<EventEntry[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/events?limit=${limit}`));
}

export async function getRunTimeline(runId: string, limit = 500): Promise<TimelineItem[]> {
  const url = apiUrl(`${BASE}/api/timeline/runs/${runId}`);
  url.searchParams.set("limit", String(limit));
  return jsonOrThrow(await fetch(url));
}

export async function getRunWorkLog(
  runId: string,
  agent?: string,
  limit = 200,
): Promise<WorkLogView> {
  const url = apiUrl(`${BASE}/api/timeline/runs/${runId}/worklog`);
  url.searchParams.set("limit", String(limit));
  if (agent) {
    url.searchParams.set("agent", agent);
  }
  return jsonOrThrow(await fetch(url));
}

export async function getReportBundle(runId: string): Promise<ReportBundle> {
  return jsonOrThrow(await fetch(`${BASE}/api/reports/${runId}`));
}

export async function regenerateReportBundle(runId: string): Promise<ReportBundle> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/reports/${runId}/regenerate`, {
      method: "POST",
    }),
  );
}

export function reportFileUrl(runId: string, filename: string): string {
  return `${BASE}/api/reports/${runId}/files/${encodeURIComponent(filename)}`;
}

export async function getConfigSnapshot(): Promise<ConfigSnapshot> {
  return jsonOrThrow(await fetch(`${BASE}/api/config`));
}

export async function getAgentLlmConfig(): Promise<AgentLlmConfigView> {
  return jsonOrThrow(await fetch(`${BASE}/api/config/agent-llm`));
}

export async function updateAgentLlmConfig(params: {
  agents: AgentLlmUpdateRow[];
  actor?: string;
}): Promise<AgentLlmConfigView> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/config/agent-llm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agents: params.agents,
        actor: params.actor ?? "frontend",
      }),
    }),
  );
}

export async function validateConfigFile(
  name: string,
  text: string,
): Promise<ConfigValidationResult> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/config/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, text }),
    }),
  );
}

export async function previewConfigDiff(
  name: string,
  text: string,
): Promise<ConfigDiffResult> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/config/preview-diff`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, text }),
    }),
  );
}

export async function applyConfigFile(params: {
  name: string;
  text: string;
  actor?: string;
  confirmHighRisk?: boolean;
}): Promise<ConfigFile> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/config/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: params.name,
        text: params.text,
        actor: params.actor ?? "user",
        confirm_high_risk: params.confirmHighRisk ?? false,
      }),
    }),
  );
}

export async function listExecutionPlots(runId: string): Promise<ExecutionPlot[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/execution/${runId}/plots`));
}

export function executionPlotUrl(plot: ExecutionPlot): string {
  const url = plot.url.startsWith("http") ? plot.url : `${BASE}${plot.url}`;
  return `${url}?v=${encodeURIComponent(String(plot.updated_at))}`;
}

// ---------- chat / Commander ----------
export type ChatMessageView = {
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp: string;
  state: string | null;
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_result: Record<string, unknown> | null;
};

export type Conversation = {
  conv_id: string;
  project: string;
  state: string;
  linked_run_id: string | null;
  auto_mode: boolean;
  metric_targets: Record<string, number>;
  messages: ChatMessageView[];
};

export async function createConversation(project = "pimc"): Promise<Conversation> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/chat/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project }),
    }),
  );
}

export async function getConversation(convId: string): Promise<Conversation> {
  return jsonOrThrow(await fetch(`${BASE}/api/chat/conversations/${convId}`));
}

export async function sendChatMessage(convId: string, text: string): Promise<Conversation> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/chat/conversations/${convId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  );
}

export async function setConversationAutoMode(
  convId: string,
  autoMode: boolean,
): Promise<Conversation> {
  return jsonOrThrow(
    await fetch(`${BASE}/api/chat/conversations/${convId}/auto_mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_mode: autoMode }),
    }),
  );
}

const BROWSER_ORIGIN = typeof window === "undefined" ? "" : window.location.origin;
export const WS_BASE =
  process.env.NEXT_PUBLIC_WS_URL ||
  (CONFIGURED_BACKEND_URL || BROWSER_ORIGIN).replace(/^http/, "ws");
