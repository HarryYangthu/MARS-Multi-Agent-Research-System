import { renderToStaticMarkup } from "react-dom/server";

import { AddHypothesisForm } from "./AddHypothesisForm";
import { createHypothesis } from "./api";
import { syntheticDiscoveryPayloadFixture } from "./fixtures/synthetic-discovery";
import { normalizeIdeaDiscoveryPayload } from "./normalize";
import type { HypothesisCreateInput } from "./types";

function assert(condition: boolean, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

const discovery = normalizeIdeaDiscoveryPayload(syntheticDiscoveryPayloadFixture);
assert(discovery.hypotheses.length === 2, "idea fixture must keep its hypotheses");

const formHtml = renderToStaticMarkup(
  <AddHypothesisForm
    defaultActor="researcher"
    onCancel={() => undefined}
    onSubmit={async () => ({ audit_ref: "audit/hypothesis-create.json" })}
  />,
);
for (const expected of ["Actor", "Reason", "Statement", "Add to pool", "researcher"]) {
  assert(formHtml.includes(expected), `add hypothesis form did not render ${expected}`);
}

async function apiSmoke(): Promise<void> {
  const input: HypothesisCreateInput = {
    actor: "researcher",
    reason: "cover an unexplored mechanism",
    statement: "A constrained variant improves the validation score.",
  };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (request: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const rawUrl = request.toString();
    const path = rawUrl.startsWith("http") ? new URL(rawUrl).pathname : rawUrl;
    assert(
      path === "/api/runs/synthetic-run-001/idea-discovery/hypotheses",
      "create hypothesis must use the frozen REST path",
    );
    assert(init?.method === "POST", "create hypothesis must use POST");
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    assert(body.actor === input.actor, "create hypothesis must preserve actor");
    assert(body.reason === input.reason, "create hypothesis must preserve reason");
    assert(body.statement === input.statement, "create hypothesis must preserve statement");
    return new Response(JSON.stringify({ audit_ref: "audit/hypothesis-create.json" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const audit = await createHypothesis("synthetic-run-001", input);
    assert(audit.audit_ref === "audit/hypothesis-create.json", "audit record must be returned");
  } finally {
    globalThis.fetch = originalFetch;
  }
}

void apiSmoke()
  .then(() => process.stdout.write("idea discovery fixture smoke passed\n"))
  .catch((caught: unknown) => {
    console.error(caught);
    process.exitCode = 1;
  });
