"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import {
  createRun,
  getStats,
  listProjects,
  listRuns,
  startRun,
  type ProjectSummary,
  type RunSummary,
  type Stats,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Tab = "lab_pipeline" | "lab_standalone" | "paper_repro";

const TAB_TO_ENTRY: Record<Tab, string> = {
  lab_pipeline: "pipeline",
  lab_standalone: "idea",       // Standalone defaults to Idea entry; user can change in form
  paper_repro: "coding",        // Paper repro hands the agent a repo + plan; we model it as Coding entry
};

export function ProjectsPanel({ onSelectRun }: { onSelectRun?: (runId: string) => void }): JSX.Element {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>("lab_pipeline");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [research, setResearch] = useState("");
  const [tags, setTags] = useState("");
  const [project, setProject] = useState("moe-pimc");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const [p, r, s] = await Promise.all([listProjects(), listRuns(), getStats()]);
        if (alive) {
          setProjects(p);
          setRuns(r.reverse());
          setStats(s);
        }
      } catch {
        /* ignore */
      }
    };
    void refresh();
    const iv = setInterval(refresh, 4000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  // Map run_id -> waiting agent name for visual highlight on RunCards.
  const waitingByRun = new Map<string, string>(
    (stats?.waiting_review_runs ?? []).map((w) => [w.run_id, w.agent]),
  );

  async function submit(): Promise<void> {
    if (!research.trim()) {
      setErr("研究问题不能为空 / Research question is required");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const taskSlug = research
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_一-龥]+/g, "_")
        .slice(0, 60);
      const detail = await createRun({
        task: taskSlug || "lab_run",
        project,
        entrypoint: TAB_TO_ENTRY[tab],
        user_request: research + (tags ? `\n\n[topics] ${tags}` : ""),
      });
      await startRun(detail.run_id);
      setResearch("");
      onSelectRun?.(detail.run_id);
      // refresh list
      setRuns((r) => [
        { ...detail } as RunSummary,
        ...r,
      ]);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="flex h-full flex-col gap-3 border-r border-mars-border bg-mars-panel/60 p-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">
          📋 {t("sidebar.projects")}
        </h2>
        <span className="text-[10px] text-slate-500">
          {runs.length} · {runs.length} {t("sidebar.running")}
        </span>
      </div>

      <div className="flex gap-1 rounded bg-mars-bg/60 p-1 text-[11px]">
        {(["lab_pipeline", "lab_standalone", "paper_repro"] as const).map((tk) => (
          <button
            key={tk}
            onClick={() => setTab(tk)}
            className={`flex-1 rounded px-2 py-1 transition ${
              tab === tk
                ? "bg-mars-accent/30 text-white"
                : "text-slate-400 hover:bg-mars-subtle"
            }`}
          >
            {t(`tab.${tk}`)}
          </button>
        ))}
      </div>

      <div className="space-y-2 rounded border border-mars-border bg-mars-bg/40 p-2">
        <Field label={t("sidebar.input.research")}>
          <textarea
            value={research}
            onChange={(e) => setResearch(e.target.value)}
            rows={3}
            placeholder={t("sidebar.input.research_placeholder")}
            className="input resize-none"
          />
        </Field>
        <Field label={t("sidebar.input.tags")}>
          <input
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder={t("sidebar.input.tags_placeholder")}
            className="input"
          />
        </Field>
        <details className="group">
          <summary className="cursor-pointer select-none text-[11px] text-slate-400 hover:text-slate-200">
            ▸ {t("sidebar.input.config")}
          </summary>
          <div className="mt-2 space-y-2">
            <Field label={t("sidebar.input.project")}>
              <select
                value={project}
                onChange={(e) => setProject(e.target.value)}
                className="input"
              >
                {(projects.length ? projects : [{ name: "moe-pimc" } as ProjectSummary]).map(
                  (p) => (
                    <option key={p.name} value={p.name}>
                      {p.name}
                    </option>
                  ),
                )}
              </select>
            </Field>
            <Field label={t("sidebar.input.entrypoint")}>
              <input
                value={TAB_TO_ENTRY[tab]}
                disabled
                className="input opacity-60"
              />
            </Field>
          </div>
        </details>
        {err ? (
          <p className="rounded bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</p>
        ) : null}
        <button
          disabled={busy}
          onClick={submit}
          className="w-full rounded bg-mars-accent py-1.5 text-xs font-medium text-white hover:bg-mars-accent2 disabled:opacity-50"
        >
          {busy ? t("common.loading") : t("sidebar.submit")}
        </button>
      </div>

      <div className="flex-1 space-y-1.5 overflow-auto pr-1">
        {runs.length === 0 ? (
          <p className="rounded border border-dashed border-mars-border p-3 text-center text-[11px] text-slate-500">
            {t("sidebar.no_runs")}
          </p>
        ) : (
          runs.map((r) => (
            <RunCard
              key={r.run_id}
              run={r}
              onSelect={onSelectRun}
              waitingAgent={waitingByRun.get(r.run_id)}
            />
          ))
        )}
      </div>

      <style jsx>{`
        .input {
          width: 100%;
          padding: 0.4rem 0.55rem;
          border-radius: 0.3rem;
          background: #0b0d12;
          border: 1px solid #23262d;
          color: #e2e8f0;
          font-size: 0.78rem;
        }
        .input:focus {
          outline: 1px solid #6366f1;
        }
      `}</style>
    </aside>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <label className="block">
      <span className="mb-0.5 block text-[10px] uppercase tracking-wider text-slate-500">
        {label}
      </span>
      {children}
    </label>
  );
}

function RunCard({
  run,
  onSelect,
  waitingAgent,
}: {
  run: RunSummary;
  onSelect?: (runId: string) => void;
  waitingAgent?: string;
}): JSX.Element {
  const { t } = useI18n();
  const isWaiting = !!waitingAgent;
  return (
    <div
      onClick={() => onSelect?.(run.run_id)}
      className={`group cursor-pointer rounded border p-2 transition ${
        isWaiting
          ? "border-fuchsia-500/50 bg-fuchsia-500/10 hover:border-fuchsia-400"
          : "border-mars-border bg-mars-panel2 hover:border-mars-accent"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-slate-200">▶ {run.task}</span>
        {isWaiting ? (
          <span className="animate-pulse rounded bg-fuchsia-500/30 px-1.5 py-0.5 text-[9px] text-fuchsia-100">
            🔔 {t("state.waiting_review")}
          </span>
        ) : (
          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] text-emerald-300">
            {t("state.running")}
          </span>
        )}
      </div>
      <p className="mt-1 truncate text-[10px] text-slate-500">
        {run.run_id} · {run.project}
      </p>
      <div className="mt-1.5 flex gap-1 text-[10px]">
        <Link
          href={`/runs/${run.run_id}`}
          className="rounded bg-amber-500/20 px-2 py-0.5 text-amber-300 hover:bg-amber-500/30"
          onClick={(e) => e.stopPropagation()}
        >
          ⏸ {t("run.pause")}
        </Link>
        <Link
          href={`/runs/${run.run_id}`}
          className="rounded bg-cyan-500/20 px-2 py-0.5 text-cyan-300 hover:bg-cyan-500/30"
          onClick={(e) => e.stopPropagation()}
        >
          ↻ {t("run.resume")}
        </Link>
        <Link
          href={`/runs/${run.run_id}`}
          className="rounded bg-rose-500/20 px-2 py-0.5 text-rose-300 hover:bg-rose-500/30"
          onClick={(e) => e.stopPropagation()}
        >
          🗑 {t("run.delete")}
        </Link>
      </div>
    </div>
  );
}
