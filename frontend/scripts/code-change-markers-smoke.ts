import assert from "node:assert/strict";
import { codeMarkerKind, directoryChangeSummaries } from "../src/lib/codeChangeMarkers";

// Authored inputs test presentation only; they do not simulate tool execution.
assert.equal(codeMarkerKind("added"), "added");
assert.equal(codeMarkerKind("deleted"), "deleted");
assert.equal(codeMarkerKind("renamed"), "modified");
assert.equal(codeMarkerKind("content"), "content", "unknown before-state must not imply an added file");
assert.equal(codeMarkerKind("unchanged"), null);
const summaries = directoryChangeSummaries([
  { path: "src/api/changed.py", change: "modified", status: "applied" },
  { path: "src/api/pending.py", change: "added", status: "not_applied" },
  { path: "src/unchanged.py", change: "unchanged", status: "applied" },
  { path: "libs/preview.py", change: "content", status: "proposed" },
  { path: "libs-extra/deleted.py", change: "deleted", status: "applied" },
  { path: "root.py", change: "modified", status: "recorded" },
  { path: "../escape.py", change: "modified", status: "applied" },
  { path: "/absolute/file.py", change: "modified", status: "applied" },
]);
assert.deepEqual(summaries.get("src"), { total: 2, unapplied: 1 });
assert.deepEqual(summaries.get("src/api"), { total: 2, unapplied: 1 });
assert.deepEqual(summaries.get("libs"), { total: 1, unapplied: 1 });
assert.deepEqual(summaries.get("libs-extra"), { total: 1, unapplied: 0 });
assert.equal(summaries.size, 4, "only real path ancestors receive a marker");
