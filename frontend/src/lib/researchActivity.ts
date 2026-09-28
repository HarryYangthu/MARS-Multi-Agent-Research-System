import type { ChatMessageView, RunDetail, RunActivityView, WorkLogView } from "./api";

export type Activity = { id: string; timestamp: string; agent: string; title: string; detail: string; status: string; ended_at?: string | null };
export type CommanderActivity = Omit<Activity, "agent" | "detail"> & { kind: string };
export const agentLabel = (name: string): string => ({ commander: "总控", idea: "研究", idea_research: "调研", experiment: "实验设计", coding: "编码", execution: "执行", writing: "报告" }[name] || name);
export const statusLabel = (state: string): string => ({ pending: "待开始", running: "处理中", waiting_review: "等待审核", approved: "已批准", done: "已完成", completed: "已完成", failed: "失败", error: "失败", interrupted: "已中断", paused: "已暂停", stopped: "已停止", cancelled: "已取消", skipped: "已跳过", created: "已创建", unknown: "状态未知", success: "成功", idle: "待命", rejected: "已驳回", blocked: "受阻" }[state] || state);
const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown): string => typeof value === "string" ? value : "";

// Only public execution metadata is projected. Responses, prompts, reasoning and
// tool arguments are deliberately excluded from this progress surface.
export function runActivities(worklog: WorkLogView, observation: RunActivityView): Activity[] {
  const items: Activity[] = worklog.items.map(item => ({ id: `run:${worklog.run_id}:${item.id}`, timestamp: item.timestamp, agent: item.agent, title: item.title, detail: item.detail, status: item.status }));
  const titles: Record<string, string> = { model_request: "发起模型调用", model_response: "模型已返回", model_error: "模型调用失败", context_compressed: "已压缩上下文", tool_dispatch: "调用工具", observation: "工具已返回", validation: "检查输出格式", stopped: "执行已停止", interrupted: "执行已中断", resource_budget_exhausted: "预算已耗尽" };
  for (const event of observation.timeline) {
    const source = record(event.source), payload = record(event.payload), kind = text(event.kind);
    if (source.component !== "agent_loop" || !titles[kind]) continue;
    const status = kind === "model_error" || kind === "resource_budget_exhausted" || payload.ok === false || payload.valid === false ? "failed" : ["model_request", "tool_dispatch"].includes(kind) ? "running" : kind === "interrupted" ? "interrupted" : kind === "stopped" ? "stopped" : "completed";
    const detail = [text(payload.model), text(payload.tool)].filter(Boolean).join(" · ");
    items.push({ id: text(event.event_id), timestamp: text(event.timestamp), agent: text(source.agent), title: titles[kind], detail, status });
  }
  return [...new Map(items.map(item => [item.id, item])).values()].sort((a,b) => Date.parse(a.timestamp) - Date.parse(b.timestamp) || a.id.localeCompare(b.id));
}

export function effectiveNodeState(state: string, runStatus?: string | null): string {
  // Old graph snapshots can retain running nodes after the owner has stopped.
  if (state === "running" && runStatus && ["failed", "interrupted", "paused", "stopped", "cancelled", "completed", "done"].includes(runStatus)) return runStatus === "completed" || runStatus === "done" ? "unknown" : runStatus;
  return state;
}
export function agentNodes(run: RunDetail): { key: string; state: string }[] {
  return run.graph.nodes.filter(node => node.kind === "agent").map(node => ({ key: node.key, state: effectiveNodeState(run.states[node.key] || node.state, run.status) }));
}

export type ConversationEntry = { kind: "message"; id: string; timestamp: string; message: ChatMessageView } | { kind: "activity"; id: string; timestamp: string; activity: Activity };
export function conversationEntries(messages: ChatMessageView[], activities: Activity[]): ConversationEntry[] {
  const entries: ConversationEntry[] = messages.filter(message => message.role !== "system").map((message,index) => ({ kind: "message", id: `message:${index}`, timestamp: message.timestamp, message }));
  entries.push(...activities.map(activity => ({ kind: "activity" as const, id: activity.id, timestamp: activity.timestamp, activity })));
  return entries.sort((a,b) => (Date.parse(a.timestamp) || 0) - (Date.parse(b.timestamp) || 0));
}

export type ConversationGroup = Extract<ConversationEntry, { kind: "message" }> | { kind: "activities"; id: string; activities: Activity[] };
export function groupConversationEntries(entries: ConversationEntry[]): ConversationGroup[] {
  const groups: ConversationGroup[] = [];
  for (const entry of entries) {
    const previous = groups.at(-1);
    if (entry.kind === "message") groups.push(entry);
    else if (previous?.kind === "activities") previous.activities.push(entry.activity);
    else groups.push({ kind: "activities", id: entry.id, activities: [entry.activity] });
  }
  return groups;
}
