"use client";

import Link from "next/link";
import { useId } from "react";
import type { ResearchSubmission } from "@/lib/useResearchSubmission";

export function ResearchSaveRecovery({ submission, onRejectedReset }: { submission: ResearchSubmission; onRejectedReset: () => void }): JSX.Element | null {
  const titleId = useId();
  const { pending, busy, error, status, reconcile } = submission;
  if (!pending && !error) return null;
  return <section role="status" className="space-y-3 rounded-lg border border-amber-500/40 bg-mars-panel p-4" aria-labelledby={titleId}>
    <h2 id={titleId} className="font-semibold text-amber-200">{busy === "saving" ? "正在保存待执行计划" : "核对原保存请求"}</h2>
    {pending ? <p className="text-sm leading-6 text-slate-300">{pending.kind === "request"
      ? busy === "reconciling" ? "正在向原后端只读核对保存状态，没有再次提交计划。"
        : status === "pending" ? "后端仍在处理原请求。此页不会自动轮询或再次保存，可稍后手动核对。"
          : status === "rejected" ? "后端已明确确认：原请求在创建任务前被拒绝，未创建任务。可修改内容后重新预检。"
          : status === "unknown" ? "后端当前无法确认完整保存结果。继续保留原请求，不创建新的任务。"
            : "已保留本次请求编号。超时或离页不会撤销后端已受理的保存；核对只读取原请求。"
      : "旧版或不可用的本地标记缺少可验证的请求编号，无法自动恢复。请查看任务记录；本页不会清锁重发。"}</p> : null}
    {error ? <p role="alert" className="text-sm leading-6 text-amber-200">{error}</p> : null}
    <div className="flex flex-wrap gap-3">
      {pending?.kind === "request" && status === "rejected" ? <button type="button" disabled={busy !== null} onClick={() => { if (submission.resetRejected()) onRejectedReset(); }} className="rounded-md border border-mars-border px-4 py-2 text-sm text-slate-200 hover:bg-mars-panel2 disabled:opacity-40">修改后重新预检</button> : null}
      {pending?.kind === "request" ? <button type="button" disabled={busy !== null} onClick={() => void reconcile()} className="rounded-md border border-mars-border px-4 py-2 text-sm text-slate-200 hover:bg-mars-panel2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-300 disabled:opacity-40">{busy === "reconciling" ? "正在核对…" : "核对原请求"}</button> : null}
      <Link href="/runs" className="rounded-md border border-mars-border px-4 py-2 text-sm text-slate-200">到研究任务列表核对</Link>
    </div>
    {pending?.kind === "request" ? <details className="text-xs text-slate-500"><summary className="cursor-pointer">请求与后端标识</summary><p className="mt-2 break-all">后端：{pending.identity.origin}</p><p className="mt-1 break-all">请求：{pending.identity.request_id}</p></details> : null}
  </section>;
}
