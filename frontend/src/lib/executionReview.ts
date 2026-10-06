import { boundedFetch } from "./clientPolicy";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
export type ExecutionConfiguration = {
  visible: boolean; run_id: string; project: string; node: string; token: string;
  state: string; launch_ready: boolean; can_confirm: boolean; confirmed: boolean;
  runtime_mode: "deterministic";
  jobs: { experiment_id: string; attempt: number; status: string; updated_at: string; error?: string; metrics: Record<string, number>; duration_seconds?: number }[];
  defaults: Record<string, unknown>;
  experiments: { name: string; seed: unknown; config: Record<string, unknown>; effective?: Record<string, unknown> }[];
  source_configs: { path: string; seed: unknown; epochs: unknown }[];
  blockers: string[]; warnings: string[]; budget: { used: number; limit: number | null; required: number };
};
export class ExecutionReviewError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}
async function request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await boundedFetch(`${BASE}/api/runs/${path}`, { ...init, cache: "no-store" });
  const value: unknown = await response.json();
  if (!response.ok) {
    const detail = typeof value === "object" && value !== null && "detail" in value ? value.detail : null;
    throw new ExecutionReviewError(typeof detail === "string" ? detail : "无法核对仿真配置，请刷新状态。", response.status);
  }
  return value as T;
}
export function getExecutionConfiguration(runId: string, project: string, signal?: AbortSignal): Promise<ExecutionConfiguration> {
  return request(`${encodeURIComponent(runId)}/execution-configuration?project=${encodeURIComponent(project)}`, { signal });
}
export function confirmExecutionConfiguration(runId: string, project: string, token: string): Promise<{ ok: boolean; confirmed: boolean; status: string }> {
  return request(`${encodeURIComponent(runId)}/execution-configuration/confirm`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project, token }),
  });
}

export function setExecutionBoundary(runId: string, project: string, stopAfterExecution: boolean): Promise<ExecutionConfiguration> {
  return request(`${encodeURIComponent(runId)}/execution-boundary`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, stop_after_execution: stopAfterExecution }),
  });
}
