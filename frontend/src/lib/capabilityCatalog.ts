import { boundedFetch, CLIENT_POLICY } from "./clientPolicy";

export type CapabilityRole = { role: string; configured_enabled: boolean | null; effective_enabled: boolean; effective_tool_granted: boolean; policy_intersection: boolean; execution_authorized: false };
export type CatalogTool = { name: string; kind: "tool" | "mcp_binding"; origin: "registered" | "bridge_only" | "runtime_bound" | "unbound"; configured_enabled: boolean | null; registered: boolean; dispatch_enabled: boolean; effective_transport: "local_python" | "mcp_stdio" | "unbound"; drift_status: "detected" | "not_detected" | "unknown"; drift_fields: string[]; input_schema_sha256: string | null; output_schema_sha256: string | null; mutation_level: "read" | "write" | "unknown"; requires_approval: boolean | null; roles: CapabilityRole[]; contract_adapter: "requires_host_scope" | "unsupported"; dependency_status: "unknown"; certification_status: "not_run"; execution_authorized: false };
export type CatalogSkill = { name: string; kind: "skill"; enabled: null; selection_required: true; definition_valid: boolean; version: string | null; required_tools: string[]; content_sha256: string | null; contract_adapter: "requires_host_scope" | "unsupported" | "unknown"; dependency_status: "unknown"; certification_status: "not_run"; execution_authorized: false };
export type CapabilityCatalog = { schema_id: "capability_catalog.v1"; context_sha256: string; agent_configuration_drift: boolean; tools: CatalogTool[]; skills: CatalogSkill[]; research_started: false; probes_started: false; certification_validation_available: false };

function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function strings(value: unknown): value is string[] { return Array.isArray(value) && value.every((item: unknown) => typeof item === "string"); }
function choices(value: unknown, options: string[]): boolean { return typeof value === "string" && options.includes(value); }
function hash(value: unknown): boolean { return typeof value === "string" && /^[a-f0-9]{64}$/.test(value); }
function optionalHash(value: unknown): boolean { return value === null || hash(value); }
function passive(value: Record<string, unknown>): boolean { return value.dependency_status === "unknown" && value.certification_status === "not_run" && value.execution_authorized === false; }
function nullableBoolean(value: unknown): boolean { return value === null || typeof value === "boolean"; }
function validRole(value: unknown): boolean { return record(value) && typeof value.role === "string" && nullableBoolean(value.configured_enabled) && typeof value.effective_enabled === "boolean" && typeof value.effective_tool_granted === "boolean" && typeof value.policy_intersection === "boolean" && value.execution_authorized === false; }

export function parseCapabilityCatalog(value: unknown): CapabilityCatalog {
  if (!record(value) || value.schema_id !== "capability_catalog.v1" || !hash(value.context_sha256)
      || typeof value.agent_configuration_drift !== "boolean" || value.research_started !== false || value.probes_started !== false || value.certification_validation_available !== false
      || !Array.isArray(value.tools) || !value.tools.every((row: unknown) => record(row) && typeof row.name === "string" && passive(row)
        && choices(row.kind, ["tool", "mcp_binding"]) && choices(row.origin, ["registered", "bridge_only", "runtime_bound", "unbound"])
        && nullableBoolean(row.configured_enabled) && typeof row.registered === "boolean" && typeof row.dispatch_enabled === "boolean"
        && choices(row.effective_transport, ["local_python", "mcp_stdio", "unbound"]) && choices(row.drift_status, ["detected", "not_detected", "unknown"])
        && strings(row.drift_fields) && optionalHash(row.input_schema_sha256) && optionalHash(row.output_schema_sha256)
        && choices(row.mutation_level, ["read", "write", "unknown"]) && nullableBoolean(row.requires_approval)
        && choices(row.contract_adapter, ["requires_host_scope", "unsupported"]) && Array.isArray(row.roles) && row.roles.every(validRole))
      || !Array.isArray(value.skills) || !value.skills.every((row: unknown) => record(row) && typeof row.name === "string" && passive(row)
        && row.kind === "skill" && row.enabled === null && row.selection_required === true && typeof row.definition_valid === "boolean"
        && (row.version === null || typeof row.version === "string") && strings(row.required_tools) && optionalHash(row.content_sha256)
        && choices(row.contract_adapter, ["requires_host_scope", "unsupported", "unknown"]))) {
    throw new Error("能力目录格式不完整或状态版本不受支持，请刷新后重试。");
  }
  const names = [...value.tools.map((row: CatalogTool) => row.name), ...value.skills.map((row: CatalogSkill) => `skill:${row.name}`)];
  if (new Set(names).size !== names.length) throw new Error("能力目录包含重复记录，无法确认状态。");
  return value as CapabilityCatalog;
}

export async function loadCapabilityCatalog(signal: AbortSignal): Promise<CapabilityCatalog> {
  const base = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
  const response = await boundedFetch(`${base}/api/capabilities`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? "当前连接未获授权。" : response.status === 503 ? "能力配置暂时无法核验，请检查高级设置中的配置后刷新。" : `能力目录读取失败（HTTP ${response.status}）。`);
  const reader = response.body?.getReader();
  if (!reader) throw new Error("没有收到能力目录。");
  const chunks: Uint8Array[] = []; let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      length += value.length;
      if (!Number.isFinite(CLIENT_POLICY.maxContractBytes) || CLIENT_POLICY.maxContractBytes <= 0 || length > CLIENT_POLICY.maxContractBytes) throw new Error("能力目录超过读取上限。");
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => undefined); }
  const bytes = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return parseCapabilityCatalog(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown);
}
