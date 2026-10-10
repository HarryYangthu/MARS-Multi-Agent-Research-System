import assert from "node:assert/strict";
import { emptyMessageDraft, messageDraftReducer } from "../src/lib/messageDraft";

// Pure draft lifecycle only. Real delivery is verified against the live UI/API.
const original = { id: "first", text: "把第二层抽头增加 5 个\n与基线对比" };
const draft = messageDraftReducer(emptyMessageDraft, { type: "change", text: "尚未发送的草稿" });
const editing = messageDraftReducer(draft, { type: "edit", messageId: original.id, text: original.text });
const revised = messageDraftReducer(editing, { type: "change", text: "把第二层抽头增加 3 个\n与基线对比" });
assert.equal(revised.editing?.messageId, original.id);
assert.equal(original.text, "把第二层抽头增加 5 个\n与基线对比");
assert.deepEqual(messageDraftReducer(revised, { type: "cancel" }), draft);

// Switching to another old message must not replace the preserved draft.
const switched = messageDraftReducer(revised, { type: "edit", messageId: "second", text: "另一条消息" });
assert.deepEqual(messageDraftReducer(switched, { type: "cancel" }), draft);
assert.deepEqual(messageDraftReducer(revised, { type: "submitted" }), draft);

// An unconfirmed send can restore the edited text without losing its cancel target.
const sent = messageDraftReducer(revised, { type: "submitted" });
const restored = messageDraftReducer(sent, { type: "restore", draft: revised });
assert.deepEqual(restored, revised);
assert.deepEqual(messageDraftReducer(restored, { type: "cancel" }), draft);
assert.deepEqual(messageDraftReducer(restored, { type: "reset" }), emptyMessageDraft);
assert.deepEqual(messageDraftReducer(emptyMessageDraft, { type: "cancel" }), emptyMessageDraft);
assert.deepEqual(messageDraftReducer(draft, { type: "submitted" }), emptyMessageDraft);
console.log("Message editing, cancellation, preserved drafts and unconfirmed delivery recovery passed");
