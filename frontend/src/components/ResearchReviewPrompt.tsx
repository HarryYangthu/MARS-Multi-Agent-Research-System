"use client";

import { useEffect, useRef, useState } from "react";
import type { ArtifactView, RunDetail } from "@/lib/api";
import { agentLabel } from "@/lib/researchActivity";
import { pendingReviewStage, reviewPromptIdentity } from "@/lib/runReview";
import { ArtifactReviewDocument } from "./ArtifactReviewDocument";

export function ResearchReviewPrompt({ run, artifact, stale, onChanged }: { run: RunDetail | null; artifact: ArtifactView | null; stale: boolean; onChanged: () => Promise<void> }): JSX.Element | null {
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const presented = useRef(new Set<string>());
  const [opened, setOpened] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [reviewed, setReviewed] = useState("");
  const stage = run ? pendingReviewStage(run) : null;
  const identity = run && stage && artifact?.run_id === run.run_id && artifact.agent_dir === stage
    ? reviewPromptIdentity(run, stage, artifact) : "";

  useEffect(() => {
    dialog.current?.close(); setOpened(false); setBusy(false);
  }, [stage]);

  useEffect(() => {
    if (!identity || stale || presented.current.has(identity)) return;
    const element = dialog.current;
    if (!element) return;
    if (!element.open) {
      element.showModal();
      setOpened(true);
      if (content.current) content.current.scrollTop = 0;
      title.current?.focus({ preventScroll: true });
    }
    presented.current.add(identity);
  }, [identity, stale]);

  function close(): void { if (!busy) dialog.current?.close(); }
  function open(): void {
    if (!dialog.current?.open) { dialog.current?.showModal(); setOpened(true); }
    if (content.current) content.current.scrollTop = 0;
    title.current?.focus({ preventScroll: true });
    if (identity) presented.current.add(identity);
  }
  if (!run || !stage) return notice ? <p role="status" className="text-sm text-indigo-200">{notice}</p> : null;
  const advanced = `/runs/${encodeURIComponent(run.run_id)}?view=advanced&agent=${stage}`;
  return <>
    {notice ? <p role="status" className="text-sm text-indigo-200">{notice}</p> : null}
    {reviewed !== identity || !identity ? <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/25 bg-amber-500/5 px-4 py-3">
      <div><p className="text-sm text-amber-200">{agentLabel(stage)}方案等待审核</p><p className="mt-1 text-xs text-slate-400">审核通过后继续下一阶段。</p></div>
      <button type="button" disabled={!identity || stale} onClick={open} className="rounded-lg bg-mars-accent px-4 py-2 text-sm text-white hover:brightness-110 disabled:opacity-40">审核方案</button>
    </div> : null}
    <dialog ref={dialog} aria-labelledby="research-review-title" aria-describedby="research-review-description" onClose={() => setOpened(false)} onCancel={event => { if (busy) event.preventDefault(); }} className="max-h-[90dvh] w-[min(960px,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-mars-border bg-mars-bg p-0 text-slate-200 shadow-2xl backdrop:bg-black/70">
      <div className="flex max-h-[90dvh] flex-col">
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-mars-border px-5 py-4 sm:px-6">
          <div className="min-w-0"><h2 ref={title} tabIndex={-1} id="research-review-title" className="text-lg font-semibold outline-none">审核{agentLabel(stage)}方案</h2><p id="research-review-description" className="mt-1 truncate text-xs text-slate-400">{run.task}</p></div>
          <button type="button" disabled={busy} onClick={close} className="shrink-0 rounded-lg border border-mars-border px-3 py-2 text-xs text-slate-300 hover:bg-mars-panel disabled:opacity-40">稍后审核</button>
        </header>
        <div ref={content} className="min-h-0 overflow-y-auto p-4 sm:p-5">
          {opened ? <ArtifactReviewDocument key={`${run.run_id}:${stage}`} run={run} stage={stage} state="waiting_review" stale={stale} advanced={advanced} onChanged={onChanged} onNotice={setNotice} onBusy={setBusy} expanded onReviewed={() => { setReviewed(identity); dialog.current?.close(); }} /> : null}
        </div>
      </div>
    </dialog>
  </>;
}
