import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseCapabilityCatalog } from "../src/lib/capabilityCatalog";

// The positive input is an actual captured API response, never a service double.
const raw: unknown = JSON.parse(readFileSync(process.argv[2], "utf8"));
const catalog = parseCapabilityCatalog(raw);
assert.ok(catalog.tools.length > 0);
assert.ok(catalog.tools.every((row) => row.certification_status === "not_run" && !row.execution_authorized));
for (const change of [
  { ...catalog, certification_validation_available: true },
  { ...catalog, research_started: true },
  { ...catalog, tools: [{ ...catalog.tools[0], certification_status: "passed" }] },
  { ...catalog, tools: [{ ...catalog.tools[0], execution_authorized: true }] },
  { ...catalog, tools: [{ ...catalog.tools[0], registered: "yes" }] },
  { ...catalog, tools: [catalog.tools[0], catalog.tools[0]] },
  { ...catalog, context_sha256: "unknown" },
]) assert.throws(() => parseCapabilityCatalog(change));
process.stdout.write("Actual catalog parsed; seven malformed or unsupported evidence states rejected.\n");
