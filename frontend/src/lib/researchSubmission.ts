// Session-local request identity only: no project paths, contract body or credentials.
const LEGACY_KEY = "mars.research-save-pending.v1";
const PREFIX = "mars.research-save-request.v1:";
export type ResearchSaveIdentity = { schema_id: "research_save_request.v1"; origin: string; request_id: string; task_sha256: string };
export type StoredResearchSave = { kind: "request"; identity: ResearchSaveIdentity } | { kind: "legacy" | "invalid" };

export function researchSaveKey(origin: string): string { return `${PREFIX}${encodeURIComponent(origin)}`; }
export function parseStoredResearchSave(value: string | null, origin: string): StoredResearchSave | null {
  if (value === null) return null;
  try {
    const item: unknown = JSON.parse(value);
    if (typeof item !== "object" || item === null || Array.isArray(item)) return { kind: "invalid" };
    const data = item as Record<string, unknown>;
    if (Object.keys(data).sort().join(",") !== "origin,request_id,schema_id,task_sha256"
        || data.schema_id !== "research_save_request.v1" || data.origin !== origin
        || typeof data.request_id !== "string" || !/^[A-Za-z0-9_-]{8,128}$/.test(data.request_id)
        || typeof data.task_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(data.task_sha256)
        || new URL(origin).origin !== origin) return { kind: "invalid" };
    return { kind: "request", identity: data as ResearchSaveIdentity };
  } catch { return { kind: "invalid" }; }
}
export function readResearchSave(origin: string): StoredResearchSave | null {
  const value = parseStoredResearchSave(window.sessionStorage.getItem(researchSaveKey(origin)), origin);
  if (value) return value;
  return window.sessionStorage.getItem(LEGACY_KEY) !== null ? { kind: "legacy" } : null;
}
export function beginResearchSave(origin: string, fingerprint: string): ResearchSaveIdentity {
  if (readResearchSave(origin)) throw new Error("仍有保存请求需要核对，未发出新的保存请求。");
  const identity: ResearchSaveIdentity = { schema_id: "research_save_request.v1", origin, request_id: crypto.randomUUID(), task_sha256: fingerprint };
  const value = JSON.stringify(identity);
  if (parseStoredResearchSave(value, origin)?.kind !== "request") throw new Error("无法建立有效的保存请求编号，未发送请求。");
  window.sessionStorage.setItem(researchSaveKey(origin), value);
  if (window.sessionStorage.getItem(researchSaveKey(origin)) !== value) throw new Error("无法保留保存请求编号，未发送请求。");
  return identity;
}
export function finishResearchSave(identity: ResearchSaveIdentity): void {
  const current = readResearchSave(identity.origin);
  if (current?.kind !== "request" || current.identity.request_id !== identity.request_id || current.identity.task_sha256 !== identity.task_sha256) {
    throw new Error("当前保存请求标记已变化，保留原标记供核对。");
  }
  window.sessionStorage.removeItem(researchSaveKey(identity.origin));
  if (window.sessionStorage.getItem(researchSaveKey(identity.origin)) !== null) throw new Error("保存已核验，但本地请求标记未能清除；可继续只读核对。");
}
