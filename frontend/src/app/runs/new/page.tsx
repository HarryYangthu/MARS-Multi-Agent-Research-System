"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { createRun, startRun, STAGE_ORDER } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const VALID_ENTRIES = new Set<string>(["pipeline", ...STAGE_ORDER]);

function NewRunInner(): JSX.Element {
  const { t } = useI18n();
  const router = useRouter();
  const sp = useSearchParams();
  const entryParam = sp.get("entrypoint") ?? "pipeline";
  const entrypoint = VALID_ENTRIES.has(entryParam) ? entryParam : "pipeline";

  const [research, setResearch] = useState("");
  const [project, setProject] = useState("moe-pimc");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const standalone = entrypoint !== "pipeline";

  async function submit(): Promise<void> {
    if (!research.trim()) {
      setErr("研究问题不能为空 / Research question is required");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const taskSlug =
        research
          .trim()
          .toLowerCase()
          .replace(/[^a-z0-9_一-龥]+/g, "_")
          .slice(0, 60) || "lab_run";
      const detail = await createRun({
        task: taskSlug,
        project,
        entrypoint,
        standalone,
        user_request: research,
      });
      await startRun(detail.run_id);
      router.push(`/runs/${detail.run_id}`);
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col gap-4 p-8 text-slate-200">
      <Link href="/" className="text-xs text-slate-400 hover:text-white">
        {t("entries.back")}
      </Link>
      <h1 className="text-lg font-semibold text-slate-100">
        {t("entries.start")} · <span className="font-mono text-mars-accent">{entrypoint}</span>
      </h1>
      <p className="text-xs text-slate-500">
        {standalone
          ? t("newrun.mode.template")
          : t("entries.card.pipeline.blurb")}
      </p>

      <label className="block">
        <span className="mb-1 block text-[11px] uppercase tracking-wider text-slate-500">
          {t("newrun.field.research")}
        </span>
        <textarea
          value={research}
          onChange={(e) => setResearch(e.target.value)}
          rows={5}
          placeholder={t("sidebar.input.research_placeholder")}
          className="w-full resize-none rounded border border-mars-border bg-mars-bg/60 p-2 text-sm text-slate-200 outline-none focus:border-mars-accent"
        />
      </label>

      <label className="block">
        <span className="mb-1 block text-[11px] uppercase tracking-wider text-slate-500">
          {t("sidebar.input.project")}
        </span>
        <input
          value={project}
          onChange={(e) => setProject(e.target.value)}
          className="w-full rounded border border-mars-border bg-mars-bg/60 p-2 text-sm text-slate-200 outline-none focus:border-mars-accent"
        />
      </label>

      {err ? (
        <p className="rounded bg-rose-500/10 px-2 py-1 text-xs text-rose-300">{err}</p>
      ) : null}

      <button
        disabled={busy}
        onClick={() => void submit()}
        className="rounded bg-mars-accent py-2 text-sm font-medium text-white hover:bg-mars-accent2 disabled:opacity-50"
      >
        {busy ? t("common.loading") : t("entries.start")}
      </button>
    </div>
  );
}

export default function NewRunPage(): JSX.Element {
  return (
    <Suspense fallback={<div className="p-8 text-xs text-slate-500">…</div>}>
      <NewRunInner />
    </Suspense>
  );
}
