import type {
  ContextManifestSummary,
  ContextManifestV2,
  ContextSegment,
} from "./api";

export const SORT_KEYS = ["render", "tokens_desc", "priority", "risk"] as const;
export type SortKey = (typeof SORT_KEYS)[number];

export type ManifestFilters = {
  agent: string;
  purpose: string;
  riskOnly: boolean;
  overBudgetOnly: boolean;
};

export type SegmentFilters = {
  kind: string;
  risk: string;
  sortKey: SortKey;
};

export type SegmentDiffItem = {
  id: string;
  title: string;
  tokens: number;
  kind: string;
};

export type ChangedSegmentDiff = {
  id: string;
  title: string;
  tokenDelta: number;
  currentTokens: number;
  compareTokens: number;
  changes: string[];
};

export type ManifestDiff = {
  added: SegmentDiffItem[];
  removed: SegmentDiffItem[];
  changed: ChangedSegmentDiff[];
  tokenDelta: number;
};

const PRIORITY_RANK: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export function manifestAgentOptions(manifests: ContextManifestSummary[]): string[] {
  return optionValues(manifests.map((item) => item.agent));
}

export function manifestPurposeOptions(manifests: ContextManifestSummary[]): string[] {
  return optionValues(manifests.map((item) => item.purpose));
}

export function segmentKindOptions(segments: ContextSegment[]): string[] {
  return optionValues(segments.map((item) => item.kind));
}

export function segmentRiskOptions(segments: ContextSegment[]): string[] {
  return optionValues(segments.flatMap((item) => item.risk_flags));
}

export function filterManifestSummaries(
  manifests: ContextManifestSummary[],
  filters: ManifestFilters,
): ContextManifestSummary[] {
  return manifests.filter((item) => {
    const agentOk = filters.agent === "all" || item.agent === filters.agent;
    const purposeOk = filters.purpose === "all" || item.purpose === filters.purpose;
    const riskOk = !filters.riskOnly || riskTotal(item.risk_counts) > 0;
    const budgetOk = !filters.overBudgetOnly || summaryOverBudget(item);
    return agentOk && purposeOk && riskOk && budgetOk;
  });
}

export function filterAndSortSegments(
  segments: ContextSegment[],
  renderOrder: string[],
  filters: SegmentFilters,
): ContextSegment[] {
  const renderOrderIndex = new Map(renderOrder.map((id, index) => [id, index]));
  const items = segments.filter((segment) => {
    const kindOk = filters.kind === "all" || segment.kind === filters.kind;
    const riskOk = filters.risk === "all" || segment.risk_flags.includes(filters.risk);
    return kindOk && riskOk;
  });
  items.sort((left, right) => {
    if (filters.sortKey === "tokens_desc") {
      return right.tokens_estimated - left.tokens_estimated;
    }
    if (filters.sortKey === "priority") {
      return (PRIORITY_RANK[left.priority] ?? 99) - (PRIORITY_RANK[right.priority] ?? 99);
    }
    if (filters.sortKey === "risk") {
      return right.risk_flags.length - left.risk_flags.length;
    }
    return (renderOrderIndex.get(left.id) ?? 9999) - (renderOrderIndex.get(right.id) ?? 9999);
  });
  return items;
}

export function riskTotal(riskCounts: Record<string, number>): number {
  return Object.values(riskCounts).reduce((total, count) => total + count, 0);
}

export function summaryBudgetUsed(item: ContextManifestSummary): number {
  const used = item.budget.used;
  return typeof used === "number" && Number.isFinite(used) ? used : 0;
}

export function summaryOverBudget(item: ContextManifestSummary): boolean {
  return item.budget.over_budget === true;
}

export function buildManifestDiff(
  current: ContextManifestV2,
  compare: ContextManifestV2,
): ManifestDiff {
  const currentMap = new Map(current.segments.map((segment) => [segment.id, segment]));
  const compareMap = new Map(compare.segments.map((segment) => [segment.id, segment]));
  const added = current.segments
    .filter((segment) => !compareMap.has(segment.id))
    .map(toSegmentDiffItem);
  const removed = compare.segments
    .filter((segment) => !currentMap.has(segment.id))
    .map(toSegmentDiffItem);
  const changed: ChangedSegmentDiff[] = [];

  currentMap.forEach((segment, id) => {
    const other = compareMap.get(id);
    if (!other) return;
    const changes: string[] = [];
    if (segment.tokens_estimated !== other.tokens_estimated) changes.push("tokens");
    if (segment.content_hash !== other.content_hash) changes.push("hash");
    if (segment.compression !== other.compression) changes.push("compression");
    if (segment.priority !== other.priority) changes.push("priority");
    if (!sameStringSet(segment.risk_flags, other.risk_flags)) changes.push("risk");
    if (changes.length === 0) return;
    changed.push({
      id,
      title: segment.title,
      tokenDelta: segment.tokens_estimated - other.tokens_estimated,
      currentTokens: segment.tokens_estimated,
      compareTokens: other.tokens_estimated,
      changes,
    });
  });

  return {
    added,
    removed,
    changed,
    tokenDelta: current.budget.used - compare.budget.used,
  };
}

export function formatTokenDelta(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

export function formatRawContent(content: string): string {
  try {
    const parsed: unknown = JSON.parse(content);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return content;
  }
}

function optionValues(values: string[]): string[] {
  return ["all", ...Array.from(new Set(values)).sort()];
}

function toSegmentDiffItem(segment: ContextSegment): SegmentDiffItem {
  return {
    id: segment.id,
    title: segment.title,
    tokens: segment.tokens_estimated,
    kind: segment.kind,
  };
}

function sameStringSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const rightSet = new Set(right);
  return left.every((item) => rightSet.has(item));
}
