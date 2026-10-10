import assert from "node:assert/strict";
import { executionExperimentFields } from "../src/lib/executionReviewPresentation";

// Authored presentation inputs only, never a substitute for real job results.
const steps = executionExperimentFields({ config: { device: "cpu", threads: 2 }, effective: {
  config_path: "/code/current.yaml", data_path: "/data/current.pth", seed: 2026,
  budget_unit: "steps", max_iters: 50, training_epochs: 1, output_parent: "/run/execution/group",
} });
assert.equal(new Map(steps).get("参数更新次数"), 50);
assert.equal(new Map(steps).get("实际数据路径"), "/data/current.pth");
assert.equal(new Map(steps).get("结果存放位置"), "/run/execution/group");
assert.equal(new Map(steps).get("线程数"), 2);
assert(!steps.some(([label]) => label.includes("轮")));
const full = new Map(executionExperimentFields({ config: {}, effective: {
  budget_unit: "epochs", max_iters: 200, configured_total_epochs: 200, scheduler_unit: "epochs",
} }));
assert.equal(full.get("完整训练轮次"), 200);
assert.equal(full.get("配置允许的完整训练轮次"), 200);
assert.equal(full.get("学习率调度单位"), "完整训练轮次");
const short = new Map(executionExperimentFields({ config: {}, effective: {
  budget_unit: "steps", max_iters: 50, configured_total_epochs: 200, scheduler_unit: "steps", budget_warning: "仅为更新次数试跑",
} }));
assert.equal(short.get("学习率调度单位"), "参数更新次数");
assert.equal(short.get("预算说明"), "仅为更新次数试跑");
assert.equal(new Map(executionExperimentFields({ config: { budget_unit: "epochs", max_iters: 3 } })).get("完整训练轮次"), 3);
assert.deepEqual(executionExperimentFields({ config: {} }), []);
console.log("Actual execution input and budget presentation checks passed");
