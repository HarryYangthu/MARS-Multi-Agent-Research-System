import { decideDiscoveryCandidate } from "./api";
import { syntheticDiscoveryReplay } from "./fixtures/synthetic-replay";
import { candidateRows, metricNames } from "./selectors";
import type { CandidateDecisionRequest, CreateDiscoveryRunRequest } from "./types";

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
const decisionRequest: CandidateDecisionRequest = {
  action: "promote",
  actor: "researcher",
  reason: "best validation tradeoff",
  idempotency_key: "synthetic-decision-001",
};
assert(rows.length === 20, "fixture must expose 20 candidates");
assert(
  createRequest.spec.project_inputs.validation_split === 0.2,
  "create contract must retain dynamic project inputs",
);
assert(decisionRequest.actor === "researcher", "candidate decisions must record the actor");
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

async function decisionApiSmoke(): Promise<void> {
  const candidate = syntheticDiscoveryReplay.candidates[0];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(input.toString());
    assert(
      url.pathname ===
        "/api/discovery/runs/synthetic-preview/candidates/candidate-001/decision",
      "candidate decision must use the frozen REST path",
    );
    assert(init?.method === "POST", "candidate decision must use POST");
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    assert(body.action === "promote", "candidate decision must preserve action");
    assert(body.actor === "researcher", "candidate decision must preserve actor");
    assert(
      body.idempotency_key === "synthetic-decision-001",
      "candidate decision must preserve idempotency key",
    );
    return new Response(
      JSON.stringify({
        run_id: "synthetic-preview",
        candidate_id: candidate.candidate_id,
        action: "promote",
        status: "promoted",
        audit_ref: "hitl/candidate-001-decision.json",
        candidate,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  try {
    const response = await decideDiscoveryCandidate(
      "synthetic-preview",
      candidate.candidate_id,
      decisionRequest,
    );
    assert(response.audit_ref.length > 0, "candidate decision response must expose audit ref");
  } finally {
    globalThis.fetch = originalFetch;
  }
}

void decisionApiSmoke()
  .then(() => console.log("discovery fixture smoke passed"))
  .catch((caught: unknown) => {
    console.error(caught);
    process.exitCode = 1;
  });
