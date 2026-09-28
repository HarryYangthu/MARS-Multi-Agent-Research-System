import { boundedFetch, CLIENT_POLICY } from "./clientPolicy";
import type { ResearchBudget, ResearchProject } from "./researchContracts";
import { BUDGET_FIELDS, budgetDraft, buildResearchProject, emptyResearchDraft, validateBudget, type ResearchDraft } from "./researchWizard";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
export type SavedSettings = { schema_id: "research_settings.v1"; source_run_id: string; project: ResearchProject; budget: ResearchBudget; mode: "manual" | "bounded_auto"; requires_preflight: true; research_started: false };
export type SavedSettingsSummary = { run_id: string; name: string; project_id: string; display_name: string; created_at: string };
export type SavedSettingsPage = { items: SavedSettingsSummary[]; next_cursor: string | null; unavailable_count: number };

function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function keys(value: Record<string, unknown>, required: string[]): boolean { return Object.keys(value).sort().join("\0") === [...required].sort().join("\0"); }
function strings(value: unknown): value is string[] { return Array.isArray(value) && value.every((item: unknown) => typeof item === "string" && !item.includes("\0")); }
function text(value: unknown): value is string { return typeof value === "string" && !value.includes("\0"); }
function oneOf(value: unknown, choices: string[]): boolean { return typeof value === "string" && choices.includes(value); }
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function identifier(value: unknown): value is string { return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value); }
function invalid(): never { throw new Error("项目设置结构不完整或含未知字段。请保留完整项目声明，不要填写旧任务指纹或凭据。"); }

export function parseResearchProject(value: unknown): ResearchProject {
  if (!record(value) || !keys(value, ["schema_id", "project_id", "display_name", "paths", "commands", "metrics", "baseline_files", "allowed_paths", "protected_paths", "execution"])
      || value.schema_id !== "research_project.v1" || !text(value.project_id) || !text(value.display_name)
      || !strings(value.baseline_files) || !strings(value.allowed_paths) || !strings(value.protected_paths)) invalid();
  const paths = value.paths, execution = value.execution;
  if (!record(paths) || !keys(paths, ["code", "knowledge", "data", "output"]) || !text(paths.code) || !text(paths.output) || !strings(paths.knowledge) || !strings(paths.data)
      || !record(execution) || !keys(execution, ["kind", "device", "connection_ref"]) || !oneOf(execution.kind, ["local", "ssh"])
      || !oneOf(execution.device, ["cpu", "gpu"]) || !(execution.connection_ref === null || text(execution.connection_ref))) invalid();
  if (!Array.isArray(value.commands) || !value.commands.length || !value.commands.every((item: unknown) => record(item)
      && keys(item, ["name", "purpose", "executable", "arguments", "cwd", "entrypoint_files"]) && text(item.name) && text(item.executable)
      && oneOf(item.purpose, ["check", "train", "evaluate"]) && strings(item.arguments) && text(item.cwd) && strings(item.entrypoint_files))) invalid();
  if (!Array.isArray(value.metrics) || !value.metrics.length || !value.metrics.every((item: unknown) => record(item)
      && keys(item, ["name", "unit", "direction", "target", "tolerance"]) && text(item.name) && text(item.unit)
      && oneOf(item.direction, ["minimize", "maximize"]) && finite(item.target) && finite(item.tolerance))) invalid();
  // Structural parsing preserves values exactly. The production backend remains
  // responsible for schema, protection, path and live source validation.
  return value as ResearchProject;
}

export function parseResearchProjectJSON(raw: string, maxBytes: number): ResearchProject {
  if (!Number.isFinite(maxBytes) || maxBytes <= 0 || new TextEncoder().encode(raw).length > maxBytes) throw new Error("项目 JSON 超过允许大小。");
  try { return parseResearchProject(JSON.parse(raw)); } catch { return invalid(); }
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (record(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

export function projectToSimpleDraft(project: ResearchProject): ResearchDraft | null {
  // Native input/textarea elements normalize embedded line endings; comparing
  // only our string builder would miss the browser's CR-to-LF conversion.
  function multiline(value: unknown): boolean {
    if (typeof value === "string") return /[\r\n]/.test(value);
    if (Array.isArray(value)) return value.some(multiline);
    return record(value) && Object.values(value).some(multiline);
  }
  if (multiline(project)) return null;
  if (project.commands.length !== 3 || new Set(project.commands.map((item) => item.purpose)).size !== 3) return null;
  const draft: ResearchDraft = { ...emptyResearchDraft(), projectId: project.project_id, displayName: project.display_name,
    code: project.paths.code, knowledge: project.paths.knowledge.join("\n"), data: project.paths.data.join("\n"), output: project.paths.output,
    baseline: project.baseline_files.join("\n"), allowed: project.allowed_paths.join("\n"), protected: project.protected_paths.join("\n"),
    kind: project.execution.kind, device: project.execution.device, connectionRef: project.execution.connection_ref ?? "",
    commands: project.commands.map((item) => ({ purpose: item.purpose, executable: item.executable, arguments: item.arguments.join("\n"), cwd: item.cwd, entries: item.entrypoint_files.join("\n") })),
    metrics: project.metrics.map((item) => ({ ...item, target: String(item.target), tolerance: String(item.tolerance) })) };
  const rebuilt = buildResearchProject(draft);
  return rebuilt.project && canonical(rebuilt.project) === canonical(project) ? draft : null;
}

export function parseSavedSettings(value: unknown, runId: string): SavedSettings {
  if (!record(value) || !keys(value, ["schema_id", "source_run_id", "project", "budget", "mode", "requires_preflight", "research_started"])
      || value.schema_id !== "research_settings.v1" || value.source_run_id !== runId || !identifier(runId)
      || value.requires_preflight !== true || value.research_started !== false || !oneOf(value.mode, ["manual", "bounded_auto"])
      || !record(value.budget) || !keys(value.budget, BUDGET_FIELDS.map((field) => field.key))) invalid();
  parseResearchProject(value.project);
  const draft = budgetDraft(value.budget as ResearchBudget);
  if (!validateBudget(draft).budget) invalid();
  return value as SavedSettings;
}

async function get(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await boundedFetch(`${BASE}/api/research-templates${path}`, { signal });
  if (!response.ok) throw new Error(response.status === 409 ? "此任务的保存证据不完整，不能复用。" : response.status === 404 ? "未找到可复用的已保存研究。" : response.status === 401 || response.status === 403 ? "当前连接未获授权。" : `读取设置失败（HTTP ${response.status}）。`);
  const reader = response.body?.getReader();
  if (!reader) throw new Error("未收到设置内容。");
  const chunks: Uint8Array[] = []; let count = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      count += value.length;
      if (!Number.isFinite(CLIENT_POLICY.maxContractBytes) || count > CLIENT_POLICY.maxContractBytes) throw new Error("设置回执超过允许大小，请缩小查询范围。");
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => undefined); }
  const bytes = new Uint8Array(count); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
}

export async function listSavedSettings(signal: AbortSignal, cursor: string | null = null): Promise<SavedSettingsPage> {
  if (cursor !== null && !identifier(cursor)) invalid();
  const value = await get(cursor ? `?cursor=${encodeURIComponent(cursor)}` : "", signal);
  if (!record(value) || !Array.isArray(value.items) || !value.items.every((item: unknown) => record(item) && identifier(item.run_id)
      && text(item.name) && text(item.project_id) && text(item.display_name) && text(item.created_at))
      || !(value.next_cursor === null || identifier(value.next_cursor)) || !Number.isSafeInteger(value.unavailable_count) || Number(value.unavailable_count) < 0) invalid();
  return value as SavedSettingsPage;
}
export async function loadSavedSettings(runId: string, signal: AbortSignal): Promise<SavedSettings> {
  if (!identifier(runId)) invalid();
  return parseSavedSettings(await get(`/${encodeURIComponent(runId)}`, signal), runId);
}
