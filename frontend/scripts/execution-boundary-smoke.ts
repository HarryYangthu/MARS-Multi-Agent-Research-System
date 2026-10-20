import assert from "node:assert/strict";
import { BoundaryRequestOrder, checkedExecutionBoundary } from "../src/lib/executionBoundaryState";

// Pure ordering/receipt contracts only; these do not substitute for API or job execution.
const order = new BoundaryRequestOrder();
const staleRead = order.beginRead();
assert.notEqual(staleRead, null);
const saving = order.beginSave();
assert.notEqual(saving, null);
assert.equal(order.acceptsRead(staleRead!), false);
assert.equal(order.beginRead(), null, "polling cannot replace a pending save");
assert.equal(order.beginSave(), null, "one save may be submitted at a time");
assert.equal(order.acceptsSave(saving!), true);
assert.equal(order.finishSave(saving!), true);
assert.equal(order.acceptsRead(staleRead!), false);
const freshRead = order.beginRead();
assert.equal(order.acceptsRead(freshRead!), true);

const failedSave = order.beginSave();
assert.equal(order.finishSave(failedSave!), true);
const retry = order.beginSave();
assert.notEqual(retry, null, "a refused save does not disable retry");
order.invalidate();
assert.equal(order.acceptsSave(retry!), false, "old task responses cannot change the new task");
assert.equal(order.finishSave(retry!), false);
const newTaskRead = order.beginRead();
assert.equal(order.acceptsRead(newTaskRead!), true);
assert.equal(order.acceptsRead(freshRead!), false);

const saved = checkedExecutionBoundary({ run_id: "run-a", project: "project-a", stop_after_execution: true, can_update: true }, "run-a", "project-a");
assert.equal(saved.stop_after_execution, true);
assert.equal(saved.can_update, true);
const reportStarted = checkedExecutionBoundary({ run_id: "run-a", project: "project-a", stop_after_execution: true, can_update: false, reason: "报告已开始" }, "run-a", "project-a");
assert.equal(reportStarted.can_update, false);
assert.equal(reportStarted.reason, "报告已开始");
for (const invalid of [null, {}, { ...saved, run_id: "other" }, { ...saved, project: "other" }, { ...saved, stop_after_execution: "true" }, { ...saved, can_update: 1 }, { ...saved, reason: {} }]) {
  assert.throws(() => checkedExecutionBoundary(invalid, "run-a", "project-a"));
}
console.log("Boundary save ordering, retry, identity and capability checks passed");
