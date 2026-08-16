import { normalizeProjectPackUiSchema } from "./schema";
import type {
  CreateV31RunInput,
  CreatedRun,
  DynamicProjectPackUiSchema,
  ProjectPackSummary,
  SystemVersion,
} from "./types";

const CONFIGURED_BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";

export class ProjectPackApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: string,
  ) {
    super(message);
    this.name = "ProjectPackApiError";
  }
}

export async function getSystemVersion(
  signal?: AbortSignal,
): Promise<SystemVersion> {
  return requestJson<SystemVersion>("/api/system/version", { signal });
}

export async function listProjectPacks(
  signal?: AbortSignal,
): Promise<ProjectPackSummary[]> {
  return requestJson<ProjectPackSummary[]>("/api/projects", { signal });
}

export async function getProjectPackUiSchema(
  project: string,
  signal?: AbortSignal,
): Promise<DynamicProjectPackUiSchema> {
  const payload = await requestJson<unknown>(
    `/api/projects/${encodeURIComponent(project)}/ui-schema`,
    { signal },
  );
  return normalizeProjectPackUiSchema(payload);
}

export async function createV31Run(input: CreateV31RunInput): Promise<CreatedRun> {
  const extensionFields = input.compatibilityMode
    ? {}
    : {
        idea_mode: input.ideaMode,
        idea_budget_profile: input.budgetProfile,
        project_inputs: input.projectInputs,
      };
  return requestJson<CreatedRun>("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task: input.task,
      project: input.project,
      entrypoint: "idea",
      standalone: false,
      user_request: input.userRequest,
      ...extensionFields,
    }),
  });
}

export async function startV31Run(runId: string): Promise<void> {
  await requestJson<unknown>(`/api/runs/${encodeURIComponent(runId)}/start`, {
    method: "POST",
  });
}

export function supportsProjectCapability(
  version: SystemVersion | null,
  project: ProjectPackSummary | null,
  capability: string,
): boolean {
  if (!version || !project) return false;
  if (version.capabilities.includes(capability)) return true;
  if (project.capabilities.includes(capability)) return true;
  return Boolean(
    version.project_packs.find(
      (pack) =>
        pack.project_id === project.name && pack.capabilities.includes(capability),
    ),
  );
}

export function isUnavailable(error: unknown): boolean {
  return error instanceof ProjectPackApiError && error.status === 404;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    cache: "no-store",
    ...init,
  });
  const text = await response.text();
  if (!response.ok) {
    throw new ProjectPackApiError(
      `HTTP ${response.status}: ${errorDetail(text)}`,
      response.status,
      text,
    );
  }
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ProjectPackApiError("Backend returned invalid JSON", response.status, text);
  }
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
