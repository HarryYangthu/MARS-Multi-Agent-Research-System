"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { boundedFetch, CLIENT_POLICY, isUncertainRequestError } from "@/lib/clientPolicy";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL?.trim() || "";

export function FrozenResearchImport(): JSX.Element {
  const router = useRouter();
  const [contract, setContract] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState("");
  const alive = useRef(true);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; pending.current?.abort(); }; }, []);

  async function load(file?: File): Promise<void> {
    setContract(null); setError(""); setSummary("");
    if (!file) return;
    if (file.size > CLIENT_POLICY.maxContractBytes) { setError("研究计划文件超过允许大小，请使用完整的单任务 JSON 文件。"); return; }
    setBusy(true);
    try {
      const value: unknown = JSON.parse(await file.text());
      if (!alive.current) return;
      if (typeof value !== "object" || value === null || !("task_sha256" in value) || typeof value.task_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.task_sha256) || !("task" in value) || typeof value.task !== "object" || value.task === null || !("goal" in value.task) || typeof value.task.goal !== "string") throw new Error("请选择由项目预检生成的完整 JSON 文件。");
      setContract(value);
      setSummary(value.task.goal);
      setName(value.task.goal.slice(0, 120));
    } catch { if (alive.current) setError("文件不是完整的研究计划 JSON，尚未保存任何任务。"); }
    finally { if (alive.current) setBusy(false); }
  }

  async function save(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!contract || !name.trim() || busy) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true); setError("");
    try {
      const response = await boundedFetch(`${BASE}/api/research-contracts/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, contract }), signal: controller.signal });
      if (!response.ok) throw new Error(response.status === 409 ? "源文件已变化或计划已失效，请重新预检并冻结后导入。" : "计划校验未通过，请检查文件、路径和预算是否完整。");
      const result: unknown = await response.json();
      if (typeof result !== "object" || result === null || !("run_id" in result) || typeof result.run_id !== "string") throw new Error("服务回执不完整，请到研究任务列表核对，避免重复保存。");
      if (alive.current) router.push(`/runs/${encodeURIComponent(result.run_id)}`);
    } catch (cause: unknown) {
      if (alive.current) setError(isUncertainRequestError(cause) ? "连接中断或等待超时，请先到任务列表核对是否已保存，避免重复提交。" : cause instanceof Error ? cause.message : "保存失败。");
    } finally { if (alive.current) setBusy(false); }
  }

  return <details className="mb-6 rounded-lg border border-mars-border bg-mars-panel p-4">
    <summary className="cursor-pointer text-sm text-slate-300">高级：导入已冻结的研究计划</summary>
    <p className="mt-3 text-sm leading-6 text-amber-200">此接入路径目前仅保存研究配置。所有预算和代码修改边界接入完成前，任务会明确阻止启动，不会调用模型或开展实验。</p>
    <form onSubmit={(event) => void save(event)} className="mt-4 space-y-3">
      <label className="block text-sm text-slate-300" htmlFor="frozen-research-file">研究计划 JSON（与 CLI 使用同一后端校验）</label>
      <input id="frozen-research-file" type="file" accept=".json,application/json" disabled={busy} onChange={(event) => void load(event.target.files?.[0])} className="block max-w-full text-sm file:mr-3 file:rounded file:border-0 file:bg-mars-panel2 file:px-3 file:py-2 file:text-slate-200" />
      {contract ? <><p className="whitespace-pre-wrap break-words text-sm text-slate-400">{summary}</p><label className="block text-sm" htmlFor="frozen-research-name">保存名称</label><input id="frozen-research-name" required maxLength={120} value={name} disabled={busy} onChange={(event) => setName(event.target.value)} className="w-full rounded border border-mars-border bg-mars-bg px-3 py-2 text-sm" /><button disabled={busy} type="submit" className="rounded border border-mars-border px-4 py-2 text-sm disabled:opacity-50">{busy ? "正在校验并保存…" : "校验并保存待执行计划"}</button></> : null}
      {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
    </form>
  </details>;
}
