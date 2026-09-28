/** Pure storage/schema boundaries, not HTTP or a simulated service success. */
import { strict as assert } from "node:assert";
import { parseStoredResearchSave, researchSaveKey, type ResearchSaveIdentity } from "../src/lib/researchSubmission";
import { parseResearchCreated, parseResearchLookup, ResearchApiError, researchSaveError } from "../src/lib/researchContracts";

const fingerprint = "a".repeat(64), requestId = "schema-only-request-12345", origin = "http://127.0.0.1:3012";
const identity: ResearchSaveIdentity = { schema_id: "research_save_request.v1", origin, request_id: requestId, task_sha256: fingerprint };
assert.equal(parseStoredResearchSave(null, origin), null);
assert.deepEqual(parseStoredResearchSave(JSON.stringify(identity), origin), { kind: "request", identity });
for (const value of ["", fingerprint, "null", "[]", "{}", JSON.stringify({ ...identity, origin: "http://127.0.0.1:3013" }),
  JSON.stringify({ ...identity, task_sha256: "broken" }), JSON.stringify({ ...identity, request_id: "../escape" }),
  JSON.stringify({ ...identity, private_path: "/private/input" })]) assert.deepEqual(parseStoredResearchSave(value, origin), { kind: "invalid" });
assert.notEqual(researchSaveKey(origin), researchSaveKey("http://127.0.0.1:3013"));
assert.deepEqual(Object.keys(identity).sort(), ["origin", "request_id", "schema_id", "task_sha256"]);
const schemaRecord = { run_id: "schema-test", project: "schema-test", task: "Parser boundary only", task_sha256: fingerprint,
  request_id: requestId, idempotent: true, status: "created", research_started: false,
  execution_admission: { ready: false, enforced_budget_fields: [], blockers: [{ code: "blocked", message: "Not execution", fields: [] }] } };
assert.equal(parseResearchCreated(schemaRecord, requestId, fingerprint).run_id, "schema-test");
for (const changed of [{ request_id: "different-id" }, { task_sha256: "b".repeat(64) }, { research_started: true }, { idempotent: null }, { idempotent: false },
  { execution_admission: { ready: false, enforced_budget_fields: [], blockers: [{ code: "x", message: "x", fields: [1] }] } }]) {
  assert.throws(() => parseResearchCreated({ ...schemaRecord, ...changed }, requestId, fingerprint));
}
const pending = { request_id: requestId, task_sha256: fingerprint, status: "pending", admitted: true, run_id: null, research_started: false, run: null, reason: "creation_in_progress" };
assert.equal(parseResearchLookup(pending, requestId, fingerprint).status, "pending");
assert.equal(parseResearchLookup({ ...pending, status: "unknown", admitted: null }, requestId, fingerprint).status, "unknown");
assert.equal(parseResearchLookup({ ...pending, status: "rejected", admitted: false, reason: "creation_preflight_rejected" }, requestId, fingerprint).status, "rejected");
assert.equal(parseResearchLookup({ ...pending, status: "created", run_id: "schema-test", run: schemaRecord, reason: null }, requestId, fingerprint).run?.run_id, "schema-test");
for (const changed of [{ task_sha256: "b".repeat(64) }, { task_sha256: null }, { task_sha256: undefined }, { admitted: null }, { admitted: false }, { request_id: "another-request" }, { research_started: true }, { status: "anything" },
  { status: "rejected", admitted: false, run_id: "not-null" }, { status: "rejected", admitted: false, run: schemaRecord },
  { status: "unknown", admitted: false }, { status: "created", run_id: "other-run", run: schemaRecord }, { status: "created", run: null }]) {
  assert.throws(() => parseResearchLookup({ ...pending, ...changed }, requestId, fingerprint));
}
assert.match(researchSaveError(new ResearchApiError(409, [], "creation_request_conflict")), /不会换编号重发/);
assert.match(researchSaveError(new ResearchApiError(404, [], "creation_request_not_found")), /不会自动重发/);
process.stdout.write("Research save identity and response schema checks passed; no HTTP or execution occurred.\n");
