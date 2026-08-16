import { syntheticDiscoveryReplay } from "./fixtures/synthetic-replay";
import { candidateRows, metricNames } from "./selectors";
import type { CreateDiscoveryRunRequest } from "./types";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(message);
}

const rows = candidateRows(syntheticDiscoveryReplay);
const createRequest: CreateDiscoveryRunRequest = {
  spec: {
    task: "compare deterministic variants",
    project: "synthetic_regression",
    objective: "maximize validation score",
    evaluator_hash: "evaluator-synthetic-v1",
    objectives: [{ name: "validation_score", direction: "maximize", unit: "score" }],
    project_inputs: { validation_split: 0.2 },
  },
  idempotency_key: "synthetic-create-001",
};
assert(rows.length === 20, "fixture must expose 20 candidates");
assert(
  createRequest.spec.project_inputs.validation_split === 0.2,
  "create contract must retain dynamic project inputs",
);
assert(syntheticDiscoveryReplay.evaluations.length === 18, "two candidates must be unevaluated");
assert(rows.filter((row) => row.pareto).length === 3, "fixture must expose three Pareto candidates");
assert(
  rows.find((row) => row.candidate.candidate_id === "candidate-017")?.preflight === "blocked",
  "quarantine event must map to a blocked preflight",
);
assert(
  rows.find((row) => row.candidate.candidate_id === "candidate-016")?.gate === "blocked",
  "failed hard constraint must map to a blocked gate",
);
assert(metricNames(rows).length === 2, "fixture must expose a two-metric objective space");
assert(
  rows.find((row) => row.candidate.candidate_id === "candidate-011")?.candidate.parent_ids[0] ===
    "candidate-001",
  "second generation must preserve parent lineage",
);

console.log("discovery fixture smoke passed");
