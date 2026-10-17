/** Decorations describe archived changes, never whether a write succeeded. */
export type CodeMarkerKind = "added" | "modified" | "deleted" | "content";
export type CodeMarkerRecord = { path: string; change: string; status: string; source?: string };
export type DirectoryChangeSummary = { total: number; unapplied: number };

export function codeMarkerKind(change: string): CodeMarkerKind | null {
  if (change === "unchanged") return null;
  if (change === "added" || change === "deleted" || change === "content") return change;
  return "modified";
}

export function directoryChangeSummaries(records: Iterable<CodeMarkerRecord>): Map<string, DirectoryChangeSummary> {
  const summaries = new Map<string, DirectoryChangeSummary>();
  for (const record of records) {
    if (!codeMarkerKind(record.change)) continue;
    const parts = record.path.split("/");
    if (parts.some(part => !part || part === "." || part === "..")) continue;
    for (let index = 1; index < parts.length; index += 1) {
      const path = parts.slice(0, index).join("/");
      const summary = summaries.get(path) ?? { total: 0, unapplied: 0 };
      const verified = record.status === "applied" || (record.status === "approved_history" && record.source === "approved_git_history");
      summaries.set(path, { total: summary.total + 1, unapplied: summary.unapplied + (verified ? 0 : 1) });
    }
  }
  return summaries;
}
