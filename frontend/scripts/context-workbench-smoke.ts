import { strict as assert } from "node:assert";

import type {
  ContextManifestSummary,
  ContextManifestV2,
  ContextSegment,
} from "../src/lib/api";
import {
  buildManifestDiff,
  filterAndSortSegments,
  filterManifestSummaries,
  formatRawContent,
  formatTokenDelta,
  manifestAgentOptions,
  manifestPurposeOptions,
  riskTotal,
  segmentKindOptions,
  segmentRiskOptions,
  summaryBudgetUsed,
  summaryOverBudget,
} from "../src/lib/contextWorkbench";

const sharedSegment: ContextSegment = {
  id: "system:base",
  kind: "system",
  title: "System Instructions",
  source_ref: "system",
  content_hash: "hash-new",
  tokens_estimated: 40,
  priority: "critical",
  selection_reason: "required",
  compression: "none",
  risk_flags: ["lost_in_middle"],
  text_preview: "Follow MARS rules.",
  raw_ref: null,
};

const addedSegment: ContextSegment = {
  id: "kb:atk",
  kind: "kb",
  title: "PIMC Note",
  source_ref: "knowledge/methodology",
  content_hash: "hash-kb",
  tokens_estimated: 18,
  priority: "medium",
  selection_reason: "top-k relevance",
  compression: "summary",
  risk_flags: ["confusion"],
  text_preview: "Routing note.",
  raw_ref: "raw/idea/kb.json",
};

const removedSegment: ContextSegment = {
  id: "tool:search",
  kind: "tool",
  title: "Search Result",
  source_ref: "tool/search",
  content_hash: "hash-tool",
  tokens_estimated: 16,
  priority: "low",
  selection_reason: "legacy tool output",
  compression: "reference",
  risk_flags: [],
  text_preview: "Old result.",
  raw_ref: "raw/idea/search.json",
};

const previousSharedSegment: ContextSegment = {
  ...sharedSegment,
  content_hash: "hash-old",
  tokens_estimated: 30,
  risk_flags: [],
};

const currentManifest: ContextManifestV2 = {
  schema: "context_manifest.v2",
  manifest_id: "context_manifest.v2.idea.draft.002.json",
  run_id: "run-1",
  agent: "idea",
  node_key: "idea",
  project: "pimc",
  output_schema: "proposal.v1",
  purpose: "draft",
  created_at: "2026-06-17T00:00:00Z",
  budget: { max: 100, target: 70, used: 58, over_budget: false },
  segments: [sharedSegment, addedSegment],
  render_order: ["system:base", "kb:atk"],
  messages_preview: [],
  diagnostics: {},
  raw_refs: ["raw/idea/kb.json"],
};

const compareManifest: ContextManifestV2 = {
  ...currentManifest,
  manifest_id: "context_manifest.v2.idea.draft.001.json",
  budget: { max: 100, target: 70, used: 46, over_budget: false },
  segments: [previousSharedSegment, removedSegment],
  render_order: ["tool:search", "system:base"],
  raw_refs: ["raw/idea/search.json"],
};

const summaries: ContextManifestSummary[] = [
  {
    manifest_id: "m1",
    agent: "idea",
    node_key: "idea",
    purpose: "draft",
    created_at: "2026-06-17T00:00:00Z",
    path: "runs/run-1/context/m1.json",
    budget: { used: 58, over_budget: false },
    segment_count: 2,
    risk_counts: { confusion: 1 },
  },
  {
    manifest_id: "m2",
    agent: "coding",
    node_key: "coding",
    purpose: "schema_repair",
    created_at: "2026-06-17T00:01:00Z",
    path: "runs/run-1/context/m2.json",
    budget: { used: 118, over_budget: true },
    segment_count: 3,
    risk_counts: {},
  },
];

assert.deepEqual(manifestAgentOptions(summaries), ["all", "coding", "idea"]);
assert.deepEqual(manifestPurposeOptions(summaries), ["all", "draft", "schema_repair"]);
assert.deepEqual(
  filterManifestSummaries(summaries, {
    agent: "all",
    purpose: "all",
    riskOnly: true,
    overBudgetOnly: false,
  }).map((item) => item.manifest_id),
  ["m1"],
);
assert.deepEqual(
  filterManifestSummaries(summaries, {
    agent: "all",
    purpose: "all",
    riskOnly: false,
    overBudgetOnly: true,
  }).map((item) => item.manifest_id),
  ["m2"],
);
assert.equal(riskTotal(summaries[0].risk_counts), 1);
assert.equal(summaryBudgetUsed(summaries[1]), 118);
assert.equal(summaryOverBudget(summaries[1]), true);

assert.deepEqual(segmentKindOptions(currentManifest.segments), ["all", "kb", "system"]);
assert.deepEqual(segmentRiskOptions(currentManifest.segments), ["all", "confusion", "lost_in_middle"]);
assert.deepEqual(
  filterAndSortSegments(currentManifest.segments, currentManifest.render_order, {
    kind: "all",
    risk: "all",
    sortKey: "render",
  }).map((segment) => segment.id),
  ["system:base", "kb:atk"],
);
assert.deepEqual(
  filterAndSortSegments(currentManifest.segments, currentManifest.render_order, {
    kind: "all",
    risk: "all",
    sortKey: "tokens_desc",
  }).map((segment) => segment.id),
  ["system:base", "kb:atk"],
);
assert.deepEqual(
  filterAndSortSegments(currentManifest.segments, currentManifest.render_order, {
    kind: "all",
    risk: "confusion",
    sortKey: "render",
  }).map((segment) => segment.id),
  ["kb:atk"],
);

const diff = buildManifestDiff(currentManifest, compareManifest);
assert.equal(diff.tokenDelta, 12);
assert.deepEqual(diff.added.map((item) => item.id), ["kb:atk"]);
assert.deepEqual(diff.removed.map((item) => item.id), ["tool:search"]);
assert.deepEqual(diff.changed.map((item) => item.id), ["system:base"]);
assert.deepEqual(diff.changed[0].changes, ["tokens", "hash", "risk"]);
assert.equal(formatTokenDelta(diff.tokenDelta), "+12");

assert.equal(formatRawContent("{\"status\":\"ok\",\"count\":2}"), "{\n  \"status\": \"ok\",\n  \"count\": 2\n}");
assert.equal(formatRawContent("plain text"), "plain text");

console.log("context workbench smoke passed");
