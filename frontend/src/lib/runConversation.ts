import { boundedFetch } from "./clientPolicy";
import type { Conversation } from "./api";

export function researchConversationUrl(runId: string, project: string, experimentId = ""): string {
  const params = new URLSearchParams({ run: runId, project });
  if (experimentId) params.set("experiment", experimentId);
  return `/runs/new?${params}`;
}

export function researchRunConversationUrl(run: { run_id: string; project: string; experiment_id?: string }): string {
  return researchConversationUrl(run.run_id, run.project, run.experiment_id);
}

export async function openRunConversation(runId: string, project: string, experimentId?: string): Promise<Conversation> {
  const base = process.env.NEXT_PUBLIC_BACKEND_URL || "";
  const response = await boundedFetch(`${base}/api/chat/runs/${encodeURIComponent(runId)}/conversation`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, ...(experimentId !== undefined ? { experiment_id: experimentId } : {}) }),
  });
  if (!response.ok) throw new Error(response.status === 404 ? "当前项目中找不到这项研究任务。" : "暂时无法打开研究对话，请重试。");
  const conversation = await response.json() as Conversation;
  if (conversation.project !== project || conversation.linked_run_id !== runId || (experimentId !== undefined && conversation.experiment_id !== experimentId)) throw new Error("研究对话与任务所属项目或实验不匹配。");
  return conversation;
}
