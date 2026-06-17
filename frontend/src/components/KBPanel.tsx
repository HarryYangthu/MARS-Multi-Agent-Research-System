"use client";

import { type FormEvent, useEffect, useState } from "react";

import {
  listQuarantineItems,
  listZoneItems,
  listZones,
  searchKnowledge,
  searchQuarantine,
  type KBItem,
  type KnowledgeMemoryType,
  type KnowledgeProfile,
  type KnowledgeSearchHit,
  type ZoneSummary,
} from "@/lib/api";
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

const MEMORY_TYPES: { value: KnowledgeMemoryType | ""; labelKey: string }[] = [
  { value: "", labelKey: "kb.search.allTypes" },
  { value: "semantic", labelKey: "kb.type.semantic" },
  { value: "episodic", labelKey: "kb.type.episodic" },
  { value: "procedural", labelKey: "kb.type.procedural" },
];

const PROFILES: KnowledgeProfile[] = ["dev_e2e", "research", "hardware"];

export function KBPanel(): JSX.Element {
  const { t } = useI18n();
  const [zones, setZones] = useState<ZoneSummary[]>([]);
  const [openZone, setOpenZone] = useState<string | null>(null);
  const [items, setItems] = useState<KBItem[]>([]);
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [selectedZone, setSelectedZone] = useState("");
  const [memoryType, setMemoryType] = useState<KnowledgeMemoryType | "">("");
  const [profile, setProfile] = useState<KnowledgeProfile>("dev_e2e");
  const [includeMock, setIncludeMock] = useState(false);
  const [includeSuperseded, setIncludeSuperseded] = useState(false);
  const [showQuarantine, setShowQuarantine] = useState(false);
  const [quarantineItems, setQuarantineItems] = useState<KBItem[]>([]);
  const [searchHits, setSearchHits] = useState<KnowledgeSearchHit[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    if (!openZone || showQuarantine) {
      setItems([]);
      return;
    }
    let alive = true;
    void listZoneItems(openZone, 8)
      .then((it) => alive && setItems(it))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [openZone, showQuarantine]);

  useEffect(() => {
    if (!showQuarantine) {
      setQuarantineItems([]);
      return;
    }
    let alive = true;
    void listQuarantineItems({
      limit: 12,
      memoryType: memoryType || undefined,
      includeMock: true,
      includeSuperseded: true,
    })
      .then((it) => alive && setQuarantineItems(it))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [memoryType, showQuarantine]);

  const runSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = query.trim();
    setSubmittedQuery(trimmed);
    setError(null);
    if (!trimmed) {
      setSearchHits([]);
      return;
    }
    setIsSearching(true);
    try {
      const hits = showQuarantine
        ? await searchQuarantine({
            q: trimmed,
            topK: 10,
            memoryType: memoryType || undefined,
            includeMock: true,
            includeSuperseded: true,
          })
        : await searchKnowledge({
            q: trimmed,
            topK: 10,
            zone: selectedZone || undefined,
            memoryType: memoryType || undefined,
            includeMock,
            includeSuperseded,
            profile,
          });
      setSearchHits(hits);
    } catch (err) {
      const message = err instanceof Error ? err.message : t("common.error");
      setError(message);
      setSearchHits([]);
    } finally {
      setIsSearching(false);
    }
  };

  return (
    <section className="flex flex-col gap-2 p-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">📚 {t("kb.title")}</h2>
        <button
          type="button"
          onClick={() => {
            setShowQuarantine((value) => !value);
            setSubmittedQuery("");
            setSearchHits([]);
          }}
          className={`rounded border px-2 py-1 text-[10px] font-medium ${
            showQuarantine
              ? "border-amber-500/50 bg-amber-500/10 text-amber-200"
              : "border-mars-border text-slate-300 hover:bg-mars-bg"
          }`}
        >
          {showQuarantine ? t("kb.main") : t("kb.quarantine")}
        </button>
      </div>

      <form className="space-y-1.5" onSubmit={runSearch}>
        <div className="flex gap-1">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("kb.search.placeholder")}
            className="min-w-0 flex-1 rounded border border-mars-border bg-mars-bg px-2 py-1.5 text-xs text-slate-100 outline-none placeholder:text-slate-600 focus:border-mars-accent"
          />
          <button
            type="submit"
            disabled={isSearching}
            className="rounded border border-mars-border px-2 py-1.5 text-[10px] font-medium text-slate-200 hover:bg-mars-bg disabled:opacity-50"
          >
            {t("kb.search")}
          </button>
        </div>
        <div className="grid grid-cols-2 gap-1">
          <select
            value={selectedZone}
            disabled={showQuarantine}
            onChange={(event) => setSelectedZone(event.target.value)}
            className="rounded border border-mars-border bg-mars-bg px-1.5 py-1 text-[10px] text-slate-200 outline-none disabled:opacity-50"
          >
            <option value="">{t("kb.search.allZones")}</option>
            {zones.map((z) => (
              <option key={z.name} value={z.name}>
                {t(ZONE_KEY[z.name] ?? z.name)}
              </option>
            ))}
          </select>
          <select
            value={memoryType}
            onChange={(event) => setMemoryType(event.target.value as KnowledgeMemoryType | "")}
            className="rounded border border-mars-border bg-mars-bg px-1.5 py-1 text-[10px] text-slate-200 outline-none"
          >
            {MEMORY_TYPES.map((option) => (
              <option key={option.value || "all"} value={option.value}>
                {t(option.labelKey)}
              </option>
            ))}
          </select>
        </div>
        {!showQuarantine ? (
          <div className="grid grid-cols-[1fr_auto_auto] gap-1">
            <select
              value={profile}
              onChange={(event) => setProfile(event.target.value as KnowledgeProfile)}
              className="rounded border border-mars-border bg-mars-bg px-1.5 py-1 text-[10px] text-slate-200 outline-none"
            >
              {PROFILES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1 rounded border border-mars-border px-1.5 py-1 text-[10px] text-slate-300">
              <input
                type="checkbox"
                checked={includeMock}
                onChange={(event) => setIncludeMock(event.target.checked)}
              />
              {t("kb.includeMock")}
            </label>
            <label className="flex items-center gap-1 rounded border border-mars-border px-1.5 py-1 text-[10px] text-slate-300">
              <input
                type="checkbox"
                checked={includeSuperseded}
                onChange={(event) => setIncludeSuperseded(event.target.checked)}
              />
              {t("kb.includeSuperseded")}
            </label>
          </div>
        ) : null}
      </form>

      {error ? <p className="text-[10px] text-red-300">{error}</p> : null}

      {submittedQuery ? (
        <ul className="space-y-1 text-[10px]">
          {searchHits.length === 0 ? (
            <li className="italic text-slate-500">{t("kb.empty")}</li>
          ) : (
            searchHits.map((hit) => renderMemoryItem(hit.item, hit.score))
          )}
        </ul>
      ) : showQuarantine ? (
        <ul className="space-y-1 text-[10px]">
          {quarantineItems.length === 0 ? (
            <li className="italic text-slate-500">{t("kb.empty")}</li>
          ) : (
            quarantineItems.map((it) => renderMemoryItem(it))
          )}
        </ul>
      ) : (
        <ul className="space-y-1.5">
          {zones.map((z) => {
            const isOpen = openZone === z.name;
            return (
              <li key={z.name}>
                <button
                  onClick={() => {
                    setOpenZone(isOpen ? null : z.name);
                    setSelectedZone(z.name);
                  }}
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
                      items.slice(0, 8).map((it) => renderMemoryItem(it))
                    )}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function renderMemoryItem(item: KBItem, score?: number): JSX.Element {
  const meta = itemMeta(item);
  const decision = evalDecision(item);
  const supersededBy = metadataString(item, "superseded_by");
  return (
    <li key={`${item.zone}-${item.id}`} className="rounded bg-mars-bg/60 px-2 py-1 text-slate-400">
      <p className="flex items-center gap-1 font-mono text-[9px] text-slate-500">
        <span className="truncate">
          {score !== undefined ? `${score.toFixed(2)} · ` : ""}
          {item.id}
          {meta ? ` · ${meta}` : ""}
        </span>
      </p>
      <p className="mt-0.5 truncate">{item.text_excerpt}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        {item.metadata.is_mock === true ? <Badge tone="amber" label="mock" /> : null}
        {supersededBy ? <Badge tone="slate" label="old" /> : null}
        {decision ? <Badge tone={decision === "pass" ? "green" : "rose"} label={decision} /> : null}
      </div>
    </li>
  );
}

function Badge({ label, tone }: { label: string; tone: "amber" | "green" | "rose" | "slate" }) {
  const cls =
    tone === "amber"
      ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
      : tone === "green"
        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
        : tone === "rose"
          ? "border-rose-500/40 bg-rose-500/10 text-rose-200"
          : "border-slate-500/40 bg-slate-500/10 text-slate-300";
  return <span className={`rounded border px-1 py-0.5 text-[9px] ${cls}`}>{label}</span>;
}

function itemMeta(item: KBItem): string {
  return [
    metadataString(item, "memory_type") || metadataString(item, "kind"),
    metadataString(item, "agent"),
    metadataString(item, "schema"),
  ]
    .filter(Boolean)
    .join(" · ");
}

function evalDecision(item: KBItem): string {
  const raw = item.metadata.eval_status;
  if (!isRecord(raw)) {
    return "";
  }
  const decision = raw.decision;
  return typeof decision === "string" ? decision : "";
}

function metadataString(item: KBItem, key: string): string {
  const value = item.metadata[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
