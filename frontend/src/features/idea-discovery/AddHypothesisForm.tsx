"use client";

import { useState } from "react";

import type {
  HypothesisCreateAuditRecord,
  HypothesisCreateInput,
} from "./types";

type SubmitState = "idle" | "pending" | "success" | "error";

export function AddHypothesisForm({
  defaultActor,
  onCancel,
  onSubmit,
}: {
  defaultActor: string;
  onCancel: () => void;
  onSubmit: (input: HypothesisCreateInput) => Promise<HypothesisCreateAuditRecord>;
}): JSX.Element {
  const [actor, setActor] = useState(defaultActor);
  const [reason, setReason] = useState("");
  const [statement, setStatement] = useState("");
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [message, setMessage] = useState("");

  const valid = Boolean(actor.trim() && reason.trim() && statement.trim());

  const submit = async (): Promise<void> => {
    if (!valid || submitState === "pending") return;
    setSubmitState("pending");
    setMessage("");
    try {
      const audit = await onSubmit({
        actor: actor.trim(),
        reason: reason.trim(),
        statement: statement.trim(),
      });
      const auditRef = typeof audit.audit_ref === "string" ? audit.audit_ref : "";
      setSubmitState("success");
      setMessage(
        auditRef
          ? `Hypothesis recorded and REST snapshot refreshed · ${auditRef}`
          : "Hypothesis recorded and REST snapshot refreshed.",
      );
      setReason("");
      setStatement("");
    } catch (caught: unknown) {
      setSubmitState("error");
      setMessage(caught instanceof Error ? caught.message : String(caught));
    }
  };

  return (
    <div className="mb-4 rounded-xl border border-indigo-400/25 bg-indigo-500/[0.06] p-4">
      <div className="grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)]">
        <label>
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Actor
          </span>
          <input
            className="mt-1.5 w-full rounded-lg border border-mars-border bg-mars-bg px-3 py-2 text-xs text-slate-200 outline-none focus:border-indigo-400"
            disabled={submitState === "pending"}
            onChange={(event) => {
              setActor(event.target.value);
              setSubmitState("idle");
            }}
            value={actor}
          />
        </label>
        <label>
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Reason
          </span>
          <input
            className="mt-1.5 w-full rounded-lg border border-mars-border bg-mars-bg px-3 py-2 text-xs text-slate-200 outline-none focus:border-indigo-400"
            disabled={submitState === "pending"}
            onChange={(event) => {
              setReason(event.target.value);
              setSubmitState("idle");
            }}
            placeholder="Why this hypothesis should enter the pool"
            value={reason}
          />
        </label>
      </div>
      <label className="mt-3 block">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          Statement
        </span>
        <textarea
          className="mt-1.5 min-h-24 w-full resize-y rounded-lg border border-mars-border bg-mars-bg px-3 py-2 text-xs leading-5 text-slate-200 outline-none focus:border-indigo-400"
          disabled={submitState === "pending"}
          onChange={(event) => {
            setStatement(event.target.value);
            setSubmitState("idle");
          }}
          placeholder="Write a testable hypothesis"
          value={statement}
        />
      </label>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div aria-live="polite" className="min-h-5 text-xs">
          {submitState === "pending" ? (
            <span className="text-indigo-200">Submitting and refreshing REST…</span>
          ) : null}
          {submitState === "success" ? (
            <span className="text-emerald-300">{message}</span>
          ) : null}
          {submitState === "error" ? <span className="text-rose-300">{message}</span> : null}
        </div>
        <div className="flex gap-2">
          <button
            className="rounded-lg border border-mars-border px-3 py-2 text-xs text-slate-400 disabled:opacity-40"
            disabled={submitState === "pending"}
            onClick={onCancel}
            type="button"
          >
            Close
          </button>
          <button
            className="rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={!valid || submitState === "pending"}
            onClick={() => void submit()}
            type="button"
          >
            {submitState === "pending" ? "Adding…" : "Add to pool"}
          </button>
        </div>
      </div>
    </div>
  );
}
