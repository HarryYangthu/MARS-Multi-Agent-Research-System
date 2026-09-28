/** Pure lossless conversions only; no replacement API/service or research result. */
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import type { ResearchBudget, ResearchProject } from "../src/lib/researchContracts";
import { buildResearchProject, emptyResearchDraft, validateBudget, budgetDraft } from "../src/lib/researchWizard";
import { parseResearchProject, parseResearchProjectJSON, parseSavedSettings, projectToSimpleDraft } from "../src/lib/researchTemplates";

const empty = emptyResearchDraft();
const original = buildResearchProject({ ...empty, projectId: "reuse", displayName: "Reuse complete settings", code: "/workspace/code", output: "/workspace/results",
  knowledge: "/workspace/paper.pdf\n/workspace/notes", data: "/workspace/data", baseline: "baseline.py", allowed: "candidate", protected: "candidate/reference",
  commands: empty.commands.map((item) => ({ ...item, executable: `/python/${item.purpose}`, arguments: `train.py\n--mode\n${item.purpose}`, entries: "train.py\nconfig.json" })),
  metrics: [{ name: "MSE", unit: "unitless", direction: "minimize", target: "0.2", tolerance: "0.001" }, { name: "accuracy", unit: "%", direction: "maximize", target: "99", tolerance: "0" }] }).project!;
assert.ok(original);
const draft = projectToSimpleDraft(original);
assert.ok(draft);
assert.deepEqual(buildResearchProject(draft).project, original);
assert.equal(draft.name, ""); assert.equal(draft.goal, "");
const limit = 1048576;
assert.deepEqual(parseResearchProjectJSON(JSON.stringify(original), limit), original);

const variants: ResearchProject[] = [
  { ...original, commands: original.commands.map((item, index) => index === 0 ? { ...item, name: "custom_check" } : item) },
  { ...original, commands: [...original.commands, { ...original.commands[1], name: "second_train" }] },
  ...["", "two\nlines", " leading", "trailing ", "\tindented", "return\rseparator"].map((argument) => ({ ...original, commands: original.commands.map((item, index) => index === 0 ? { ...item, arguments: ["train.py", argument] } : item) })),
  { ...original, paths: { ...original.paths, knowledge: ["/workspace/name\nwith newline"] } },
];
for (const project of variants) {
  assert.equal(projectToSimpleDraft(project), null);
  assert.deepEqual(parseResearchProjectJSON(JSON.stringify(project), limit), project);
}
assert.deepEqual(projectToSimpleDraft({ ...original, execution: { kind: "ssh", device: "gpu", connection_ref: "saved-gpu-connection" } })?.connectionRef, "saved-gpu-connection");
for (const invalid of [{ ...original, extra: "cannot silently discard" }, { ...original, execution: { ...original.execution, kind: ["local"] } },
  { ...original, commands: [{ ...original.commands[0], purpose: ["check"] }] }, { ...original, metrics: [{ ...original.metrics[0], target: Infinity }] }]) assert.throws(() => parseResearchProject(invalid));
assert.throws(() => parseResearchProjectJSON(JSON.stringify(original), 8));
assert.throws(() => parseResearchProjectJSON(JSON.stringify({ task_sha256: "1".repeat(64), task: original }), limit));
const budget = parse(readFileSync("../configs/research_defaults.yaml", "utf8")).budget as ResearchBudget;
const input = { schema_id: "research_settings.v1", source_run_id: "saved_source_run", project: original, budget, mode: "manual", requires_preflight: true, research_started: false };
const saved = parseSavedSettings(input, "saved_source_run");
assert.equal(Object.keys(saved.budget).length, 23);
assert.deepEqual(validateBudget(budgetDraft(saved.budget)).budget, budget);
for (const invalid of [{ ...input, source_run_id: "another_run" }, { ...input, research_started: true }, { ...input, requires_preflight: false },
  { ...input, task_sha256: "1".repeat(64) }, { ...input, mode: ["manual"] }, { ...input, budget: { ...budget, invented_limit: 1 } },
  { ...input, budget: { ...budget, input_tokens: undefined } }]) assert.throws(() => parseSavedSettings(invalid, "saved_source_run"));
process.stdout.write("Saved research settings lossless conversion checks passed; no requests or execution occurred.\n");
