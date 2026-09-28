/** Pure form/schema checks. No model, backend, fetch replacement or success fixture. */
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import { BUDGET_FIELDS, budgetDraft, buildResearchProject, emptyResearchDraft, lines, validateBudget, validateResearchGoal } from "../src/lib/researchWizard";
import type { ResearchBudget } from "../src/lib/researchContracts";

const defaults = parse(readFileSync("../configs/research_defaults.yaml", "utf8")).budget as ResearchBudget;
assert.deepEqual(BUDGET_FIELDS.map((field) => field.key).sort(), Object.keys(defaults).sort());
const limits = budgetDraft(defaults);
assert.deepEqual(validateBudget(limits).budget, defaults);
for (const field of BUDGET_FIELDS) for (const invalid of ["", "NaN", "Infinity", "1e309", "-1"]) assert.ok(validateBudget({ ...limits, [field.key]: invalid }).issues.some((issue) => issue.field === `budget.${field.key}`));
for (const field of BUDGET_FIELDS.filter((item) => !item.decimal)) assert.ok(validateBudget({ ...limits, [field.key]: "1.2" }).issues.some((issue) => issue.field === `budget.${field.key}`));
assert.equal(validateBudget({ ...limits, operation_retries: "3" }).budget, null);
assert.ok(validateBudget({ ...limits, repeated_error_limit: "3" }).issues.some((issue) => issue.field === "budget.repeated_error_limit"));
assert.equal(validateBudget({ ...limits, automatic_iterations: "0", max_gpus: "0", operation_retries: "0" }).issues.length, 0);
assert.equal(validateBudget({ ...limits, coding_output_tokens: "999999999" }).budget, null);
assert.throws(() => budgetDraft({ ...defaults, model_requests: undefined } as unknown as ResearchBudget));
const empty = emptyResearchDraft();
assert.equal(buildResearchProject(empty).project, null);
assert.equal(validateResearchGoal(empty).length, 2);
assert.ok(buildResearchProject(empty).issues.length > 5);
const complete = { ...empty, projectId: "regression", displayName: "Regression project", name: "Small measured comparison",
  goal: "Compare held-out MSE against the baseline", code: "/workspace/code", output: "/workspace/output",
  baseline: "baseline.py", allowed: "src", protected: "src/reference", executable: "/environment/bin/python",
  commands: empty.commands.map((command) => ({ ...command, arguments: "experiment.py\n--mode\n" + command.purpose, entries: "experiment.py\nconfigs/input.yaml" })),
  metrics: [{ name: "MSE", unit: "unitless", direction: "minimize" as const, target: "0.1", tolerance: "0.01" }] };
const built = buildResearchProject(complete);
assert.deepEqual(built.issues, []);
assert.ok(built.project);
assert.deepEqual(built.project.commands.map((command) => command.purpose), ["check", "train", "evaluate"]);
assert.deepEqual(built.project.commands[0].arguments, ["experiment.py", "--mode", "check"]);
assert.equal(built.project.metrics[0].target, 0.1);
assert.deepEqual(built.project.paths.data, []);
assert.deepEqual(built.project.paths.knowledge, []);
assert.equal(built.project.execution.connection_ref, null);
assert.deepEqual(lines(" a path with spaces \r\n\nsecond "), ["a path with spaces", "second"]);
for (const invalid of ["../escape.py", "src//file.py", "src/*", "C:/data.py", "src\\file.py", "/outside.py"]) assert.ok(buildResearchProject({ ...complete, allowed: invalid }).issues.some((issue) => issue.field === "allowed_paths"));
for (const invalid of ["", "NaN", "1e309"]) assert.ok(buildResearchProject({ ...complete, metrics: [{ ...complete.metrics[0], target: invalid }] }).issues.some((issue) => issue.field === "metrics.0.target"));
assert.ok(buildResearchProject({ ...complete, metrics: [complete.metrics[0], complete.metrics[0]] }).issues.some((issue) => issue.field === "metrics.1.name"));
assert.ok(buildResearchProject({ ...complete, kind: "ssh" }).issues.some((issue) => issue.field === "execution.connection_ref"));
assert.equal(buildResearchProject({ ...complete, kind: "ssh", connectionRef: "saved-reference" }).project?.execution.connection_ref, "saved-reference");
assert.equal(buildResearchProject({ ...complete, commands: complete.commands.map((command) => ({ ...command, cwd: "../outside" })) }).project, null);
assert.equal(buildResearchProject({ ...complete, commands: complete.commands.map((command) => ({ ...command, entries: "" })) }).project, null);
assert.equal(validateResearchGoal({ ...complete, name: "x".repeat(121) }).length, 1);
process.stdout.write("Research wizard pure form checks passed; no requests or execution occurred.\n");
