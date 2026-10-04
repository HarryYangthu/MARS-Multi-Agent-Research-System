import { boundedFetch } from "./clientPolicy";

export type LiteratureSource = {
  id: string; title: string; url: string; source_id: string;
  reading_status: "unread" | "read" | "method_complete" | "unavailable" | "unverified";
  decision: string; reason: string; method_summary: string; limitations: string;
  complete_pages: number[]; error: string;
};
export type LiteratureComparison = {
  direction: string; source_ids: string[]; mechanism: string;
  compatibility: string; tradeoff: string; decision: string;
};
export type LiteratureEvidence = {
  run_id: string; project: string; proposal_version: string; invocation: string;
  statistics_verified: boolean; quality_evaluated: boolean;
  counts: { candidates: number; read: number; method_complete: number; adopted: number };
  sources: LiteratureSource[]; method_comparison: LiteratureComparison[];
  coverage: { label: string; finding: string; remaining_gap: string }[];
  stop_reason: string; warnings: string[];
};
export function literatureStatus(source: LiteratureSource): string {
  const read = { unread: "未读正文", read: "已读正文片段", method_complete: "方法页阅读完整", unavailable: "正文获取失败", unverified: "阅读凭据待核对" }[source.reading_status];
  const decision = { use: "方案采用", reject: "未采用", defer: "待补充" }[source.decision as "use" | "reject" | "defer"];
  return decision ? `${read} · ${decision}` : `${read} · 尚未记录筛选结论`;
}
export async function getLiteratureEvidence(runId: string, project: string, signal: AbortSignal): Promise<LiteratureEvidence> {
  const query = new URLSearchParams({ project });
  const base = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
  const response = await boundedFetch(`${base}/api/artifacts/${encodeURIComponent(runId)}/idea/literature-evidence?${query}`, { signal });
  if (!response.ok) throw new Error("调研记录暂时无法校验，请稍后重试。");
  const data: LiteratureEvidence = await response.json();
  if (data.run_id !== runId || data.project !== project) throw new Error("调研记录与当前项目不一致。");
  return data;
}
