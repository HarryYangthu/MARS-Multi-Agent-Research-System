import { boundedFetch } from "./clientPolicy";

export type InspectionItem = { id: string; kind: string; time: string; model?: string; tool?: string; status?: string; ok?: boolean; backend?: string };
export type InspectionCatalog = { run_id: string; project: string; agent: string; events: InspectionItem[]; requests: InspectionItem[] };
export type InspectionEvent = { id: string; event: Record<string, unknown>; assembly: Record<string, unknown> | null; wire_payload: unknown; prompt_source: string };
const BASE = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";
export async function readInspection<T>(runId: string, agent: string, eventId?: string, signal?: AbortSignal): Promise<T> {
  const suffix = eventId ? `/events/${encodeURIComponent(eventId)}` : "";
  const response = await boundedFetch(`${BASE}/api/traces/${encodeURIComponent(runId)}/agents/${encodeURIComponent(agent)}${suffix}`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("暂时无法读取 Agent 记录，请重新连接后刷新。");
  return response.json() as Promise<T>;
}
export const objectValue = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
export function promptMessages(detail: InspectionEvent): Record<string, unknown>[] {
  const payload = detail.wire_payload ?? detail.event.visible;
  const body = objectValue(payload);
  const messages = Array.isArray(payload) ? payload : body.messages ?? body.contents;
  const list = Array.isArray(messages) ? messages.map(objectValue).map(message => message.parts ? { ...message, content: message.parts } : message) : [];
  const system = body.system ?? body.systemInstruction;
  return system ? [{ role: "system", content: system }, ...list] : list;
}
