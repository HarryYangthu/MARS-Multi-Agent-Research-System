"use client";

import { useEffect, useMemo, useState } from "react";

import {
  listEvents,
  STAGE_TO_TIER,
  type EventEntry,
  type Stage,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Filter = "all" | "l1" | "l2" | "l3" | "l4" | "l5";

const TIER_TINT: Record<number, string> = {
  1: "text-amber-300",
  2: "text-orange-300",
  3: "text-emerald-300",
  4: "text-rose-300",
  5: "text-violet-300",
};

function payloadAgent(p: Record<string, unknown>): Stage | null {
  const a = p?.agent as string | undefined;
  if (a && (a === "idea" || a === "experiment" || a === "coding" || a === "execution" || a === "writing")) {
    return a as Stage;
  }
  return null;
}

function shortTime(ts: string): string {
  if (!ts) return "";
  const m = /T(\d{2}):(\d{2}):(\d{2})/.exec(ts);
  return m ? `${m[1]}:${m[2]}:${m[3]}` : ts.slice(11, 19);
}

export function EventLog(): JSX.Element {
  const { t } = useI18n();
  const [events, setEvents] = useState<EventEntry[]>([]);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    let alive = true;
    const refresh = () => {
      void listEvents(120)
        .then((es) => alive && setEvents(es))
        .catch(() => {});
    };
    refresh();
    const iv = setInterval(refresh, 2000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  const filtered = useMemo(() => {
    if (filter === "all") return events;
    const tier = Number(filter.slice(1)) as 1 | 2 | 3 | 4 | 5;
    return events.filter((e) => {
      const stage = payloadAgent(e.payload);
      return stage ? STAGE_TO_TIER[stage] === tier : false;
    });
  }, [events, filter]);

  return (
    <section className="flex h-full min-h-0 flex-col gap-2 border-b border-mars-border p-3">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">📝 {t("events.title")} <span className="ml-1 text-[10px] text-slate-500">{events.length}</span></h2>
      </header>
      <div className="flex flex-wrap gap-1 text-[10px]">
        {(["all", "l1", "l2", "l3", "l4", "l5"] as Filter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded px-1.5 py-0.5 ${
              filter === f
                ? "bg-mars-accent/30 text-white"
                : "bg-mars-subtle text-slate-400 hover:bg-mars-border"
            }`}
          >
            {t(`events.filter.${f}`)}
          </button>
        ))}
      </div>
      <ol className="flex-1 space-y-1 overflow-auto pr-1 text-[11px]">
        {filtered.length === 0 ? (
          <li className="text-center text-slate-500">{t("events.empty")}</li>
        ) : (
          filtered.map((e, i) => {
            const stage = payloadAgent(e.payload);
            const tier = stage ? STAGE_TO_TIER[stage] : null;
            const ts = shortTime(e.timestamp);
            const fromState = (e.payload?.from_state as string) ?? "";
            const toState = (e.payload?.to_state as string) ?? "";
            const event = (e.payload?.event as string) ?? "";
            const summary = toState
              ? `${fromState || "?"} → ${toState}`
              : event || JSON.stringify(e.payload).slice(0, 80);
            return (
              <li
                key={`${e.run_id}-${i}-${e.timestamp}`}
                className="rounded border border-mars-border/50 bg-mars-bg/40 px-2 py-1"
              >
                <div className="flex items-baseline gap-1.5">
                  <span className="font-mono text-[10px] text-slate-500">{ts}</span>
                  {tier ? (
                    <span className={`font-mono text-[10px] ${TIER_TINT[tier]}`}>
                      [L{tier}]
                    </span>
                  ) : null}
                  {stage ? (
                    <span className="font-mono text-[10px] text-slate-400">{stage}</span>
                  ) : null}
                </div>
                <p className="truncate text-slate-300">{summary}</p>
                <p className="truncate font-mono text-[9px] text-slate-600">{e.run_id}</p>
              </li>
            );
          })
        )}
      </ol>
    </section>
  );
}
