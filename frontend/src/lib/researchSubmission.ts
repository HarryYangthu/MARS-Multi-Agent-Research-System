// Only an opaque plan fingerprint is kept; project paths and user input stay in memory.
const KEY = "mars.research-save-pending.v1";
export function hasPendingResearchSave(): boolean {
  try { return window.sessionStorage.getItem(KEY) !== null; } catch { return false; }
}
export function markPendingResearchSave(fingerprint: string): void {
  try { window.sessionStorage.setItem(KEY, fingerprint); } catch { /* In-memory lock remains active. */ }
}
export function clearPendingResearchSave(): void {
  try { window.sessionStorage.removeItem(KEY); } catch { /* The current session can continue. */ }
}
