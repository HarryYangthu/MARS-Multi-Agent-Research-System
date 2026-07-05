"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { TopBar } from "@/components/TopBar";
import { listRuns, type RunSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useProject } from "@/lib/project";

export default function RunsList(): JSX.Element {
  const { t } = useI18n();
  const { selectedProject } = useProject();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    void listRuns(selectedProject)
      .then((r) => setRuns(r.reverse()))
      .catch((e) => setErr(String(e)));
  }, [selectedProject]);

  return (
    <div className="grid h-screen grid-rows-[auto_1fr]">
      <TopBar />
      <main className="container mx-auto max-w-5xl px-6 py-8">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-bold">{selectedProject} runs · {runs.length}</h1>
          <Link href="/" className="text-xs text-slate-400 hover:text-slate-200">
            ← Lab
          </Link>
        </header>
        {err ? <p className="text-sm text-red-300">{err}</p> : null}
        {runs.length === 0 ? (
          <p className="text-sm text-slate-500">{t("sidebar.no_runs")}</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="py-2">{t("common.run_id")}</th>
                <th>{t("common.task")}</th>
                <th>{t("common.project")}</th>
                <th>{t("common.entrypoint")}</th>
                <th>{t("common.created_at")}</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id} className="border-t border-mars-border">
                  <td className="py-2">
                    <Link
                      href={`/runs/${r.run_id}`}
                      className="font-mono text-xs text-mars-accent hover:underline"
                    >
                      {r.run_id}
                    </Link>
                  </td>
                  <td>{r.task}</td>
                  <td>{r.project}</td>
                  <td>{r.entrypoint}</td>
                  <td className="text-slate-400">{r.created_at.slice(0, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </div>
  );
}
