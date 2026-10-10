import { boundedFetch } from "./clientPolicy";
import type { Conversation } from "./api";

export type ConversationSummary = {
  summary?: string;
  conv_id: string;
  project: string;
  experiment_id: string;
  state: string;
  processing: boolean;
  linked_run_id: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export function parseConversationSummaries(value: unknown): ConversationSummary[] {
  if (!Array.isArray(value)) throw new Error("对话记录格式不正确，请重新读取。");
  return value.map((item: unknown) => {
    if (!item || typeof item !== "object") throw new Error("对话记录格式不正确，请重新读取。");
    const row = item as Record<string, unknown>;
    if (typeof row.conv_id !== "string" || !row.conv_id || typeof row.project !== "string" || !row.project ||
        typeof row.created_at !== "string" || !Number.isFinite(Date.parse(row.created_at)) ||
        typeof row.updated_at !== "string" || !Number.isFinite(Date.parse(row.updated_at)) ||
        typeof row.message_count !== "number" || !Number.isSafeInteger(row.message_count) || row.message_count < 0 ||
        typeof row.state !== "string" || typeof row.processing !== "boolean" ||
        (row.experiment_id !== undefined && typeof row.experiment_id !== "string") ||
        (row.linked_run_id !== null && typeof row.linked_run_id !== "string")) {
      throw new Error("对话记录格式不正确，请重新读取。");
    }
    return { ...(typeof row.summary === "string" ? { summary: row.summary } : {}), conv_id: row.conv_id, project: row.project, experiment_id: typeof row.experiment_id === "string" ? row.experiment_id : "",
      state: row.state, processing: row.processing, linked_run_id: row.linked_run_id as string | null,
      created_at: row.created_at, updated_at: row.updated_at, message_count: row.message_count };
  });
}

export async function listConversationSummaries(signal?: AbortSignal): Promise<ConversationSummary[]> {
  const base = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
  const response = await boundedFetch(`${base}/api/chat/conversations`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("暂时无法读取历史对话，请重试。");
  return parseConversationSummaries(await response.json());
}

export function projectConversations(rows: ConversationSummary[], project: string, experimentId?: string): ConversationSummary[] {
  return rows.filter(row => row.project === project && (experimentId === undefined || row.experiment_id === experimentId)
    && (row.message_count > 0 || row.linked_run_id !== null || row.processing))
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at) || Date.parse(b.created_at) - Date.parse(a.created_at) || a.conv_id.localeCompare(b.conv_id));
}

export function existingConversationUrl(row: Pick<ConversationSummary, "project" | "conv_id" | "experiment_id">): string {
  const params = new URLSearchParams({ project: row.project, conversation: row.conv_id });
  if (row.experiment_id) params.set("experiment", row.experiment_id);
  return `/runs/new?${params}`;
}

export function validateConversationScope(value: Conversation, project: string, experimentId?: string, conversationId?: string): void {
  if (value.project !== project || (experimentId !== undefined && (value.experiment_id || "") !== experimentId)
    || (conversationId !== undefined && value.conv_id !== conversationId)) {
    throw new Error("历史对话与当前项目或实验不匹配，请选择对应项目的对话。");
  }
}
