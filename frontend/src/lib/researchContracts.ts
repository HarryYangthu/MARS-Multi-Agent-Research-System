import { boundedFetch } from "./clientPolicy";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";

export type ResearchBudget = {
  search_candidates: number; deep_read_papers: number; concurrent_readers: number;
  proposal_candidates: number; implemented_candidates: number; debate_rounds: number; automatic_iterations: number;
  model_requests: number; tool_executions: number; research_activity_seconds: number;
  input_tokens: number; billed_output_tokens: number; model_cost_cny: number;
  request_input_tokens: number; request_output_tokens: number; coding_output_tokens: number;
  training_job_seconds: number; concurrent_training_jobs: number; max_gpus: number;
  training_process_seconds: number; gpu_seconds: number; operation_retries: number; repeated_error_limit: number;
};
export type ResearchProject = {
  schema_id: "research_project.v1"; project_id: string; display_name: string;
  paths: { code: string; knowledge: string[]; data: string[]; output: string };
  commands: { name: string; purpose: "check" | "train" | "evaluate"; executable: string; arguments: string[]; cwd: string; entrypoint_files: string[] }[];
  metrics: { name: string; unit: string; direction: "minimize" | "maximize"; target: number; tolerance: number }[];
  baseline_files: string[]; allowed_paths: string[]; protected_paths: string[];
  execution: { kind: "local" | "ssh"; device: "cpu" | "gpu"; connection_ref: string | null };
};
export type ResearchIssue = { field: string; code: string; message: string };
export type ResearchPreflight = { ready: boolean; project_sha256: string; issues: ResearchIssue[]; input_fingerprints: { path: string; sha256: string }[]; scope: string };
export type FrozenResearch = {
  task_sha256: string;
  task: { schema_id: "research_task.v1"; goal: string; mode: "bounded_auto" | "manual"; project: ResearchProject;
    budget: ResearchBudget; project_sha256: string; input_fingerprints: { path: string; sha256: string }[] };
};
export type ResearchCreated = {
  run_id: string; project: string; task: string; task_sha256: string;
  status: "created"; research_started: false;
  execution_admission: { ready: boolean; enforced_budget_fields: string[]; blockers: { code: string; message: string; fields: string[] }[] };
};

function localizeIssue(issue: ResearchIssue): ResearchIssue {
  const labels: Record<string, string> = {
    missing_directory: "所选代码目录不存在或当前不可访问。",
    missing_input: "所选知识资料或数据引用不存在或当前不可访问。",
    unreadable_input: "所选知识资料或数据引用没有读取权限。",
    unwritable_output: "请选择父目录已存在、可写且不会覆盖文件的输出目录。",
    symlink_output: "输出目录不能经过符号链接，请填写解析后的真实路径。",
    protected_output: "输出目录不能覆盖代码根目录或已保护的范围。",
    unsafe_scope: "声明的路径范围不安全，请检查符号链接和代码目录边界。",
    invalid_source_file: "声明的基线或入口文件不可用，请检查真实文件及符号链接。",
    missing_executable: "所选环境的可执行文件不存在或没有执行权限。",
    invalid_working_directory: "命令工作目录不可用，请检查目录及符号链接。",
    remote_preflight_pending: "当前尚未接通 SSH 合同环境预检，不能冻结此计划。",
    gpu_preflight_pending: "当前尚未接通 GPU 能力预检，不能冻结此计划。",
  };
  return { ...issue, message: labels[issue.code] || issue.message };
}

export class ResearchApiError extends Error {
  constructor(readonly status: number, readonly issues: ResearchIssue[]) {
    super(issues.map((issue) => issue.message).join("；") || `请求失败（HTTP ${status}）`);
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function issuesFrom(value: unknown): ResearchIssue[] {
  if (!record(value)) return [];
  const detail = value.detail;
  const items = Array.isArray(detail) ? detail : record(detail) && Array.isArray(detail.issues) ? detail.issues : [];
  return items.flatMap((item: unknown): ResearchIssue[] => {
    if (!record(item)) return [];
    const field = typeof item.field === "string" ? item.field : Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
    const message = typeof item.message === "string" ? item.message : typeof item.msg === "string" ? item.msg : "该项未通过后端校验";
    return [localizeIssue({ field, code: typeof item.code === "string" ? item.code : "invalid_field", message })];
  });
}
async function request(path: string, body: unknown | undefined, signal?: AbortSignal): Promise<unknown> {
  const response = await boundedFetch(`${BASE}/api/research-contracts${path}`, {
    method: body === undefined ? "GET" : "POST", signal,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  let value: unknown;
  try { value = await response.json(); } catch (cause: unknown) { if (response.ok) throw cause; value = null; }
  signal?.throwIfAborted();
  if (!response.ok) {
    const issues = issuesFrom(value);
    throw new ResearchApiError(response.status, issues.length ? issues : [{ field: "", code: "request_failed",
      message: response.status === 409 ? "声明文件已变化或计划已失效，请重新预检。" : response.status === 401 || response.status === 403 ? "当前连接未获授权，请检查本地服务会话。" : `后端未完成请求（HTTP ${response.status}）。` }]);
  }
  if (!record(value)) throw new Error("服务回执不完整，请核对后端记录。");
  return value;
}
export async function getResearchDefaults(signal?: AbortSignal): Promise<ResearchBudget> {
  const value = await request("/defaults", undefined, signal);
  if (!record(value) || !Object.values(value).every((item) => typeof item === "number" && Number.isFinite(item))) throw new Error("后端预算默认值无效。");
  return value as ResearchBudget;
}
export async function preflightResearch(project: ResearchProject, signal?: AbortSignal): Promise<ResearchPreflight> {
  const value = await request("/preflight", project, signal);
  if (!record(value) || typeof value.ready !== "boolean" || !Array.isArray(value.issues) || !value.issues.every((item: unknown) => record(item) && typeof item.field === "string" && typeof item.code === "string" && typeof item.message === "string") || typeof value.project_sha256 !== "string") throw new Error("预检回执不完整，请重新检查连接。");
  const result = value as ResearchPreflight;
  return { ...result, issues: result.issues.map(localizeIssue) };
}
export async function prepareResearch(body: { project: ResearchProject; goal: string; mode: "bounded_auto" | "manual"; budget: ResearchBudget }, signal?: AbortSignal): Promise<FrozenResearch> {
  const value = await request("/prepare", body, signal);
  if (!record(value) || typeof value.task_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.task_sha256) || !record(value.task) || !Array.isArray(value.task.input_fingerprints)) throw new Error("冻结回执不完整，尚未创建任务。");
  return value as FrozenResearch;
}
export async function saveResearch(name: string, contract: unknown, signal?: AbortSignal): Promise<ResearchCreated> {
  const value = await request("/runs", { name, contract }, signal);
  if (!record(value) || typeof value.run_id !== "string" || !value.run_id || typeof value.task !== "string" || typeof value.task_sha256 !== "string"
      || value.research_started !== false || value.status !== "created" || !record(value.execution_admission)
      || typeof value.execution_admission.ready !== "boolean" || !Array.isArray(value.execution_admission.blockers)
      || !value.execution_admission.blockers.every((item: unknown) => record(item) && typeof item.code === "string" && typeof item.message === "string" && Array.isArray(item.fields))) throw new Error("保存回执不完整，请到任务列表核对是否已保存。");
  return value as ResearchCreated;
}
export function knownResearchRejection(error: unknown): boolean {
  return error instanceof ResearchApiError && error.status >= 400 && error.status < 500 && error.status !== 408;
}
export function researchRequestError(error: unknown): string {
  if (error instanceof DOMException && error.name === "TimeoutError") return "等待后端超时，请检查连接；没有自动重试。";
  if (error instanceof TypeError) return "无法连接后端服务，请检查连接；没有自动重试。";
  return error instanceof Error ? error.message : "请求未完成，请检查连接。";
}
export function admissionMessage(code: string, fallback: string): string {
  const labels: Record<string, string> = {
    project_execution_adapter_pending: "项目命令、数据范围和结果指标尚未全部接入受约束执行，当前不能启动。",
    research_budget_enforcement_pending: "全局预算尚未完成所有执行入口的绑定，当前不能调用模型或提交实验。",
    research_contract_integrity_error: "保存的计划身份或文件校验不一致，需要检查任务记录。",
  };
  return labels[code] || fallback;
}
