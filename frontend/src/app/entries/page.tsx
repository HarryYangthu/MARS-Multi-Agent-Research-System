"use client";

import Link from "next/link";

import { TopBar } from "@/components/TopBar";
import { useI18n } from "@/lib/i18n";

const CARDS = [
  {
    href: "/runs/new?entrypoint=pipeline",
    titleKey: "entries.card.pipeline.title",
    blurbKey: "entries.card.pipeline.blurb",
    icon: "🚀",
    accent: "from-indigo-500/30",
  },
  {
    href: "/runs/new?entrypoint=idea",
    titleKey: "agent.idea",
    blurbKey: "entries.card.idea.blurb",
    icon: "💡",
    accent: "from-amber-500/30",
  },
  {
    href: "/runs/new?entrypoint=experiment",
    titleKey: "agent.experiment",
    blurbKey: "entries.card.experiment.blurb",
    icon: "🧪",
    accent: "from-orange-500/30",
  },
  {
    href: "/runs/new?entrypoint=coding",
    titleKey: "agent.coding",
    blurbKey: "entries.card.coding.blurb",
    icon: "🛠",
    accent: "from-emerald-500/30",
  },
  {
    href: "/runs/new?entrypoint=execution",
    titleKey: "agent.execution",
    blurbKey: "entries.card.execution.blurb",
    icon: "⚡",
    accent: "from-rose-500/30",
  },
  {
    href: "/runs/new?entrypoint=writing",
    titleKey: "agent.writing",
    blurbKey: "entries.card.writing.blurb",
    icon: "✍️",
    accent: "from-violet-500/30",
  },
] as const;

export default function EntriesPage(): JSX.Element {
  const { t } = useI18n();
  return (
    <div className="grid h-screen grid-rows-[auto_1fr] bg-mars-bg">
      <TopBar />
      <main className="container mx-auto max-w-6xl px-6 py-10">
        <header className="mb-8 flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-100">
              {t("entries.title")}
            </h1>
            <p className="mt-1 text-sm text-slate-400">{t("entries.subtitle")}</p>
          </div>
          <Link
            href="/"
            className="rounded border border-mars-border bg-mars-panel px-3 py-1.5 text-xs text-slate-300 hover:bg-mars-subtle"
          >
            {t("entries.back")}
          </Link>
        </header>

        <section className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {CARDS.map((c) => (
            <Link
              key={c.href}
              href={c.href}
              className="group block rounded-lg border border-mars-border bg-mars-panel p-5 transition hover:border-mars-accent hover:bg-mars-panel2"
            >
              <div
                className={`-m-5 mb-4 h-2 rounded-t-lg bg-gradient-to-r ${c.accent} to-transparent`}
              />
              <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
                <span>{c.icon}</span>
                <span>{t(c.titleKey)}</span>
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">
                {t(c.blurbKey)}
              </p>
              <span className="mt-4 inline-block text-xs text-mars-accent group-hover:translate-x-0.5">
                {t("entries.start")} &rarr;
              </span>
            </Link>
          ))}
        </section>
      </main>
    </div>
  );
}
