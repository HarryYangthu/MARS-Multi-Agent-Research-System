import { boundedFetch } from "@/lib/clientPolicy";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
export type OfficeFile = { kind: string; path: string; status: string; bytes?: number; error?: string; sha256?: string };
export type OfficeBundle = { exists: boolean; current?: boolean; report_ready?: boolean; manifest?: string; metadata?: { deliverables: OfficeFile[]; images?: OfficeFile[]; materials_archive?: OfficeFile; materials_saved?: boolean; qa_status?: { status: string }; generation_errors?: string[] } };
export type ReportSkills = { project: string; selected: string[]; options: { id: string; version: string; name: string; description: string; available: boolean; reason: string; required_tools: string[] }[] };

export async function reportRequest<T>(runId: string, suffix: string, signal: AbortSignal, method = "GET", body?: object): Promise<T> {
  const response = await boundedFetch(`${BASE}/api/reports/${encodeURIComponent(runId)}${suffix}`, {
    method, signal, cache: "no-store", ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  const value: unknown = await response.json();
  if (!response.ok) {
    const detail = typeof value === "object" && value !== null && "detail" in value && typeof value.detail === "string" ? value.detail : "操作未完成，请检查服务连接后重试。";
    throw new Error(detail);
  }
  return value as T;
}

export function officeFileUrl(runId: string, file: OfficeFile, manifest: string): string {
  return `${BASE}/api/reports/${encodeURIComponent(runId)}/files/${encodeURIComponent(file.path.split("/").pop() || "")}?${new URLSearchParams({ manifest })}`;
}
