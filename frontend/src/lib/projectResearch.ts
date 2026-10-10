import type { RunSummary } from "./api";
import { existingConversationUrl, projectConversations, type ConversationSummary } from "./conversationHistory";

export type ResearchRecord = { id: string; title: string; createdAt: string; updatedAt: string; href: string; runId: string | null; processing: boolean; messages: number };
export function researchDate(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
export function projectResearch(rows: ConversationSummary[], runs: RunSummary[], project: string): ResearchRecord[] {
  const relevant = runs.filter(run => run.project === project);
  const byId = new Map(relevant.map(run => [run.run_id, run]));
  const conversations = projectConversations(rows, project);
  const linked = new Set(conversations.map(row => row.linked_run_id));
  const records: ResearchRecord[] = conversations.map(row => ({ id: row.conv_id, title: row.summary || byId.get(row.linked_run_id || "")?.task || "研究讨论", createdAt: row.created_at, updatedAt: row.updated_at, href: existingConversationUrl(row), runId: row.linked_run_id, processing: row.processing, messages: row.message_count }));
  for (const run of relevant) if (!linked.has(run.run_id)) records.push({ id: run.run_id, title: run.task, createdAt: run.created_at, updatedAt: run.created_at, href: `/runs/${encodeURIComponent(run.run_id)}`, runId: run.run_id, processing: false, messages: 0 });
  return records.sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt) || a.id.localeCompare(b.id));
}
