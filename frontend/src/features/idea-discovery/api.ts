import { normalizeIdeaDiscoveryPayload } from "./normalize";
import type {
  HypothesisCreateAuditRecord,
  HypothesisCreateInput,
  HypothesisEditInput,
  HypothesisMutationInput,
  IdeaDiscoverySnapshot,
  SystemVersion,
} from "./types";

const CONFIGURED_BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";

export class IdeaDiscoveryApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: string,
  ) {
    super(message);
    this.name = "IdeaDiscoveryApiError";
  }
}

export async function getIdeaDiscovery(
  runId: string,
  signal?: AbortSignal,
): Promise<IdeaDiscoverySnapshot> {
  const payload = await requestJson<unknown>(
    `/api/runs/${encodeURIComponent(runId)}/idea-discovery`,
    { signal },
  );
  return normalizeIdeaDiscoveryPayload(payload);
}

export async function getDiscoverySystemVersion(
  signal?: AbortSignal,
): Promise<SystemVersion> {
  return requestJson<SystemVersion>("/api/system/version", { signal });
}

export async function editHypothesis(
  runId: string,
  hypothesisId: string,
  input: HypothesisEditInput,
): Promise<void> {
  await requestJson<unknown>(hypothesisPath(runId, hypothesisId), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function createHypothesis(
  runId: string,
  input: HypothesisCreateInput,
  signal?: AbortSignal,
): Promise<HypothesisCreateAuditRecord> {
  return requestJson<HypothesisCreateAuditRecord>(
    `/api/runs/${encodeURIComponent(runId)}/idea-discovery/hypotheses`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    },
  );
}

export async function rejectHypothesis(
  runId: string,
  hypothesisId: string,
  input: HypothesisMutationInput,
): Promise<void> {
  await requestJson<unknown>(`${hypothesisPath(runId, hypothesisId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function selectHypothesis(
  runId: string,
  hypothesisId: string,
  input: HypothesisMutationInput,
): Promise<void> {
  await requestJson<unknown>(`${hypothesisPath(runId, hypothesisId)}/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function systemSupportsIdeaDiscovery(version: SystemVersion): boolean {
  return (
    version.capabilities.includes("idea_deep_discovery") ||
    version.project_packs.some((pack) =>
      pack.capabilities.includes("idea_deep_discovery"),
    )
  );
}

export function isIdeaDiscoveryUnavailable(error: unknown): boolean {
  return error instanceof IdeaDiscoveryApiError && error.status === 404;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { cache: "no-store", ...init });
  const text = await response.text();
  if (!response.ok) {
    throw new IdeaDiscoveryApiError(
      `HTTP ${response.status}: ${errorDetail(text)}`,
      response.status,
      text,
    );
  }
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new IdeaDiscoveryApiError("Backend returned invalid JSON", response.status, text);
  }
}

function hypothesisPath(runId: string, hypothesisId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/idea-discovery/hypotheses/${encodeURIComponent(hypothesisId)}`;
}

function apiUrl(path: string): string {
  if (!CONFIGURED_BACKEND_URL) return path;
  return new URL(path, CONFIGURED_BACKEND_URL).toString();
}

function errorDetail(text: string): string {
  if (!text) return "empty response";
  try {
    const value = JSON.parse(text) as unknown;
    if (isRecord(value) && typeof value.detail === "string") return value.detail;
    if (isRecord(value) && isRecord(value.detail)) {
      const message = value.detail.message;
      if (typeof message === "string") return message;
    }
  } catch {
    return text;
  }
  return text;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
