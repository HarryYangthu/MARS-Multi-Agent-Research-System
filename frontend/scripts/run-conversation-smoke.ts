import { strict as assert } from "node:assert";
import { researchConversationUrl, researchRunConversationUrl } from "../src/lib/runConversation";

const raw = researchConversationUrl("run/id + 中文", "project & 1", "experiment+a");
const url = new URL(raw, "http://localhost");
assert.equal(url.pathname, "/runs/new");
assert.equal(url.searchParams.get("run"), "run/id + 中文");
assert.equal(url.searchParams.get("project"), "project & 1");
assert.equal(url.searchParams.get("experiment"), "experiment+a");
assert.equal(new URL(researchRunConversationUrl({run_id: "existing", project: "a"}), "http://localhost").searchParams.has("experiment"), false);
assert.equal(new URL(researchRunConversationUrl({run_id: "existing", project: "a", experiment_id: "exp"}), "http://localhost").searchParams.get("experiment"), "exp");
console.log("Research chat links retain exact run, project and experiment scope.");
