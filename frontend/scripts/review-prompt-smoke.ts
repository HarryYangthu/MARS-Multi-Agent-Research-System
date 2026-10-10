import assert from "node:assert/strict";
import { pendingReviewStage, reviewPromptIdentity } from "../src/lib/runReview";
import type { ArtifactView, RunDetail } from "../src/lib/api";

// Human-authored pure UI state inputs, not service or Agent substitutes.
const run: RunDetail = { run_id: "review-run", project: "review-project", task: "Review state inputs", entrypoint: "idea", created_at: "", status: "running", states: { idea: "waiting_review", experiment: "pending" }, graph: { nodes: [], edges: [], entrypoints: ["idea"] } };
const artifact: ArtifactView = { run_id: run.run_id, agent_dir: "idea", stem: "idea_proposal", version: "v1", path: "", text: "Human-authored UI input", metadata: {}, schema_id: "proposal.v1", valid: false, errors: [] };
assert.equal(pendingReviewStage(run), "idea");
assert.equal(pendingReviewStage({ ...run, read_only: true }), null);
for (const status of ["stopped", "paused", "failed", "interrupted", "cancelled", "completed", "done"]) {
  assert.equal(pendingReviewStage({ ...run, status }), null);
}
assert.equal(pendingReviewStage({ ...run, states: { idea: "running" } }), null);
assert.equal(pendingReviewStage({ ...run, states: { idea: "waiting_review", idea_attempt_2: "running" } }), null);
assert.equal(pendingReviewStage({ ...run, states: { idea: "failed", idea_attempt_2: "waiting_review" } }), "idea");
assert.equal(pendingReviewStage({ ...run, states: { idea: "approved", experiment: "waiting_review" } }), "experiment");

const identity = reviewPromptIdentity(run, "idea", artifact);
assert.equal(reviewPromptIdentity({ ...run }, "idea", { ...artifact }), identity);
assert.notEqual(reviewPromptIdentity(run, "idea", { ...artifact, version: "v2" }), identity);
assert.notEqual(reviewPromptIdentity(run, "idea", { ...artifact, text: "Revised human-authored UI input" }), identity);
assert.notEqual(reviewPromptIdentity({ ...run, run_id: "another-run" }, "idea", artifact), identity);
assert.notEqual(reviewPromptIdentity({ ...run, states: { idea: "failed", idea_attempt_2: "waiting_review" } }, "idea", artifact), identity);
console.log("Review prompt state and document identity checks passed");
