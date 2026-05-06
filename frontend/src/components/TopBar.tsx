"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { getStats, type Stats } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const REFRESH_MS = 3000;

export function TopBar(): JSX.Element {
  const { t, lang, toggle } = useI18n();
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    let alive = true;
    void getStats().then((s) => alive && setStats(s)).catch(() => {});
    const iv = setInterval(() => {
      void getStats().then((s) => alive && setStats(s)).catch(() => {});
    }, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  return (
    <header className="flex items-center justify-between border-b border-mars-border bg-mars-panel/80 px-5 py-2.5 backdrop-blur">
      <div className="flex items-center gap-3">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-xl">🚀</span>
          <span className="text-base font-semibold tracking-tight">{t("app.title")}</span>
          <span className="rounded bg-mars-subtle px-1.5 py-0.5 text-[10px] text-slate-400">
            {t("app.version")}
          </span>
        </Link>
      </div>

      <div className="flex items-center gap-1.5">
        <Stat label={t("stat.agents")} value={stats?.agents_registered ?? 0} icon="🦾" />
        <Stat
          label={t("stat.running")}
          value={stats?.runs_running ?? 0}
          icon="⚙️"
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
          icon="⚠️"
          tone={stats && stats.runs_failed > 0 ? "danger" : "muted"}
        />
        <Stat
          label={t("stat.artifacts")}
          value={stats?.artifacts_total ?? 0}
          icon="📦"
        />
        <Stat label={t("stat.kb")} value={stats?.kb_total ?? 0} icon="📚" />
      </div>

      <div className="flex items-center gap-2">
        <Link
          href="/entries"
          className="rounded border border-mars-border bg-mars-panel2 px-2.5 py-1 text-xs text-slate-200 hover:bg-mars-subtle hover:text-white"
          title={t("topbar.entries")}
        >
          🃏 {t("topbar.entries")}
        </Link>
        <Link
          href="/runs"
          className="rounded border border-mars-border bg-mars-panel2 px-2.5 py-1 text-xs text-slate-200 hover:bg-mars-subtle hover:text-white"
          title={t("common.run_id") + " · all"}
        >
          📜 Runs
        </Link>
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

function Stat({
  label,
  value,
  icon,
  tone = "muted",
}: {
  label: string;
  value: number;
  icon: string;
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
      <span>{icon}</span>
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
      <span className={active ? "animate-pulse" : ""}>🔔</span>
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
