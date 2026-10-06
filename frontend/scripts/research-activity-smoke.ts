import assert from "node:assert/strict";
import { activeActivityGroups, activityTiming, activityElapsedSeconds, formatActivityElapsed, runActivities, effectiveNodeState, groupConversationEntries, conversationEntries, type Activity } from "../src/lib/researchActivity";
import type { RunObservabilityView, WorkLogView, ChatMessageView } from "../src/lib/api";
// Pure projection inputs; these are not model/tool execution substitutes.
const obs: RunObservabilityView = { schema: "", run_id: "r", project: "p", task: "", entrypoint: "", status: "running", states: {}, health: {}, latest_event_at: "", event_streams: {}, trace: {}, execution: {}, audit: {}, timeline: [
  { event_id: "e1", timestamp: "2026-09-29T00:00:01Z", kind: "model_request", source: { component: "agent_loop", agent: "experiment" }, payload: { model: "model", reasoning_content: "PRIVATE", visible: "PRIVATE" } },
  { event_id: "e2", timestamp: "2026-09-29T00:00:02Z", kind: "model_error", source: { component: "agent_loop", agent: "experiment" }, payload: {} },
  { event_id: "e3", kind: "reflection", source: { component: "agent_loop" }, payload: { content: "PRIVATE" } },
] };
const worklog: WorkLogView = { run_id: "r", project: "p", agent: "", status: "", started_at: "", latest_at: "", elapsed_seconds: null, items: [] };
const projected = runActivities(worklog, obs);
assert.equal(projected.length, 2);
assert.equal(projected[1].status, "failed");
assert(!JSON.stringify(projected).includes("PRIVATE"));
assert.equal(effectiveNodeState("running", "interrupted"), "interrupted");
assert.equal(effectiveNodeState("waiting_review", "running"), "waiting_review");
assert.equal(effectiveNodeState("running", "completed"), "unknown");
const message: ChatMessageView = { role: "assistant", content: "reply", timestamp: "2026-09-29T00:00:01.500Z", state: null, tool_name: null, tool_args: null, tool_result: null };
const entries = conversationEntries([message], projected);
assert.deepEqual(entries.map(entry => entry.kind), ["activity", "message", "activity"]);
assert.equal(groupConversationEntries(entries).length, 3);
assert.equal(groupConversationEntries(conversationEntries([], projected)).length, 1);
assert.deepEqual(runActivities(worklog, obs).map(row => row.id), projected.map(row => row.id));
console.log("Research activity projection checks passed");

// Default review focus follows actionable work, including retried stages.
import { latestStages, reviewFocus, artifactBody } from "../src/lib/runReview";
import type { RunDetail } from "../src/lib/api";
const run: RunDetail = { run_id: "r", project: "p", task: "t", entrypoint: "experiment", created_at: "", status: "running", states: { idea: "skipped", experiment: "failed", experiment_attempt_2: "waiting_review", coding: "pending" }, graph: { nodes: [], edges: [], entrypoints: ["experiment"] } };
assert.equal(reviewFocus(run, ""), "experiment");
assert.equal(reviewFocus(run, "commander"), "experiment");
assert.equal(reviewFocus(run, "coding"), "coding");
assert.equal(latestStages(run).find(item => item.stage === "experiment")?.state, "waiting_review");
assert.equal(reviewFocus({ ...run, states: { idea: "skipped", experiment: "done", coding: "running" } }, ""), "coding");
assert.equal(artifactBody("---\nschema: experiment_plan.v1\n---\n# Human-readable plan"), "# Human-readable plan");
assert.equal(artifactBody("# Plan\n---\nKeep this rule"), "# Plan\n---\nKeep this rule");
console.log("Simple review focus checks passed");

// Real-time display is a pure projection of persisted timestamps and owner
// state; these inputs do not stand in for an Agent or a successful tool run.
const start = Date.parse("2026-10-06T03:00:00Z");
const rows: Activity[] = [
  { id: "start", timestamp: new Date(start).toISOString(), agent: "coding", title: "开始编码", detail: "", status: "running", startsStage: true },
  { id: "request", timestamp: new Date(start + 5000).toISOString(), agent: "coding", title: "发起模型调用", detail: "", status: "running" },
  { id: "response", timestamp: new Date(start + 20000).toISOString(), agent: "coding", title: "模型已返回", detail: "", status: "completed" },
];
const timing = activityTiming(rows);
assert.equal(activityElapsedSeconds(timing, true, start + 21000), 21);
assert.equal(activityElapsedSeconds(timing, true, start + 22000), 22); // No new event is required.
assert.equal(activityElapsedSeconds(timing, false, start + 100000), 20); // Completion freezes the clock.
assert.equal(activityElapsedSeconds(timing, false, start + 900000), 20);
assert.equal(activityElapsedSeconds(timing, true, start - 1000), 0); // Clock skew cannot produce negatives.
assert.equal(activityElapsedSeconds(activityTiming([]), true, start), null);
assert.equal(activityElapsedSeconds(activityTiming([], new Date(start).toISOString()), true, start + 3000), 3);
assert.equal(activityElapsedSeconds(activityTiming(rows.slice(1), new Date(start).toISOString()), true, start + 22000), 22); // Truncated history retains the persisted task start.
assert.equal(activityElapsedSeconds(activityTiming([{ ...rows[0], timestamp: "invalid" }]), true, start), null);
assert.equal(activityElapsedSeconds(activityTiming([{ ...rows[0], ended_at: new Date(start + 30000).toISOString() }, rows[1]]), false, start), 30);
assert.equal(formatActivityElapsed(0), "0 秒");
assert.equal(formatActivityElapsed(61), "1 分 1 秒");
assert.equal(formatActivityElapsed(3661), "1 小时 1 分 1 秒");

const grouped = groupConversationEntries(conversationEntries([{ ...message, timestamp: new Date(start + 21000).toISOString() }], rows));
const codingRun = { ...run, states: { coding: "running" } };
assert.deepEqual([...activeActivityGroups(grouped, codingRun, false).keys()], ["start"]); // A trailing message does not hide live work.
for (const status of ["failed", "stopped", "paused", "interrupted", "cancelled", "completed", "done"]) {
  assert.equal(activeActivityGroups(grouped, { ...codingRun, status }, false).size, 0);
}
assert.equal(activeActivityGroups(grouped, { ...run, states: { coding: "waiting_review" } }, false).size, 0);
assert.equal(activeActivityGroups(grouped, { ...run, states: { coding: "running", coding_attempt_2: "waiting_review" } }, false).size, 0);
const retryGroups = groupConversationEntries(conversationEntries([], [...rows, { ...rows[0], id: "retry", timestamp: new Date(start + 25000).toISOString(), agent: "coding_attempt_2" }]));
assert.deepEqual([...activeActivityGroups(retryGroups, { ...run, states: { coding: "failed", coding_attempt_2: "running" } }, false).keys()], ["retry"]);

const commanderGroups = groupConversationEntries(conversationEntries([], [
  { ...rows[0], id: "old-commander", agent: "commander" },
  { ...rows[0], id: "new-commander", agent: "commander", timestamp: new Date(start + 30000).toISOString() },
]));
assert.deepEqual([...activeActivityGroups(commanderGroups, null, true, new Date(start + 25000).toISOString()).keys()], ["new-commander"]);
assert.equal(activeActivityGroups(commanderGroups, null, true, new Date(start + 35000).toISOString()).size, 0);
assert.equal(activeActivityGroups(commanderGroups, null, false).size, 0);
const simultaneous = groupConversationEntries(conversationEntries([], [rows[0], { ...rows[0], id: "commander", agent: "commander", timestamp: new Date(start + 1000).toISOString() }]));
assert.deepEqual([...activeActivityGroups(simultaneous, codingRun, true).keys()].sort(), ["commander", "start"]);
const beforeWindowShift = activeActivityGroups(groupConversationEntries(conversationEntries([], rows)), codingRun, false);
const afterWindowShift = activeActivityGroups(groupConversationEntries(conversationEntries([], rows.slice(1))), codingRun, false);
assert.deepEqual(beforeWindowShift.get("start"), afterWindowShift.get("request")); // Stable owners preserve live UI state when a window shifts.
console.log("Live activity timing, current-stage selection and terminal-state checks passed");
