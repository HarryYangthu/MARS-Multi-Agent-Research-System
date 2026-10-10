import assert from "node:assert/strict";
import type { RunDetail } from "../src/lib/api";
import { managedReviewPending, managedReviewReason } from "../src/lib/managedReview";

// Pure authored UI states, not runtime or model substitutes.
const run: RunDetail = { run_id: "review-ui", project: "p", task: "UI state contract", entrypoint: "idea", created_at: "", states: {}, graph: { nodes: [], edges: [], entrypoints: [] } };
assert.equal(managedReviewPending(run, "idea", "artifact"), false);
assert.equal(managedReviewPending({ ...run, review_mode: "commander" }, "idea", "artifact"), true);
const stopped = { ...run, review_mode: "commander" as const, managed_review: { node: "idea", kind: "artifact", status: "needs_user", reason: "缺少依据" } };
assert.equal(managedReviewPending(stopped, "idea", "artifact"), false);
assert.equal(managedReviewReason(stopped, "idea", "artifact"), "缺少依据");
assert.equal(managedReviewPending(stopped, "coding", "artifact"), true);
assert.equal(managedReviewPending({ ...stopped, review_generation: 2, managed_review: { ...stopped.managed_review, generation: 1 } }, "idea", "artifact"), true);
assert.equal(managedReviewPending({ ...stopped, managed_review: { node: "execution", kind: "execution_configuration", status: "reviewing" } }, "execution", "execution_configuration"), true);
assert.equal(managedReviewReason(stopped, "execution", "execution_configuration"), "");
console.log("Managed review UI state checks passed");
