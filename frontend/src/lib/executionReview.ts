import { boundedFetch, CLIENT_POLICY } from "./clientPolicy";
import { checkedExecutionBoundary, type ExecutionBoundary } from "./executionBoundaryState";

export type { ExecutionBoundary } from "./executionBoundaryState";

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
  validation?: { name: string; status: "required" | "unverified" | "passed" | "failed" | "stale"; configured: boolean; error?: string; receipt_path?: string; receipt_sha256?: string; checks?: { name: string; ready: boolean; details?: Record<string, unknown> }[] }[];
};
export class ExecutionReviewError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}
async function request<T>(path: string, init: RequestInit, timeoutMs = CLIENT_POLICY.requestTimeoutMs): Promise<T> {
  const response = await boundedFetch(`${BASE}/api/runs/${path}`, { ...init, cache: "no-store" }, timeoutMs);
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
  }, CLIENT_POLICY.executionConfirmationTimeoutMs);
}

export async function getExecutionBoundary(runId: string, project: string, signal?: AbortSignal): Promise<ExecutionBoundary> {
  const value = await request<unknown>(`${encodeURIComponent(runId)}/execution-boundary?project=${encodeURIComponent(project)}`, { signal });
  return checkedExecutionBoundary(value, runId, project);
}

export async function setExecutionBoundary(runId: string, project: string, stopAfterExecution: boolean): Promise<ExecutionBoundary> {
  const value = await request<unknown>(`${encodeURIComponent(runId)}/execution-boundary`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, stop_after_execution: stopAfterExecution }),
  });
  return checkedExecutionBoundary(value, runId, project);
}
