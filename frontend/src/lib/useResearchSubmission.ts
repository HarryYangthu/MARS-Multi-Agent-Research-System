"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { lookupResearchSave, researchBackendOrigin, researchSaveError, saveResearch, type ResearchCreated, type ResearchLookup } from "./researchContracts";
import { beginResearchSave, finishResearchSave, readResearchSave, type ResearchSaveIdentity, type StoredResearchSave } from "./researchSubmission";

export type ResearchSubmission = {
  pending: StoredResearchSave | null; created: ResearchCreated | null;
  busy: "saving" | "reconciling" | null; error: string; status: ResearchLookup["status"] | null;
  save: (name: string, contract: unknown, fingerprint: string) => Promise<void>;
  reconcile: () => Promise<void>; resetRejected: () => boolean;
};

export function useResearchSubmission(): ResearchSubmission {
  const [pending, setPending] = useState<StoredResearchSave | null>(null);
  const [created, setCreated] = useState<ResearchCreated | null>(null);
  const [busy, setBusy] = useState<ResearchSubmission["busy"]>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState<ResearchLookup["status"] | null>(null);
  const alive = useRef(false);
  const operation = useRef<AbortController | null>(null);
  const automaticChecked = useRef(false);

  const run = useCallback(async (action: "saving" | "reconciling", identity: ResearchSaveIdentity, name?: string, contract?: unknown): Promise<void> => {
    if (!alive.current || operation.current || researchBackendOrigin() !== identity.origin) return;
    const controller = new AbortController();
    operation.current = controller;
    const active = (): boolean => alive.current && operation.current === controller && !controller.signal.aborted && researchBackendOrigin() === identity.origin;
    setBusy(action); setError(""); setStatus(null);
    try {
      const result = action === "saving"
        ? await saveResearch(name!, contract, identity.request_id, identity.task_sha256, controller.signal)
        : await lookupResearchSave(identity.request_id, identity.task_sha256, controller.signal);
      if (!active()) return;
      setStatus(result.status);
      if (result.status === "created" && result.run) {
        finishResearchSave(identity);
        setCreated(result.run); setPending(null);
      }
    } catch (cause: unknown) { if (active()) setError(researchSaveError(cause)); }
    finally { if (active()) { setBusy(null); operation.current = null; } }
  }, []);

  useEffect(() => {
    alive.current = true;
    try {
      const stored = readResearchSave(researchBackendOrigin());
      setPending(stored);
      if (stored?.kind === "request" && !automaticChecked.current) {
        automaticChecked.current = true;
        // Defer until after the effect's StrictMode probe cleanup. No repeated poll.
        queueMicrotask(() => { if (alive.current && !operation.current) void run("reconciling", stored.identity); });
      }
    } catch { setPending({ kind: "invalid" }); setError("本地会话存储不可用，无法可靠保留请求编号；保存已禁用，请检查浏览器设置。"); }
    return () => { alive.current = false; operation.current?.abort(); operation.current = null; };
  }, [run]);

  async function save(name: string, contract: unknown, fingerprint: string): Promise<void> {
    if (!alive.current || operation.current || created || pending) return;
    try {
      const origin = researchBackendOrigin();
      const existing = readResearchSave(origin);
      if (existing) { setPending(existing); setError("已有保存需要核对，未发出新请求。"); return; }
      const identity = beginResearchSave(origin, fingerprint);
      setPending({ kind: "request", identity });
      await run("saving", identity, name, contract);
    } catch { setError("无法可靠保留保存请求编号；没有发送新请求。请检查浏览器会话存储后再操作。"); }
  }
  async function reconcile(): Promise<void> {
    if (operation.current || created) return;
    try {
      const stored = readResearchSave(researchBackendOrigin());
      setPending(stored);
      if (stored?.kind === "request") await run("reconciling", stored.identity);
      else setError("原请求编号不可用，不能自动核对或重发；请查看原后端的任务记录。");
    } catch { setError("无法读取本地请求编号，保留未知状态；请查看任务记录。"); }
  }
  function resetRejected(): boolean {
    if (status !== "rejected" || operation.current || pending?.kind !== "request") return false;
    try {
      finishResearchSave(pending.identity);
      setPending(null); setStatus(null); setError("");
      return true;
    } catch { setError("原请求标记未能安全清除，继续保留并核对。"); return false; }
  }
  return { pending, created, busy, error, status, save, reconcile, resetRejected };
}
