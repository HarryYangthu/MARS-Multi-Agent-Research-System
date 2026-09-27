import { boundedFetch } from "@/lib/clientPolicy";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";

type Role = "baseline" | "candidate" | "ablation" | "unknown";
type Verification = "verified_local_receipt" | "unverified" | "invalid";
export type RunResults = {
  schema_id: "run_results.v1";
  identity: { run_id: string; project: string; task: string; question: string | null };
  state: { status: string; authority: "sqlite" | "legacy_json" | "missing" | "invalid"; revision: number | null; read_only: boolean; updated_at: string | null };
  outcome: { status: "unknown" | "goal_met" | "goal_not_met" | "budget_stopped" | "user_cancelled" | "blocked" | "failed"; reason: string };
  evidence: { level: "missing" | "unverified" | "receipt_verified"; verified_jobs: number; total_jobs: number };
  experiments: { experiment_id: string; job_id: string | null; role: Role; status: string; verification: Verification; duration_seconds: number | null; seed: number | null; source_id: string | null }[];
  metrics: { experiment_id: string; job_id: string | null; name: string; value: number | null; unit: string | null; direction: "minimize" | "maximize" | null; role: Role; verification: Verification; source_id: string | null }[];
  curves: { experiment_id: string; job_id: string; metric: string; points: number[]; source_id: string }[];
  resources: { status: "missing" | "recorded" | "invalid"; model_requests: number | null; logical_records: number | null; observed_sdk_attempts: number | null; charged_sdk_attempts: number | null; reserved_sdk_attempts: number | null; calls_with_unknown_attempt_count: number | null; observed_attempts_complete: boolean | null; input_tokens: number | null; billed_output_tokens: number | null; cost: number | null; currency: string | null; usage_complete: boolean | null };
  conclusions: { facts: string[]; hypotheses: string[]; interpretations: string[] };
  limitations: string[];
  sources: { id: string; kind: string; sha256: string; bytes: number }[];
  reproduction: { status: "reviewable_only"; external_requirements: string[]; independent_rerun_verified: false };
  statistics: { experiment_id: string; metric: string; n: number; mean: number; standard_deviation: number | null; independent_repeats: boolean }[];
};

export async function getResults(runId: string, signal: AbortSignal): Promise<RunResults> {
  const response = await boundedFetch(`${BASE}/api/results/${encodeURIComponent(runId)}`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(response.status === 404 ? "没有找到此任务，可能已移入回收站。" : "无法读取结果，请检查本地服务或任务产物后重试。");
  return response.json() as Promise<RunResults>;
}

export async function downloadResultExport(runId: string, signal: AbortSignal): Promise<void> {
  const root = `${BASE}/api/results/${encodeURIComponent(runId)}/exports`;
  const response = await boundedFetch(root, { method: "POST", signal });
  if (!response.ok) throw new Error("报告导出失败，请检查产物完整性和可用磁盘空间后重试。");
  const result: unknown = await response.json();
  if (typeof result !== "object" || result === null || !("export_id" in result) || typeof result.export_id !== "string" || !/^[A-Za-z0-9_-]+$/.test(result.export_id)) throw new Error("导出回执无效，未开始下载。");
  signal.throwIfAborted();
  const link = document.createElement("a");
  // The backend verifies the immutable archive again before serving it.
  // Keep the download on the authenticated app origin, including in Electron.
  link.href = `${root}/${encodeURIComponent(result.export_id)}/download`;
  link.download = `mars-results-${result.export_id}.zip`;
  document.body.append(link);
  link.click();
  link.remove();
}
