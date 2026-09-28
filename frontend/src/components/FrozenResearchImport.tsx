"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { admissionMessage } from "@/lib/researchContracts";
import type { ResearchSubmission } from "@/lib/useResearchSubmission";
import { ResearchSaveRecovery } from "./ResearchSaveRecovery";

export function FrozenResearchImport({ submission, onRejectedReset }: { submission: ResearchSubmission; onRejectedReset: () => void }): JSX.Element {
  const [contract, setContract] = useState<unknown>(null);
  const [fingerprint, setFingerprint] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { created } = submission;
  const disabled = busy || submission.pending !== null || submission.busy !== null;
  const [summary, setSummary] = useState("");
  const alive = useRef(true);
  const fileRevision = useRef(0);
  const fileInput = useRef<HTMLInputElement | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  async function load(file?: File): Promise<void> {
    const revision = ++fileRevision.current;
    setContract(null); setError(""); setSummary(""); setFingerprint("");
    if (!file) return;
    if (file.size > CLIENT_POLICY.maxContractBytes) { setError("研究计划文件超过允许大小，请使用完整的单任务 JSON 文件。"); return; }
    setBusy(true);
    try {
      const value: unknown = JSON.parse(await file.text());
      if (!alive.current || revision !== fileRevision.current) return;
      if (typeof value !== "object" || value === null || !("task_sha256" in value) || typeof value.task_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.task_sha256) || !("task" in value) || typeof value.task !== "object" || value.task === null || !("goal" in value.task) || typeof value.task.goal !== "string") throw new Error("请选择由项目预检生成的完整 JSON 文件。");
      setContract(value); setFingerprint(value.task_sha256); setSummary(value.task.goal); setName(value.task.goal.slice(0, 120));
    } catch { if (alive.current && revision === fileRevision.current) setError("文件不是完整的研究计划 JSON，尚未保存任何任务。"); }
    finally { if (alive.current && revision === fileRevision.current) setBusy(false); }
  }
  async function save(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!contract || !name.trim() || disabled || created) return;
    setError("");
    await submission.save(name.trim(), contract, fingerprint);
  }

  return <section className="rounded-lg border border-mars-border bg-mars-panel p-4" aria-labelledby="frozen-import-title">
    <h2 id="frozen-import-title" className="text-sm font-medium text-slate-200">导入已冻结的研究计划</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">与 CLI 使用同一后端校验。这里只保存待执行任务，不发送启动请求。</p>
    {created ? <div className="mt-4 space-y-3"><p className="text-sm text-emerald-200">{created.task} 已保存，研究尚未启动。</p><ul className="space-y-2 text-sm text-amber-200">{created.execution_admission.blockers.map((item) => <li key={item.code}>{admissionMessage(item.code, item.message)}</li>)}</ul><Link className="inline-block text-sm underline" href={`/runs/${encodeURIComponent(created.run_id)}`}>查看已保存任务</Link></div> : <form onSubmit={(event) => void save(event)} className="mt-4 space-y-3">
      <label className="block text-sm text-slate-300" htmlFor="frozen-research-file">研究计划 JSON</label>
      <input ref={fileInput} id="frozen-research-file" type="file" accept=".json,application/json" disabled={disabled} onChange={(event) => void load(event.target.files?.[0])} className="block max-w-full text-sm file:mr-3 file:rounded file:border-0 file:bg-mars-panel2 file:px-3 file:py-2 file:text-slate-200" />
      {contract ? <><p className="whitespace-pre-wrap break-words text-sm text-slate-400">{summary}</p><label className="block text-sm" htmlFor="frozen-research-name">保存名称</label><input id="frozen-research-name" required maxLength={120} value={name} disabled={disabled} onChange={(event) => setName(event.target.value)} className="w-full rounded border border-mars-border bg-mars-bg px-3 py-2 text-sm" /><button disabled={disabled} type="submit" className="rounded border border-mars-border px-4 py-2 text-sm disabled:opacity-50">{submission.busy === "saving" ? "正在校验并保存…" : "校验并保存待执行计划"}</button></> : null}
    </form>}
    {error ? <p role="alert" className="mt-3 text-sm text-amber-200">{error}</p> : null}
    <ResearchSaveRecovery submission={submission} onRejectedReset={() => { if (fileInput.current) fileInput.current.value = ""; setContract(null); setFingerprint(""); setSummary(""); setName(""); setError("请重新预检并导入新的冻结计划。"); onRejectedReset(); }} />
  </section>;
}
