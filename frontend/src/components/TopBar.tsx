"use client";

import Link from "next/link";

import { ProjectSwitcher } from "@/components/ProjectSwitcher";
import { RuntimeOpsPanel } from "@/components/RuntimeOpsPanel";
import { type Readiness } from "@/lib/api";
import { useRuntimeSnapshot } from "@/lib/dashboard";
import { useI18n } from "@/lib/i18n";
import { useProject } from "@/lib/project";

const REFRESH_MS = 3000;

export function TopBar(): JSX.Element {
  const { t, lang, toggle } = useI18n();
  const { selectedProject } = useProject();
  const { stats, readiness } = useRuntimeSnapshot(selectedProject, REFRESH_MS);

  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-mars-border bg-mars-panel/90 px-4 py-2.5 backdrop-blur">
      <div className="flex items-center gap-3">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-base font-semibold tracking-tight">{t("app.title")}</span>
          <span className="rounded bg-mars-subtle px-1.5 py-0.5 text-[10px] text-slate-400">
            {t("app.version")}
          </span>
        </Link>
      </div>

      <div className="order-3 flex w-full flex-wrap items-center gap-1.5 lg:order-none lg:w-auto lg:flex-1 lg:justify-center">
        <Stat label={t("stat.agents")} value={stats?.agents_registered ?? 0} />
        <ReadinessBadge readiness={readiness} />
        <Stat
          label={t("stat.running")}
          value={stats?.runs_running ?? 0}
          tone="info"
        />
        <WaitingStat
          label={t("stat.waiting")}
          value={stats?.runs_waiting_review ?? 0}
          firstWaiting={stats?.waiting_review_runs?.[0]?.run_id}
        />
        <Stat
          label={t("stat.failed")}
          value={stats?.runs_failed ?? 0}
          tone={stats && stats.runs_failed > 0 ? "danger" : "muted"}
        />
        <Stat
          label={t("stat.artifacts")}
          value={stats?.artifacts_total ?? 0}
        />
        <Stat label={t("stat.kb")} value={stats?.kb_total ?? 0} />
      </div>

      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        <ProjectSwitcher />
        <Link
          href="/entries"
          className="rounded border border-mars-border bg-mars-panel2 px-2.5 py-1 text-xs text-slate-200 hover:bg-mars-subtle hover:text-white"
          title={t("topbar.entries")}
        >
          {t("topbar.entries")}
        </Link>
        <Link
          href="/runs"
          className="rounded border border-mars-border bg-mars-panel2 px-2.5 py-1 text-xs text-slate-200 hover:bg-mars-subtle hover:text-white"
          title={t("common.run_id") + " · all"}
        >
          运行记录
        </Link>
        <Link
          href="/context"
          className="rounded border border-mars-border bg-mars-panel2 px-2.5 py-1 text-xs text-slate-200 hover:bg-mars-subtle hover:text-white"
          title="上下文工作台"
        >
          上下文
        </Link>
        <RuntimeOpsPanel project={selectedProject} />
        <button
          onClick={toggle}
          className="rounded border border-mars-border bg-mars-panel2 px-2.5 py-1 text-xs hover:bg-mars-subtle"
          title={lang === "zh" ? "Switch to English" : "切换到中文"}
        >
          {t("lang.toggle")}
        </button>
      </div>
    </header>
  );
}

function ReadinessBadge({
  readiness,
}: {
  readiness: Readiness | null;
}): JSX.Element {
  const blockers =
    readiness?.checks.filter((c) => c.severity === "blocker" && !c.ready) ?? [];
  const ready = readiness?.ready ?? false;
  const label = readiness
    ? `${readiness.runtime_mode}/${readiness.execution_backend}`
    : "检查中";
  const cls = ready
    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
    : "border-amber-500/30 bg-amber-500/10 text-amber-200";
  return (
    <div
      className={`flex items-center gap-1.5 rounded border px-2 py-1 text-xs ${cls}`}
      title={blockers.map((c) => c.message).join("\n") || "运行态已就绪"}
    >
      <span>{ready ? "✓" : "!"}</span>
      <span className="font-mono">{label}</span>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "muted",
}: {
  label: string;
  value: number;
  tone?: "muted" | "info" | "danger";
}): JSX.Element {
  const toneClass =
    tone === "danger"
      ? "text-red-300"
      : tone === "info"
        ? "text-emerald-300"
        : "text-slate-200";
  return (
    <div className="flex items-center gap-1.5 rounded bg-mars-bg/60 px-2 py-1 text-xs">
      <span className="text-slate-400">{label}</span>
      <span className={`font-mono font-semibold tabular-nums ${toneClass}`}>{value}</span>
    </div>
  );
}

function WaitingStat({
  label,
  value,
  firstWaiting,
}: {
  label: string;
  value: number;
  firstWaiting?: string;
}): JSX.Element {
  const active = value > 0;
  const content = (
    <>
      <span className={active ? "text-fuchsia-200" : "text-slate-400"}>
        {label}
      </span>
      <span
        className={`font-mono font-semibold tabular-nums ${
          active ? "text-fuchsia-200" : "text-slate-400"
        }`}
      >
        {value}
      </span>
    </>
  );
  const cls = `flex items-center gap-1.5 rounded px-2 py-1 text-xs ${
    active
      ? "border border-fuchsia-500/40 bg-fuchsia-500/15"
      : "bg-mars-bg/60"
  }`;
  if (active && firstWaiting) {
    return (
      <Link href={`/runs/${firstWaiting}`} className={cls} title="跳到待审核 run">
        {content}
      </Link>
    );
  }
  return <div className={cls}>{content}</div>;
}
