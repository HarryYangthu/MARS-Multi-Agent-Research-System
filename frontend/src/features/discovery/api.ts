import type {
  CandidateDecisionRequest,
  CandidateDecisionResponse,
  CreateDiscoveryRunRequest,
  DiscoveryReplayView,
  DiscoveryRunView,
  DiscoverySnapshot,
  RunMutation,
} from "./types";

const CONFIGURED_BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";

function apiUrl(path: string): URL {
  const origin =
    CONFIGURED_BACKEND_URL ||
    (typeof window === "undefined" ? "http://127.0.0.1:3001" : window.location.origin);
  return new URL(path, origin);
}

export class DiscoveryApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly path: string,
  ) {
    super(message);
    this.name = "DiscoveryApiError";
  }

  get isCapabilityMissing(): boolean {
    return this.status === 404 || this.status === 405 || this.status === 501;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new DiscoveryApiError(
      detail || `Discovery request failed with ${response.status}`,
      response.status,
      path,
    );
  }
  return (await response.json()) as T;
}

export function isDiscoveryAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export async function getDiscoveryRun(
  runId: string,
  signal?: AbortSignal,
): Promise<DiscoveryRunView> {
  return requestJson<DiscoveryRunView>(`/api/discovery/runs/${encodeURIComponent(runId)}`, {
    signal,
  });
}

export async function createDiscoveryRun(
  request: CreateDiscoveryRunRequest,
): Promise<DiscoveryRunView> {
  return requestJson<DiscoveryRunView>("/api/discovery/runs", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function getDiscoveryReplay(
  runId: string,
  signal?: AbortSignal,
): Promise<DiscoveryReplayView> {
  return requestJson<DiscoveryReplayView>(
    `/api/discovery/runs/${encodeURIComponent(runId)}/replay`,
    { signal },
  );
}

export async function loadDiscoverySnapshot(
  runId: string,
  signal?: AbortSignal,
): Promise<DiscoverySnapshot> {
  signal?.throwIfAborted();
  if (runId === "synthetic-preview") {
    const { syntheticDiscoveryReplay } = await import("./fixtures/synthetic-replay");
    signal?.throwIfAborted();
    return {
      source: "contract_fixture",
      replay: syntheticDiscoveryReplay,
      loaded_at: new Date().toISOString(),
    };
  }
  const [run, replay] = await Promise.all([
    getDiscoveryRun(runId, signal),
    getDiscoveryReplay(runId, signal),
  ]);
  return {
    source: "rest",
    replay: { ...replay, run },
    loaded_at: new Date().toISOString(),
  };
}

export async function mutateDiscoveryRun(
  runId: string,
  mutation: RunMutation,
): Promise<DiscoveryRunView> {
  const payload: Record<RunMutation, Record<string, unknown>> = {
    start: { wait: false },
    pause: { reason: "user_requested" },
    resume: { wait: false },
    stop: { reason: "user_requested" },
  };
  return requestJson<DiscoveryRunView>(
    `/api/discovery/runs/${encodeURIComponent(runId)}/${mutation}`,
    { method: "POST", body: JSON.stringify(payload[mutation]) },
  );
}

export async function decideDiscoveryCandidate(
  runId: string,
  candidateId: string,
  request: CandidateDecisionRequest,
  signal?: AbortSignal,
): Promise<CandidateDecisionResponse> {
  return requestJson<CandidateDecisionResponse>(
    `/api/discovery/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}/decision`,
    {
      method: "POST",
      body: JSON.stringify(request),
      signal,
    },
  );
}
