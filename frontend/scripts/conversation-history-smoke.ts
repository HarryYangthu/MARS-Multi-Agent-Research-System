import assert from "node:assert/strict";
import { existingConversationUrl, parseConversationSummaries, projectConversations, validateConversationScope, type ConversationSummary } from "../src/lib/conversationHistory";
import type { Conversation } from "../src/lib/api";

// Only pure history projection and routing contracts; no simulated Agent,
// tool, HTTP service, or model execution.
const summary: ConversationSummary = { conv_id: "old", project: "p", experiment_id: "", state: "idle", processing: false,
  linked_run_id: null, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-02T00:00:00Z", message_count: 4 };
const rows = [summary, { ...summary, conv_id: "new", updated_at: "2026-10-06T00:00:00Z" },
  { ...summary, conv_id: "empty", updated_at: "2026-10-07T00:00:00Z", message_count: 0 },
  { ...summary, conv_id: "other-project", project: "q", updated_at: "2026-10-08T00:00:00Z" },
  { ...summary, conv_id: "experiment", experiment_id: "exp", updated_at: "2026-10-03T00:00:00Z" }];
assert.deepEqual(projectConversations(rows, "p").map(row => row.conv_id), ["new", "experiment", "old"]);
assert.deepEqual(projectConversations(rows, "p", "").map(row => row.conv_id), ["new", "old"]);
assert.deepEqual(projectConversations(rows, "p", "exp").map(row => row.conv_id), ["experiment"]);
assert.deepEqual(projectConversations(rows, "missing"), []);
assert.equal(rows[0].conv_id, "old"); // Sorting does not mutate a shared list.
assert.equal(projectConversations([{ ...summary, message_count: 0, linked_run_id: "run" }], "p").length, 1);
assert.equal(projectConversations([{ ...summary, message_count: 0, processing: true }], "p").length, 1);
const updatedOlderConversation = [{ ...summary, conv_id: "recently-used", updated_at: "2026-10-09T00:00:00Z" }, rows[1]];
assert.equal(projectConversations(updatedOlderConversation, "p")[0].conv_id, "recently-used");

const url = new URL(existingConversationUrl({ conv_id: "conversation +/中文", project: "project & 中文", experiment_id: "exp+a" }), "http://localhost");
assert.equal(url.pathname, "/runs/new");
assert.equal(url.searchParams.get("conversation"), "conversation +/中文");
assert.equal(url.searchParams.get("project"), "project & 中文");
assert.equal(url.searchParams.get("experiment"), "exp+a");
assert.equal(url.searchParams.has("run"), false); // Restore exact conversation without creating/rebinding a run.
assert.equal(new URL(existingConversationUrl(summary), "http://localhost").searchParams.has("experiment"), false);

assert.deepEqual(parseConversationSummaries([{ ...summary, activities: [{ title: "not part of history projection" }] }]), [summary]);
assert.deepEqual(parseConversationSummaries([]), []);
assert.equal(parseConversationSummaries([{ ...summary, summary: "优化基线学习率" }])[0].summary, "优化基线学习率");
for (const invalid of [null, {}, "[]", [null], [{ ...summary, project: "" }], [{ ...summary, conv_id: "" }],
  [{ ...summary, updated_at: "invalid" }], [{ ...summary, message_count: -1 }], [{ ...summary, message_count: 1.5 }],
  [{ ...summary, experiment_id: 2 }], [{ ...summary, processing: "yes" }], [{ ...summary, linked_run_id: {} }]]) {
  assert.throws(() => parseConversationSummaries(invalid));
}
const conversation: Conversation = { conv_id: "old", project: "p", state: "idle", linked_run_id: null,
  experiment_id: "", auto_mode: false, metric_targets: {}, messages: [] };
assert.doesNotThrow(() => validateConversationScope(conversation, "p", "", "old"));
assert.doesNotThrow(() => validateConversationScope({ ...conversation, experiment_id: "exp" }, "p"));
assert.throws(() => validateConversationScope(conversation, "q", undefined, "old"));
assert.throws(() => validateConversationScope(conversation, "p", "exp", "old"));
assert.throws(() => validateConversationScope(conversation, "p", undefined, "different"));
console.log("Conversation history projection, ordering, exact identity and project/experiment boundaries passed");
