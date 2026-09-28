import assert from "node:assert/strict";
import { runActivities, effectiveNodeState, groupConversationEntries, conversationEntries } from "../src/lib/researchActivity";
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
