export type RunState = 'running' | 'finished' | 'waiting_approval' | 'stopped' | 'new';

export interface EventItem {
  index: number;
  ts: string;
  stage: string;
  message: string;
  [extra: string]: unknown;
}

export interface RunAlert {
  level: 'error' | 'warn' | 'info';
  code: string;
  title: string;
  detail: string;
  error?: string;
  since: string | null;
  params?: Record<string, string | number | null>;
}

export interface RunSummary {
  id: string;
  title: string;
  task_id: string;
  state: RunState;
  kind: string;
  alerts: RunAlert[];
  phase: string;
  pid: number | null;
  step1_round: string | null;
  started: string | null;
  last_event: { ts: string; stage: string; message: string } | null;
  events: number;
  has_report: boolean;
  routes: { id: string; status: string; iterations: number }[];
}

/** Values per dataset name (any dataset the project declares). */
export type Pair = Record<string, number | null>;

/** The project's metric declaration (.mars/project.yaml metrics). */
export interface MetricMeta { label?: string; unit?: string; better: 'lower' | 'higher' }
export interface MetricsDecl { primary: string | null; fields: Record<string, MetricMeta> }

/** A contract gate on a check's output, as the core judged it. */
export interface CheckGate { check: string; field: string; max?: number; min?: number; stop?: boolean; values: unknown[]; ok: boolean }
export interface Declaration { ready: boolean; metrics: MetricsDecl; checks: string[]; datasets: string[]; problems: string[] }
export interface CheckGates { all?: boolean | null; stop?: boolean | null; gates: CheckGate[] }

/** One dataset at one stage (screen or verify): metrics, the baseline's, and how much worse (negative = better). */
export interface StageEntry {
  metrics?: Record<string, number> | null;
  baseline_metrics?: Record<string, number> | null;
  worse_by?: Record<string, number> | null;
  primary?: string | null;
  margin?: number | null;
  passed?: boolean | null;
  ok?: boolean;
  error?: string | null;
  gates?: Record<string, boolean> | null;
}

export interface IterationRow {
  iteration: number;
  summary: string;
  branch: string | null;
  commit: string | null;
  implemented: boolean | null;
  stopped_at: string | null;
  check_gates: CheckGates;
  screen_gap: Pair;
  screen_passed: boolean | null;
  verify_passed: boolean | null;
  verdict: string | null;
  assessment: string;
  decision_error: boolean;
  next_change: string | null;
  implementation: { ok: boolean | null; turns: number; files: string[] };
  prequick: { passed: boolean | null; metrics: Record<string, number> | null; gates: { metric: string; min?: number; max?: number; value: number | null; ok: boolean }[] | null; error: string | null };
  screen: Record<string, StageEntry>;
  diagnostics?: { name?: string; question?: string; result?: Record<string, unknown>; error?: string }[];
  verify: Record<string, StageEntry>;
  plots: Record<string, string>;
  parent_iteration: number | null;
  base_commit: string | null;
  datasets?: string[];
}

export interface IterationDiff {
  base_commit: string | null;
  commit: string | null;
  files: { path: string; status: 'added' | 'modified' | 'deleted' | string; patch: string; truncated: boolean }[];
}

export interface CodingSummary {
  turns: { turn: number; status: string; seconds: number | null; outcome: string; summary: string; violations: string[] | null; reverted?: string[] | null; denials?: unknown }[];
  tools: Record<string, number>;
  tool_errors: number;
  permissions: { approved: number; denied: number };
  files_touched: string[];
  started: number | null;
  ended: number | null;
}

export interface CoreJob {
  kind: 'train' | 'codingagent' | string;
  run_id: string;
  route: string;
  iteration: number | null;
  stage: string | null;
  dataset: string | null;
  seed?: number | null;
  baseline?: boolean;
  started: number | string | null;
  target: string | null;
  progress: { step?: number | null; total?: number | null; epoch?: number | null } | null;
}

export interface Jobs {
  core: CoreJob[];
  app: { kind: 'hpo' | 'verify'; id: string; title: string; state: string; counts?: Record<string, number> }[];
  core_error: string | null;
  max_parallel: number;
  usage: { input_tokens: number; output_tokens: number; cached_tokens: number; known_calls: number; unknown_calls: number };
  recent: { run_id: string; route: string; iteration: number; stage: string; passed: boolean; modified: number }[];
}

export interface Route {
  recommended_iteration?: number | null;
  id: string;
  title: string;
  status: string;
  running_iteration: number | null;
  best: { iteration: number; branch: string; score: number } | null;
  iterations: IterationRow[];
}

export interface RankingItem {
  id: string;
  title: string;
  score: number | string | null; // judges' mean, or a label such as "用户方案" for a refine proposal
  precheck: string[];
  duplicate_of: string | null;
  one_sentence: string;
  handed_off: boolean;
}

export interface Step1Round {
  round: string;
  stages: { key: string; label: string; done: boolean }[];
  directions: { id: string; name: string; hypothesis: string }[];
  cards: { total: number; verified: number };
  ranking: RankingItem[];
  handoff: string[];
}

export interface RunDetail extends Omit<RunSummary, 'routes'> {
  contract: Record<string, unknown>;
  metrics: MetricsDecl;
  step1: Step1Round[];
  routes: Route[];
  datasets: string[];
  recommended?: { route: string; iteration: number } | null;
  followups?: Followup[];
  user_proposal: string | null;
  implementation: (Omit<Implementation, 'turns'> & { turns?: unknown[] }) | null;
}

export interface Followup {
  kind: 'seeds' | 'search';
  route: string;
  iteration: number;
  dataset?: string;
  epochs?: number;
  commit: string;
  source: { file: string; sha256: string };
  rows: { role?: string; seed?: number | string; dataset?: string; params?: Record<string, unknown>;
          metrics: Record<string, number | null>; baseline_metrics?: Record<string, number | null> }[];
}

export interface ApprovalCandidate {
  id: string;
  title: string;
  score: number | string | null;
  judge_scores: number[];
  precheck: string[];
  duplicate_of: string | null;
  one_sentence: string;
  mechanism_hypothesis: string;
  removed_or_shrunk: string;
  added_capability: string;
  resource_estimate: string;
  constraints_argument: string;
  history_relation: string;
  falsification: string;
  evidence: string[];
  critiques: { fatal: number; major: number; minor: number; verdicts: string[] };
  rebuttals: number;
  proposal?: Record<string, unknown>;
  critique_details?: unknown[];
  rebuttal_details?: unknown[];
  evidence_cards?: unknown[];
}

export interface Approval {
  enabled: boolean;
  pending: boolean;
  round?: string;
  routes_budget?: number;
  candidates?: ApprovalCandidate[];
  default_selection?: string[];
}

export interface Template {
  name: string;
  title: string;
  yaml: string;
  human_approval: boolean;
  contract: Record<string, unknown>;
}

export interface ProjectConfig {
  name: string;
  objective?: string;
  repo?: { path: string; ref: string; commit: string; editable_files: string[] | string };
  datasets?: { name: string; config: string; data: string }[];
  context_sources?: string[];
  remote?: { enabled?: boolean; name?: string; device?: string; parallel?: number; data_path?: string };
  model_mode?: string;
}

/** Steps per epoch for each bound dataset, from the core's own data windowing. */
export type EpochInfo = Record<string, { steps_per_epoch: number; epochs: number }>;

/** A note left for a route; `answer` appears once a decision has read it. */
export interface RouteNote { ts: string; text: string; answer?: { adopted: boolean; reason?: string; iteration?: number } }

export interface Project {
  id: string; name: string; path: string; runs_dir: string; reports_dir: string;
  connected: boolean; builtin: boolean; demo?: boolean; preferred?: boolean; config: ProjectConfig;
}
export interface FolderEntries { path: string; parent: string; entries: { name: string; path: string; directory: boolean }[] }
export interface PreflightCheck { key: string; title: string; status: 'pass' | 'warn' | 'error'; detail: string }
export interface Artifact {
  id: string; kind: 'fig' | 'code' | 'report' | 'record'; title: string; run_id: string;
  route: string | null; iteration: number | null; step1_round?: string | null; source: string; path: string; relative_path: string;
  size: number | null; modified: number; extension: string; virtual: boolean; commit?: string;
}
export interface ArtifactPreview { format: 'image' | 'text' | 'markdown' | 'download' | 'diff'; text?: string; truncated?: boolean; diff?: IterationDiff }
export interface ProjectOverview {
  stats: { runs: number; routes: number; iterations: number; sources: number; passed: number };
  contract: Record<string, unknown>; datasets: string[];
}

let activeProject = 'current';
export function setApiProject(id: string) { activeProject = id; }
export function projectUrl(path: string): string {
  return `${path}${path.includes('?') ? '&' : '?'}project=${encodeURIComponent(activeProject)}`;
}

export interface SearchKey {
  key: string;
  type?: 'float' | 'int' | 'str' | 'bool' | 'categorical';
  low?: number;
  high?: number;
  log?: boolean;
  choices?: unknown[];
  declared: boolean;
}

export interface HpoCandidate {
  run_id: string;
  route: string;
  iteration: number;
  dir: string;
  change: string;
  stopped_at: string | null;
  screen: Record<string, number | null>;
}

export interface HpoMeta {
  search_space: { datasets: string[]; keys: SearchKey[]; metrics: MetricsDecl };
  baseline_dir: string;
  max_parallel: number;
  max_trials: number;
  candidates: HpoCandidate[];
}

export type HpoParam =
  | { key: string; mode: 'range'; low: number | string; high: number | string; log: boolean; type?: string }
  | { key: string; mode: 'choices'; choices: unknown[]; type?: string };

export interface HpoSpec {
  environment?: ExecutionEnvironment;
  title: string;
  target: { kind: 'baseline' | 'candidate'; dir: string; label: string };
  datasets: string[];
  steps: number;
  seed: number;
  n_trials: number;
  parallel: number;
  params: HpoParam[];
  metric?: string;
  direction?: 'minimize' | 'maximize';
  created?: string;
}

export interface EvalSummary {
  ok: boolean;
  metrics: Record<string, number> | null;
  elapsed_seconds: number | null;
  executed_on: string | null;
  error: string | null;
}

export interface HpoTrial {
  number: number;
  state: 'complete' | 'fail' | 'running' | 'interrupted' | 'waiting' | 'pruned';
  value: number | null;
  params: Record<string, unknown>;
  datasets: Record<string, EvalSummary>;
  error: string | null;
  started: string | null;
  seconds: number | null;
}

export type HpoState = 'running' | 'stopping' | 'paused' | 'finished';

export interface HpoSummary {
  id: string;
  spec: HpoSpec;
  state: HpoState;
  counts: { complete: number; fail: number; running: number; interrupted: number };
  best: HpoTrial | null;
  best_gain: number | null;
}

export interface HpoDetail extends HpoSummary {
  running: boolean;
  stopping: boolean;
  reference: { datasets?: Record<string, EvalSummary>; value?: number | null; error?: string } | null;
  trials: HpoTrial[];
}

export interface VerifyEval extends EvalSummary {
  run_dir?: string | null;
  plots?: string[] | null;
  info?: Record<string, unknown> | null;
}

export interface Implementation {
  ok: boolean;
  candidate_dir: string | null;
  branch: string | null;
  commit: string | null;
  changed_files: string[] | null;
  violations: string[] | null;
  checks: Record<string, Record<string, unknown>> | null;
  measure_checks: Record<string, Record<string, unknown>> | null;
  gates: CheckGates | null;
  turns: { turn: number; status: string; seconds: number | null; violations: string[] | null; reverted: string[] | null; error: string | null }[];
  summary: string;
}

export interface VerifyMetric { candidate: number | null; baseline: number | null; worse_by: number | null; max_worse: number | null; ok: boolean | null }

export interface VerifyComparisonRow {
  metrics: Record<string, VerifyMetric>;
  plots: { candidate: string[]; baseline: string[] };
}

export type VerifyStage = 'implementing' | 'evaluating' | 'done' | 'failed' | 'interrupted';

export interface VerifySummary {
  id: string;
  title: string;
  run_id: string;
  stage: VerifyStage;
  running: boolean;
  datasets: string[];
  steps: number;
  created: string;
  error: string | null;
  all_ok: boolean | null;
}

export interface VerifyDetail extends VerifySummary {
  environment?: ExecutionEnvironment | null;
  spec: string;
  parent: string;
  seed: number;
  task: string;
  implementation: Implementation | null;
  results: Record<string, { baseline?: VerifyEval; candidate?: VerifyEval }>;
  targets: { gates?: { metric: string; max_worse?: number }[]; checks?: Omit<CheckGate, 'values' | 'ok'>[] };
  metrics?: MetricsDecl;
  comparison: { metrics: MetricsDecl; datasets: Record<string, VerifyComparisonRow>; check_gates: CheckGates | Record<string, never>; all_ok: boolean; full_budget: boolean };
}

export interface VerifyMeta {
  templates: { name: string; title: string }[];
  datasets: string[];
  max_parallel: number;
  parents: { branch: string; label: string; change: string; run_id?: string; route?: string; iteration?: number }[];
}

export interface ModelProvider {
  api_base?: string;
  api_key_env?: string;
  models?: string[];
  [extra: string]: unknown;
}

export interface ModelRole {
  provider?: string;
  model?: string;
  max_tokens?: number;
  timeout?: number;
  idle_timeout?: number;
  stream?: boolean;
  json_mode?: boolean;
  single_user?: boolean;
  thinking?: boolean;
  reasoning_effort?: string;
  extra_body?: Record<string, unknown>;
  concurrency?: number;
  [extra: string]: unknown;
}

export interface ProbeResult {
  skipped?: boolean;
  ok?: boolean;
  error?: string;
  seconds?: number;
  status?: number;
  reply?: string;
  reasoning_chars?: number;
  think_tag?: boolean;
  native_tool_call?: boolean;
  valid_json_tail?: boolean;
  sse_body_for_non_stream?: boolean;
  first_chunk_s?: number | null;
  transport?: 'stream' | 'nonstream';
  finish_reason?: string | null;
  arguments_valid?: boolean;
  round_trip?: ProbeResult;
}

export interface ModelCheck {
  id: string;
  role: string;
  status: 'running' | 'done' | 'failed';
  started: string;
  finished?: string;
  skip_thinking: boolean;
  passed?: boolean;
  error: string | null;
  config: string;
  report: {
    error?: string;
    model?: string;
    api_base?: string;
    report_version?: number;
    ready?: boolean;
    capability_transport?: 'stream' | 'nonstream' | null;
    raw?: ProbeResult;
    nonstream?: ProbeResult;
    stream?: ProbeResult;
    json?: ProbeResult;
    structured?: ProbeResult;
    tools?: ProbeResult;
    thinking?: Record<string, ProbeResult>;
    thinking_switches?: Record<string, { status: string; on: string; off: string }>;
    suggested?: { stream?: boolean; json_mode?: boolean; note_raw?: string; coder?: string;
      to_disable_thinking?: Record<string, unknown>; thinking_switch_effect?: Record<string, number>; thinking_note?: string };
  } | null;
}

export interface ModelsView {
  editable: boolean;
  path: string;
  exists: boolean;
  error: string | null;
  env_files: string[];
  providers: Record<string, ModelProvider>;
  roles: Record<string, ModelRole>;
  fallbacks: Record<string, string>;
  retries: number | null;
  keys: Record<string, { set: boolean; in_file: string | null; in_environment: boolean }>;
  roles_required: string[];
  check_available: boolean;
  check_script: string;
  checks: Record<string, ModelCheck>;
}

const put = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!path.startsWith('/api/projects') && !path.startsWith('/api/filesystem')) headers.set('X-Mars-Project', activeProject);
  const response = await fetch(path, { ...init, headers });
  const text = await response.text();
  if (!response.ok) {
    let detail = text;
    try {
      detail = (JSON.parse(text) as { detail?: string }).detail ?? text;
    } catch {
      /* plain-text error body */
    }
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  const type = response.headers.get('content-type') ?? '';
  return (type.includes('application/json') ? JSON.parse(text) : text) as T;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });

export type ExecutionEnvironment = 'local' | 'remote';
export interface ExecutionOption {
  id: ExecutionEnvironment;
  label: string;
  device?: string;
  name?: string;
  configured: boolean;
}

/** After a start: wait (up to ~20 s) until the core has written the run, so its page does not open on a 404. */
export async function openStartedRun(id: string): Promise<void> {
  for (let i = 0; i < 40; i++) {
    try { await api.run(id); break; } catch { await new Promise((done) => setTimeout(done, 500)); }
  }
  window.location.hash = `#/runs/${encodeURIComponent(id)}`;
}

export const api = {
  executionEnvironments: () => request<{ options: ExecutionOption[] }>('/api/execution-environments'),
  health: () => request<{ core_root: string; core_found: boolean; api_version: number | null; error: string | null }>('/api/health'),
  runs: () => request<RunSummary[]>('/api/runs'),
  run: (id: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  events: (id: string) => request<EventItem[]>(`/api/runs/${encodeURIComponent(id)}/events`),
  record: (id: string, route: string, k: number) =>
    request<Record<string, unknown>>(`/api/runs/${encodeURIComponent(id)}/routes/${encodeURIComponent(route)}/iterations/${k}`),
  report: (id: string) => request<string>(`/api/runs/${encodeURIComponent(id)}/report`),
  log: (id: string) => request<string>(`/api/runs/${encodeURIComponent(id)}/log`),
  approval: (id: string) => request<Approval>(`/api/runs/${encodeURIComponent(id)}/approval`),
  templates: () => request<Template[]>('/api/templates'),
  declaration: () => request<Declaration>('/api/declaration'),
  approve: (id: string, approve: string[]) => post<{ run_id: string }>(`/api/runs/${encodeURIComponent(id)}/approval`, { approve }),
  start: (run_id: string, contract_yaml: string, human_approval: boolean) =>
    post<{ run_id: string }>('/api/runs', { run_id, contract_yaml, human_approval }),
  resume: (id: string) => post<{ run_id: string }>(`/api/runs/${encodeURIComponent(id)}/resume`),
  stop: (id: string) => post<{ run_id: string }>(`/api/runs/${encodeURIComponent(id)}/stop`),
  streamUrl: (id: string) => projectUrl(`/api/runs/${encodeURIComponent(id)}/stream`),
  models: () => request<ModelsView>('/api/models'),
  modelsSave: (body: { providers: Record<string, ModelProvider>; roles: Record<string, Record<string, unknown>>; fallbacks: Record<string, string>; retries: number | null }) =>
    put<ModelsView>('/api/models', body),
  modelsKey: (name: string, value: string) =>
    put<{ env_file: string; warning: string | null; keys: ModelsView['keys'] }>('/api/models/keys', { name, value }),
  modelsCheck: (role: string, skip_thinking: boolean) => post<{ id: string }>('/api/models/check', { role, skip_thinking }),
  modelsCheckStatus: (id: string) => request<ModelCheck>(`/api/models/check/${encodeURIComponent(id)}`),
  diff: (id: string, route: string, k: number) =>
    request<IterationDiff>(`/api/runs/${encodeURIComponent(id)}/routes/${encodeURIComponent(route)}/iterations/${k}/diff`),
  coding: (id: string, route: string, k: number) =>
    request<CodingSummary>(`/api/runs/${encodeURIComponent(id)}/routes/${encodeURIComponent(route)}/iterations/${k}/coding`),
  runPlotUrl: (id: string, route: string, name: string, k?: number) =>
    projectUrl(`/api/runs/${encodeURIComponent(id)}/plots/${encodeURIComponent(route)}/${encodeURIComponent(name)}${k === undefined ? '' : `?iteration=${k}`}`),
  evolution: (id: string, route: string | null, iteration: number | null = null) =>
    request<import('./components/EvolutionStory').EvolutionData>(`/api/runs/${encodeURIComponent(id)}/evolution${route ? `?route=${encodeURIComponent(route)}${iteration ? `&iteration=${iteration}` : ''}` : ''}`),
  step1Story: (id: string, round: string) =>
    request<import('./components/EvolutionStory').Step1Story | null>(`/api/runs/${encodeURIComponent(id)}/step1/${encodeURIComponent(round)}/story`),
  notes: (id: string, route: string) => request<RouteNote[]>(`/api/runs/${encodeURIComponent(id)}/routes/${encodeURIComponent(route)}/notes`),
  addNote: (id: string, route: string, text: string) =>
    post<RouteNote[]>(`/api/runs/${encodeURIComponent(id)}/routes/${encodeURIComponent(route)}/notes`, { text }),
  jobs: (remote = false) => request<Jobs>(`/api/jobs${remote ? '?remote_progress=true' : ''}`),
  plotUrl: (path: string) => projectUrl(`/api/plot?path=${encodeURIComponent(path)}`),
  exportUrl: (id: string, format: 'docx' | 'pptx' | 'xlsx') => projectUrl(`/api/runs/${encodeURIComponent(id)}/export/${format}`),
  verifyMeta: () => request<VerifyMeta>('/api/verify/meta'),
  verifyList: () => request<VerifySummary[]>('/api/verify'),
  verify: (id: string) => request<VerifyDetail>(`/api/verify/${encodeURIComponent(id)}`),
  verifyCreate: (body: { title: string; template: string; spec: string; datasets: string[]; steps: number; seed: number; parent: string; environment?: ExecutionEnvironment }) =>
    post<{ id: string }>('/api/verify', body),
  verifyControl: (id: string, action: 'stop' | 'resume') => post<{ id: string }>(`/api/verify/${encodeURIComponent(id)}/${action}`),
  refine: (run_id: string, contract_yaml: string, proposal: string, human_approval: boolean) =>
    post<{ run_id: string }>('/api/refine', { run_id, contract_yaml, proposal, human_approval }),
  hpoMeta: () => request<HpoMeta>('/api/hpo/meta'),
  hpoList: () => request<HpoSummary[]>('/api/hpo'),
  hpo: (id: string) => request<HpoDetail>(`/api/hpo/${encodeURIComponent(id)}`),
  hpoCreate: (spec: HpoSpec) => post<{ id: string }>('/api/hpo', { spec }),
  hpoControl: (id: string, action: 'stop' | 'resume') => post<{ id: string }>(`/api/hpo/${encodeURIComponent(id)}/${action}`),
  projects: () => request<{ projects: Project[]; default_parent: string }>('/api/projects'),
  createProject: (path: string, config: ProjectConfig) => post<Project>('/api/projects', { path, config }),
  openProject: (path: string) => post<Project>('/api/projects/open', { path }),
  saveProject: (id: string, config: ProjectConfig) => put<Project>(`/api/projects/${encodeURIComponent(id)}`, config),
  stepsPerEpoch: (candidate_dir: string | null, overrides: Record<string, unknown> | null) =>
    post<EpochInfo>('/api/steps-per-epoch', { candidate_dir, overrides }),
  moveProject: (id: string, path: string) => put<Project>(`/api/projects/${encodeURIComponent(id)}/location`, { path, config: {} }),
  projectPreflight: (path: string, config: ProjectConfig) => post<{ checks: PreflightCheck[] }>('/api/projects/preflight', { path, config }),
  folders: (path = '', files = false) => request<FolderEntries>(`/api/filesystem?path=${encodeURIComponent(path)}&files=${files}`),
  overview: () => request<ProjectOverview>('/api/project/overview'),
  artifacts: () => request<Artifact[]>('/api/artifacts'),
  artifactPreview: (id: string) => request<ArtifactPreview>(`/api/artifacts/${encodeURIComponent(id)}/preview`),
  artifactUrl: (id: string, download = false) => projectUrl(`/api/artifacts/${encodeURIComponent(id)}/file${download ? '?download=true' : ''}`),
  artifactReveal: (id: string) => post<{ opened: boolean }>(`/api/artifacts/${encodeURIComponent(id)}/reveal`),
  checkContract: (body: { yaml?: string; contract?: Record<string, unknown>; environment?: ExecutionEnvironment }) => post<{ contract: Record<string, unknown>; yaml: string; errors: string[] }>('/api/contracts/check', body),
  saveContract: (pid: string, run_id: string, contract_yaml: string, human_approval: boolean) => post<{ path: string; saved: boolean }>(`/api/projects/${encodeURIComponent(pid)}/contracts`, { run_id, contract_yaml, human_approval }),
  proposals: (id: string, round: string) => request<Approval>(`/api/runs/${encodeURIComponent(id)}/proposals/${encodeURIComponent(round)}`),
  stopRoute: (id: string, route: string) => post<{ requested: boolean }>(`/api/runs/${encodeURIComponent(id)}/routes/${encodeURIComponent(route)}/stop`, {}),
};
