import type { DiscoveryHypothesis } from "./types";

interface HypothesisReviewPanelProps {
  hypothesis: DiscoveryHypothesis | null;
  isFinalist: boolean;
  actor: string;
  reason: string;
  statement: string;
  busyAction: string;
  message: string;
  onActorChange: (value: string) => void;
  onReasonChange: (value: string) => void;
  onStatementChange: (value: string) => void;
  onEdit: () => void;
  onReject: () => void;
  onSelect: () => void;
}

export function HypothesisReviewPanel({
  hypothesis,
  isFinalist,
  actor,
  reason,
  statement,
  busyAction,
  message,
  onActorChange,
  onReasonChange,
  onStatementChange,
  onEdit,
  onReject,
  onSelect,
}: HypothesisReviewPanelProps): JSX.Element {
  if (!hypothesis) {
    return <aside className="rounded-2xl border border-mars-border bg-mars-panel p-5 text-sm text-slate-500">Select a hypothesis to review.</aside>;
  }
  const disabled = Boolean(busyAction) || hypothesis.blocked;
  return (
    <aside className="sticky top-5 rounded-2xl border border-mars-border bg-mars-panel p-5 shadow-xl shadow-black/20">
      <div className="flex items-start justify-between gap-3">
        <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-300">HITL review</p><h2 className="mt-2 text-base font-semibold">{hypothesis.mechanism}</h2></div>
        <span className={`rounded-full px-2.5 py-1 text-[11px] ${hypothesis.blocked ? "bg-rose-500/15 text-rose-300" : isFinalist ? "bg-emerald-500/15 text-emerald-300" : "bg-slate-500/15 text-slate-400"}`}>{hypothesis.blocked ? "Blocked" : isFinalist ? "Top-K" : "Pool"}</span>
      </div>
      <div className="mt-5 space-y-4">
        <Field label="Hypothesis statement"><textarea className={`${inputClass()} min-h-36 resize-y`} value={statement} disabled={disabled} onChange={(event) => onStatementChange(event.target.value)} /></Field>
        <Field label="Reviewer"><input className={inputClass()} value={actor} disabled={Boolean(busyAction)} onChange={(event) => onActorChange(event.target.value)} /></Field>
        <Field label="Decision reason"><textarea className={`${inputClass()} min-h-20 resize-y`} value={reason} disabled={Boolean(busyAction)} onChange={(event) => onReasonChange(event.target.value)} placeholder="Required for a durable review decision." /></Field>
      </div>
      {hypothesis.testable_predictions?.length ? <div className="mt-4 rounded-xl border border-mars-border bg-mars-bg/55 p-3"><p className="text-xs font-semibold text-slate-300">Testable predictions</p><ul className="mt-2 space-y-1 text-xs leading-5 text-slate-500">{hypothesis.testable_predictions.map((item) => <li key={item}>• {item}</li>)}</ul></div> : null}
      {message ? <p role="status" className="mt-4 rounded-lg bg-mars-bg px-3 py-2 text-xs text-slate-300">{message}</p> : null}
      <div className="mt-5 grid grid-cols-2 gap-2">
        <button type="button" disabled={disabled || !statement.trim() || !actor.trim() || !reason.trim()} onClick={onEdit} className="rounded-lg border border-mars-border px-3 py-2 text-xs font-medium text-slate-200 transition hover:border-indigo-400 disabled:cursor-not-allowed disabled:opacity-40">{busyAction === "edit" ? "Saving…" : "Save edit"}</button>
        <button type="button" disabled={disabled || !actor.trim() || !reason.trim()} onClick={onReject} className="rounded-lg border border-rose-500/40 px-3 py-2 text-xs font-medium text-rose-200 transition hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-40">{busyAction === "reject" ? "Rejecting…" : "Reject"}</button>
        <button type="button" disabled={disabled || !isFinalist || !actor.trim() || !reason.trim()} onClick={onSelect} className="col-span-2 rounded-lg bg-emerald-500 px-3 py-2.5 text-xs font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40">{busyAction === "select" ? "Selecting…" : "Select hypothesis → proposal.v1"}</button>
      </div>
      {!isFinalist && !hypothesis.blocked ? <p className="mt-3 text-[11px] leading-5 text-slate-500">Only legal Top-K candidates can be selected. You may still edit or reject this pool item.</p> : null}
    </aside>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return <label className="block space-y-2"><span className="text-xs font-medium text-slate-300">{label}</span>{children}</label>;
}

function inputClass(): string {
  return "w-full rounded-lg border border-mars-border bg-mars-bg/75 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-50";
}
