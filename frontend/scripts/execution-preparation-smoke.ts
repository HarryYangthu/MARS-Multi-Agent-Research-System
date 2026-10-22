import assert from "node:assert/strict";
import type { ExecutionConfiguration } from "../src/lib/executionReview";
import { checkedExecutionPreparation, PreparationRequestOrder, preparationCanConfirm, preparationCanRetryConfirmation, preparationSummary, type ExecutionPreparation } from "../src/lib/executionPreparationState";

// Pure receipt/state contracts. These checks do not represent API execution or training.
const token = "a".repeat(64);
const nextToken = "b".repeat(64);
const configuration: ExecutionConfiguration = {
  run_id: "run-a", project: "project-a", node: "execution", token,
  visible: true, state: "approved", launch_ready: true, can_confirm: true, confirmed: false,
  runtime_mode: "deterministic", jobs: [], defaults: { budget_unit: "epochs" },
  experiments: [], source_configs: [], blockers: [], warnings: [], budget: { used: 0, limit: null, required: 0 },
};
const ready: ExecutionPreparation = {
  run_id: "run-a", project: "project-a", node: "execution", operation_id: "operation-a",
  status: "ready", token, completed: 5, total: 5,
  validation: Array.from({ length: 5 }, (_, index) => ({ name: `job-${index}`, status: "passed", configured: true })),
  confirmed: false, launch_state: "approved", confirmation_status: "unconfirmed", configuration,
};
assert.deepEqual(checkedExecutionPreparation(ready, "run-a", "project-a"), ready);
assert.equal(preparationCanConfirm(ready, token, false, false), true);
assert.equal(preparationCanConfirm(ready, nextToken, false, false), false, "an old ACK cannot approve another configuration");
assert.equal(preparationCanConfirm({ ...ready, token: nextToken }, nextToken, false, false), false, "the visible configuration must match the ready token");
assert.equal(preparationCanConfirm(ready, token, true, false), false);
assert.equal(preparationCanConfirm(ready, token, false, true), false);
for (const status of ["unprepared", "preparing", "failed", "stale", "interrupted"] as const) {
  assert.equal(preparationCanConfirm({ ...ready, status }, token, false, false), false, status);
}
assert.equal(preparationCanConfirm({ ...ready, confirmation_status: "confirming" }, token, false, false), false);
assert.equal(preparationCanConfirm({ ...ready, confirmed: true }, token, false, false), false);
assert.equal(preparationCanRetryConfirmation(ready, token), true, "lost confirmation transport can retry the identical token");
assert.equal(preparationCanRetryConfirmation(ready, nextToken), false);
assert.equal(preparationCanRetryConfirmation({ ...ready, confirmation_status: "confirming" }, token), false, "observe background confirmation rather than duplicate it");
assert.equal(preparationCanRetryConfirmation({ ...ready, confirmed: true, confirmation_status: "confirmed" }, token), true, "a saved receipt may resume its still-approved task");
for (const launch_state of ["running", "done", "failed", "skipped", "pending"]) {
  assert.equal(preparationCanRetryConfirmation({ ...ready, confirmed: true, launch_state }, token), false, launch_state);
  assert.equal(preparationCanRetryConfirmation({ ...ready, launch_state }, token), false, launch_state);
  assert.equal(preparationCanConfirm({ ...ready, launch_state }, token, false, false), false, launch_state);
}
const partial: ExecutionPreparation = { ...ready, status: "preparing", completed: 3, validation: ready.validation.map((item, index) => ({ ...item, status: index < 3 ? "passed" : index === 3 ? "running" : "required" })) };
assert.match(preparationSummary(partial), /3 \/ 5/);
assert.equal(preparationSummary({ ...partial, total: 0, completed: 0, validation: [] }), "正在读取已批准的实验配置");
assert.equal(partial.validation[3].status, "running");
assert.equal(partial.validation[4].status, "required");
assert.match(preparationSummary(ready), /已通过/);
assert.doesNotMatch(preparationSummary({ ...ready, validation: [{ name: "legacy", status: "unverified", configured: false }] }), /已通过/);
assert.doesNotMatch(preparationSummary({ ...ready, validation: [] }), /已通过/);
for (const invalid of [
  null, {}, { ...ready, run_id: "other" }, { ...ready, project: "other" },
  { ...ready, status: "success" }, { ...ready, token: "not-an-identity" },
  { ...ready, completed: 6 }, { ...ready, total: -1 }, { ...ready, confirmed: "yes" },
  { ...ready, confirmation_status: "done" }, { ...ready, confirmation_error: {} },
  { ...ready, validation: [{ name: "job", status: "guessed" }] },
  { ...ready, configuration: { ...configuration, token: nextToken } },
  { ...ready, configuration: { ...configuration, node: "execution_attempt_2" } },
  { ...ready, configuration: { ...configuration, project: "other" } },
]) assert.throws(() => checkedExecutionPreparation(invalid, "run-a", "project-a"));

const order = new PreparationRequestOrder();
const oldPoll = order.beginRead();
const post = order.beginMutation();
assert.notEqual(post, null);
assert.equal(order.acceptsRead(oldPoll), false);
assert.equal(order.beginMutation(), null, "one HTTP mutation at a time");
const duringPostPoll = order.beginRead();
assert.equal(order.acceptsRead(duringPostPoll), true, "status remains observable while POST is pending");
assert.equal(order.finishMutation(post!), true);
assert.equal(order.acceptsRead(duringPostPoll), false, "late polls cannot replace a newer POST receipt");
const afterTimeoutRetry = order.beginMutation();
assert.notEqual(afterTimeoutRetry, null, "unknown outcome does not keep the request slot busy");
assert.equal(order.finishMutation(afterTimeoutRetry!), true);
const anotherTask = order.beginMutation();
order.invalidate();
assert.equal(order.acceptsMutation(anotherTask!), false, "old task replies cannot affect the new task");
assert.equal(order.finishMutation(anotherTask!), false);
assert.equal(order.acceptsRead(order.beginRead()), true);
console.log("Preparation progress, exact ACK, same-token retry and request/outcome ordering checks passed");
