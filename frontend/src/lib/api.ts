// Thin REST client. Backend URL comes from NEXT_PUBLIC_BACKEND_URL.

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

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

export type GraphNode = { key: string; kind: string; state: string; metadata: Record<string, unknown> };
export type GraphEdge = { src: string; dst: string };
export type RunDetail = RunSummary & {
  states: Record<string, string>;
  graph: { nodes: GraphNode[]; edges: GraphEdge[]; entrypoints: string[] };
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
  text_excerpt: string;
  metadata: Record<string, unknown>;
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

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`HTTP ${r.status}: ${text}`);
  }
  return (await r.json()) as T;
}

// ---------- runs ----------
export async function listRuns(): Promise<RunSummary[]> {
  return jsonOrThrow(await fetch(`${BASE}/api/runs`));
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
export async function diffVersions(
  runId: string,
  agentDir: string,
  stem: string,
  from: string,
  to: string,
): Promise<{ diff: string }> {
  const url = new URL(`${BASE}/api/artifacts/${runId}/${agentDir}/${stem}/diff`);
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
export async function searchZone(zone: string, q: string, topK = 5) {
  const url = new URL(`${BASE}/api/knowledge/${zone}/search`);
  url.searchParams.set("q", q);
  url.searchParams.set("top_k", String(topK));
  return jsonOrThrow<{ score: number; item: KBItem }[]>(await fetch(url));
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

export const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || BASE.replace(/^http/, "ws");
