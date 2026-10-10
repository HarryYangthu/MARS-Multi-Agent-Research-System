import type { ChatMessageView, RunDetail, RunActivityView, WorkLogView } from "./api";
import type { StoredChatMessage } from "./chatMessageEditing";

export type Activity = { id: string; timestamp: string; agent: string; title: string; detail: string; status: string; kind?: string; ended_at?: string | null; startsStage?: boolean; endsStage?: boolean; turn_id?: string | null };
export type CommanderActivity = Omit<Activity, "agent" | "detail"> & { kind: string };
export const agentLabel = (name: string): string => ({ commander: "总控", idea: "研究", idea_research: "调研", experiment: "实验设计", coding: "编码", execution: "执行", writing: "报告" }[name] || name);
export const statusLabel = (state: string): string => ({ pending: "待开始", running: "处理中", waiting_review: "等待审核", waiting_feedback: "等待反馈", waiting_execution_confirmation: "等待核对仿真配置", approved: "已批准", done: "已完成", completed: "已完成", failed: "失败", error: "失败", interrupted: "已中断", paused: "已暂停", stopped: "已停止", cancelled: "已取消", skipped: "已跳过", created: "已创建", unknown: "状态未知", success: "成功", idle: "待命", rejected: "已驳回", blocked: "受阻" }[state] || state);
const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown): string => typeof value === "string" ? value : "";

// Only public execution metadata is projected. Responses, prompts, reasoning and
// tool arguments are deliberately excluded from this progress surface.
export function runActivities(worklog: WorkLogView, observation: RunActivityView): Activity[] {
  const items: Activity[] = worklog.items.map(item => ({ id: `run:${worklog.run_id}:${item.id}`, timestamp: item.timestamp, agent: item.agent, title: item.title, detail: item.detail, status: item.status, startsStage: item.kind === "state" && item.status === "running", endsStage: item.kind === "state" && ["waiting_review", "waiting_feedback", "waiting_execution_confirmation", "failed", "done", "interrupted", "paused", "stopped", "cancelled", "skipped"].includes(item.status) }));
  const titles: Record<string, string> = { model_request: "发起模型调用", model_response: "模型已返回", model_error: "模型调用失败", context_compressed: "已压缩上下文", tool_dispatch: "调用工具", observation: "工具已返回", validation: "检查方案格式与证据", stopped: "执行已停止", interrupted: "执行已中断", resource_budget_exhausted: "预算已耗尽" };
  for (const event of observation.timeline) {
    const source = record(event.source), payload = record(event.payload), kind = text(event.kind);
    if (source.component !== "agent_loop" || !titles[kind]) continue;
    // The work log describes concrete file/query work. Prefer that record to a
    // duplicate raw tool event; full raw events live in Agent details.
    if (worklog.items.some(item => item.agent === text(source.agent))) continue;
    const status = kind === "model_error" || kind === "resource_budget_exhausted" || payload.ok === false || payload.valid === false ? "failed" : ["model_request", "tool_dispatch"].includes(kind) ? "running" : kind === "interrupted" ? "interrupted" : kind === "stopped" ? "stopped" : "completed";
    const detail = [text(payload.model), text(payload.tool)].filter(Boolean).join(" · ");
    items.push({ id: text(event.event_id), timestamp: text(event.timestamp), agent: text(source.agent), title: titles[kind], detail, status, kind });
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

export type ConversationEntry = { kind: "message"; id: string; timestamp: string; message: StoredChatMessage } | { kind: "activity"; id: string; timestamp: string; activity: Activity };
export function conversationEntries(messages: StoredChatMessage[], activities: Activity[]): ConversationEntry[] {
  const entries: ConversationEntry[] = messages.filter(message => message.role !== "system").map((message,index) => ({ kind: "message", id: message.id || `message:${index}`, timestamp: message.timestamp, message }));
  entries.push(...activities.map(activity => ({ kind: "activity" as const, id: activity.id, timestamp: activity.timestamp, activity })));
  // A regenerated earlier reply has a new timestamp but keeps its original
  // place in the dialogue. Its progress belongs to that turn, not the tail.
  if (messages.some(message => message.role === "user" && message.id)) {
    const ranks = new Map<string, number>();
    const messageRanks = new Map<string, number>();
    let rank = -1;
    for (const entry of entries) {
      if (entry.kind !== "message") continue;
      if (entry.message.role === "user") { rank++; ranks.set(entry.message.turn_id || entry.id, rank); }
      messageRanks.set(entry.id, rank);
    }
    const users = messages.filter(message => message.role === "user");
    const ownerRank = (entry: ConversationEntry): number => {
      if (entry.kind === "message") return messageRanks.get(entry.id) ?? -1;
      const owner = entry.activity.turn_id || [...users].reverse().find(message => message.timestamp <= entry.timestamp)?.id;
      return owner ? ranks.get(owner) ?? -1 : -1;
    };
    return entries.sort((a, b) => ownerRank(a) - ownerRank(b) || (Date.parse(a.timestamp) || 0) - (Date.parse(b.timestamp) || 0));
  }
  return entries.sort((a,b) => (Date.parse(a.timestamp) || 0) - (Date.parse(b.timestamp) || 0));
}

export type ConversationGroup = Extract<ConversationEntry, { kind: "message" }> | { kind: "activities"; id: string; activities: Activity[] };
export function groupConversationEntries(entries: ConversationEntry[]): ConversationGroup[] {
  const groups: ConversationGroup[] = [];
  for (const entry of entries) {
    const previous = groups.at(-1);
    if (entry.kind === "message") groups.push(entry);
    else if (previous?.kind === "activities" && !entry.activity.startsStage) previous.activities.push(entry.activity);
    else groups.push({ kind: "activities", id: entry.id, activities: [entry.activity] });
  }
  return groups;
}

// Use owner state, rather than an old request event's "running" status. A model
// request remains in history after its response, and must not keep a clock alive.
export function activeActivityGroups(groups: ConversationGroup[], run: RunDetail | null, commanderProcessing: boolean, commanderStartedAt?: string): Map<string, string[]> {
  const agents = new Set<string>();
  if (commanderProcessing) agents.add("commander");
  if (run) {
    const stages = new Map<string, { attempt: number; state: string }>();
    for (const [key, state] of Object.entries(run.states)) {
      const [stage, attemptText] = key.split("_attempt_");
      const attempt = Number(attemptText || 1);
      if (attempt >= (stages.get(stage)?.attempt ?? 0)) stages.set(stage, { attempt, state });
    }
    for (const [stage, { state }] of stages) {
      if (effectiveNodeState(state, run.status) === "running") agents.add(stage);
    }
  }
  const selected = new Map<string, string[]>();
  const boundary = commanderStartedAt ? Date.parse(commanderStartedAt) : NaN;
  for (const group of [...groups].reverse()) {
    if (group.kind !== "activities") continue;
    for (const activity of [...group.activities].reverse()) {
      const agent = activity.agent.replace(/_attempt_\d+$/, "");
      if (!agents.has(agent)) continue;
      if (agent === "commander" && Number.isFinite(boundary) && Date.parse(activity.timestamp) < boundary) continue;
      selected.set(group.id, [...(selected.get(group.id) ?? []), agent].sort());
      agents.delete(agent);
    }
  }
  return selected;
}

export type ActivityTiming = { startedAt: number | null; endedAt: number | null };
export function activityTiming(activities: Activity[], startedAt?: string): ActivityTiming {
  const starts = activities.map(item => Date.parse(item.timestamp)).filter(Number.isFinite);
  const ends = activities.flatMap(item => [Date.parse(item.timestamp), Date.parse(item.ended_at || "")]).filter(Number.isFinite);
  const stageEnds = activities.filter(item => item.endsStage).map(item => Date.parse(item.timestamp)).filter(Number.isFinite);
  const persistedStart = Date.parse(startedAt || "");
  // A supplied start must belong to this processing group, not the whole run.
  // Human review and subsequent approval are outside Agent processing time.
  return { startedAt: Number.isFinite(persistedStart) ? persistedStart : starts.length ? Math.min(...starts) : null, endedAt: stageEnds.length ? Math.min(...stageEnds) : ends.length ? Math.max(...ends) : null };
}

export function activityElapsedSeconds(timing: ActivityTiming, processing: boolean, now: number): number | null {
  const end = processing ? now : timing.endedAt;
  if (timing.startedAt === null || end === null || !Number.isFinite(end)) return null;
  return Math.max(0, Math.floor((end - timing.startedAt) / 1000));
}

export function formatActivityElapsed(seconds: number): string {
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), remaining = seconds % 60;
  return `${hours ? `${hours} 小时 ` : ""}${minutes ? `${minutes} 分 ` : ""}${remaining} 秒`;
}
