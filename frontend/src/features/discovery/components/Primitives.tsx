import type { ReactNode } from "react";

const TONES: Record<string, string> = {
  running: "border-cyan-400/30 bg-cyan-400/10 text-cyan-200",
  completed: "border-emerald-400/30 bg-emerald-400/10 text-emerald-200",
  elite: "border-violet-400/30 bg-violet-400/10 text-violet-200",
  promoted: "border-violet-400/30 bg-violet-400/10 text-violet-200",
  passed: "border-emerald-400/30 bg-emerald-400/10 text-emerald-200",
  paused: "border-amber-400/30 bg-amber-400/10 text-amber-200",
  waiting_hitl: "border-amber-400/30 bg-amber-400/10 text-amber-200",
  blocked: "border-rose-400/30 bg-rose-400/10 text-rose-200",
  quarantined: "border-rose-400/30 bg-rose-400/10 text-rose-200",
  failed: "border-rose-400/30 bg-rose-400/10 text-rose-200",
  stopped: "border-slate-400/30 bg-slate-400/10 text-slate-300",
  pending: "border-slate-400/20 bg-slate-400/5 text-slate-400",
};

export function Badge({ value, label }: { value: string; label?: string }): JSX.Element {
  const tone = TONES[value] ?? "border-slate-400/20 bg-slate-400/5 text-slate-300";
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] ${tone}`}
    >
      {label ?? value.replaceAll("_", " ")}
    </span>
  );
}
export function Panel({
  title,
  eyebrow,
  action,
  children,
  className = "",
}: {
  title: string;
  eyebrow?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <section className={`rounded-2xl border border-white/10 bg-[#11151d] ${className}`}>
      <header className="flex items-start justify-between gap-4 border-b border-white/8 px-5 py-4">
        <div>
          {eyebrow ? (
            <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-300/70">
              {eyebrow}
            </p>
          ) : null}
          <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
        </div>
        {action}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}

export function EmptyState({ title, detail }: { title: string; detail: string }): JSX.Element {
  return (
    <div className="rounded-xl border border-dashed border-white/10 px-5 py-10 text-center">
      <p className="text-sm font-medium text-slate-200">{title}</p>
      <p className="mx-auto mt-2 max-w-lg text-xs leading-5 text-slate-500">{detail}</p>
    </div>
  );
}

export function KeyValue({ label, value }: { label: string; value: ReactNode }): JSX.Element {
  return (
    <div className="min-w-0 rounded-xl border border-white/8 bg-white/[0.025] px-4 py-3">
      <dt className="text-[11px] uppercase tracking-[0.12em] text-slate-500">{label}</dt>
      <dd className="mt-1 truncate text-sm text-slate-200">{value}</dd>
    </div>
  );
}
