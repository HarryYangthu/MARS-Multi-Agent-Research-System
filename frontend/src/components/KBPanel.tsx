"use client";

import { useEffect, useState } from "react";

import { listZoneItems, listZones, type KBItem, type ZoneSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const ZONE_ICON: Record<string, string> = {
  literature: "💡",
  methodology: "📚",
  code_assets: "🧩",
  run_archive: "📊",
};

const ZONE_KEY: Record<string, string> = {
  literature: "kb.literature",
  methodology: "kb.methodology",
  code_assets: "kb.code_assets",
  run_archive: "kb.run_archive",
};

const ZONE_SUBTITLE_KEY: Record<string, string> = {
  literature: "kb.literature.subtitle",
  methodology: "kb.methodology.subtitle",
  code_assets: "kb.code_assets.subtitle",
  run_archive: "kb.run_archive.subtitle",
};

export function KBPanel(): JSX.Element {
  const { t } = useI18n();
  const [zones, setZones] = useState<ZoneSummary[]>([]);
  const [openZone, setOpenZone] = useState<string | null>(null);
  const [items, setItems] = useState<KBItem[]>([]);

  useEffect(() => {
    let alive = true;
    const refresh = () => {
      void listZones()
        .then((z) => alive && setZones(z))
        .catch(() => {});
    };
    refresh();
    const iv = setInterval(refresh, 5000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  useEffect(() => {
    if (!openZone) return;
    let alive = true;
    void listZoneItems(openZone, 8)
      .then((it) => alive && setItems(it))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [openZone]);

  return (
    <section className="flex flex-col gap-2 p-3">
      <h2 className="text-sm font-semibold">📚 {t("kb.title")}</h2>
      <ul className="space-y-1.5">
        {zones.map((z) => {
          const isOpen = openZone === z.name;
          return (
            <li key={z.name}>
              <button
                onClick={() => setOpenZone(isOpen ? null : z.name)}
                className="flex w-full items-center justify-between rounded border border-mars-border bg-mars-bg/40 px-2 py-1.5 text-left text-xs hover:border-mars-accent"
              >
                <div>
                  <span className="font-medium text-slate-200">
                    {ZONE_ICON[z.name]} {t(ZONE_KEY[z.name] ?? z.name)}
                  </span>
                  <p className="mt-0.5 text-[9px] text-slate-500">
                    {t(ZONE_SUBTITLE_KEY[z.name] ?? "")}
                  </p>
                </div>
                <span className="rounded bg-mars-subtle px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                  {z.count}
                </span>
              </button>
              {isOpen ? (
                <ul className="mt-1 space-y-1 pl-4 text-[10px]">
                  {items.length === 0 ? (
                    <li className="italic text-slate-500">{t("kb.empty")}</li>
                  ) : (
                    items.slice(0, 8).map((it) => (
                      <li
                        key={it.id}
                        className="rounded bg-mars-bg/60 px-2 py-1 text-slate-400"
                      >
                        <p className="font-mono text-[9px] text-slate-500">
                          {it.id} · {String(it.metadata?.kind ?? "")}
                        </p>
                        <p className="mt-0.5 truncate">{it.text_excerpt}</p>
                      </li>
                    ))
                  )}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
