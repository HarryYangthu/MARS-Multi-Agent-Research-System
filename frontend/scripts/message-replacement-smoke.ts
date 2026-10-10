import assert from "node:assert/strict";
import { pendingMessageSaved, type StoredChatMessage } from "../src/lib/chatMessageEditing";
import { conversationEntries, type Activity } from "../src/lib/researchActivity";

const common = { state: null, tool_name: null, tool_args: null, tool_result: null };
const messages: StoredChatMessage[] = [
  { ...common, id: "a", turn_id: "a", role: "user", content: "edited input", timestamp: "2026-10-10T03:00:00Z" },
  { ...common, id: "new-reply", turn_id: "a", role: "assistant", content: "new output", timestamp: "2026-10-10T03:00:02Z" },
  { ...common, id: "b", turn_id: "b", role: "user", content: "later input", timestamp: "2026-10-10T02:00:00Z" },
  { ...common, id: "b-reply", turn_id: "b", role: "assistant", content: "later output", timestamp: "2026-10-10T02:00:02Z" },
];
const activities: Activity[] = [
  { id: "b-progress", turn_id: "b", agent: "commander", detail: "", title: "later progress", status: "completed", timestamp: "2026-10-10T02:00:01Z" },
  { id: "a-progress", turn_id: "a", agent: "commander", detail: "", title: "edit progress", status: "completed", timestamp: "2026-10-10T03:00:01Z" },
];
assert.deepEqual(conversationEntries(messages, activities).map(item => item.id), ["a", "a-progress", "new-reply", "b", "b-progress", "b-reply"]);
assert.equal(messages[2].id, "b");
const pending = { text: "edited input", after: 4, startedAt: "2026-10-10T03:00:00Z", replaceId: "a", expectedTimestamp: "2026-10-10T01:00:00Z" };
assert.equal(pendingMessageSaved(messages, pending), true);
assert.equal(pendingMessageSaved(messages, { ...pending, expectedTimestamp: messages[0].timestamp }), false);
assert.equal(pendingMessageSaved(messages, { ...pending, replaceId: "missing" }), false);
assert.equal(pendingMessageSaved(messages, { ...pending, text: "another edit" }), false);
assert.equal(pendingMessageSaved(messages, { ...pending, replaceId: undefined, after: 0 }), true);
assert.equal(pendingMessageSaved(messages, { ...pending, replaceId: undefined }), false);
console.log("Single-turn order, progress ownership and confirmed replacement checks passed");
