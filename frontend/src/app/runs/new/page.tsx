"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

import { TopBar } from "@/components/TopBar";
import { createRun, getTemplateByAgent, startRun } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useProject } from "@/lib/project";

const VALID_ENTRYPOINTS = new Set([
  "pipeline",
  "idea",
  "experiment",
  "coding",
  "execution",
  "writing",
]);

// Pipeline + Idea use the simple research-question form (Idea Agent will
// draft proposal.v1 for you). Other entries use the schema-template form.
const TEMPLATE_ENTRIES = new Set(["experiment", "coding", "execution", "writing"]);

export default function NewRun(): JSX.Element {
  return (
    <Suspense
      fallback={<main className="container mx-auto max-w-3xl px-6 py-12">Loading…</main>}
    >
      <NewRunInner />
    </Suspense>
  );
}

function NewRunInner(): JSX.Element {
  const router = useRouter();
  const params = useSearchParams();
  const { t } = useI18n();
  const { selectedProject, setSelectedProject, projects } = useProject();
  const initialEntry = params?.get("entrypoint") ?? "pipeline";
  const entrypoint = VALID_ENTRYPOINTS.has(initialEntry) ? initialEntry : "pipeline";
  const usesTemplate = TEMPLATE_ENTRIES.has(entrypoint);

  const [task, setTask] = useState("PIMC 8L 路由简化");
  const [project, setProject] = useState(selectedProject);
  const [userRequest, setUserRequest] = useState(
    "如何在 8L 配置下进一步降低 PIMC 的计算资源，同时保持 RES 性能？请用中文生成研究假设、实验方案和后续产物。",
  );

  useEffect(() => {
    setProject(selectedProject);
  }, [selectedProject]);
  const [seedArtifact, setSeedArtifact] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [schemaErrors, setSchemaErrors] = useState<{ path: string; message: string }[] | null>(
    null,
  );

  // Load schema template when standalone agent entry needs one.
  useEffect(() => {
    if (!usesTemplate) return;
    void getTemplateByAgent(entrypoint)
      .then((tpl) => setSeedArtifact(tpl.text))
      .catch((e) => setErr(`Failed to load template: ${e}`));
  }, [entrypoint, usesTemplate]);

  async function submit(): Promise<void> {
    setBusy(true);
    setErr(null);
    setSchemaErrors(null);
    try {
      const body: Parameters<typeof createRun>[0] = {
        task,
        project,
        entrypoint,
        user_request: userRequest,
      };
      if (usesTemplate) {
        body.seed_artifact = seedArtifact;
      }
      const detail = await createRun(body);
      await startRun(detail.run_id);
      const initialAgent = entrypoint === "pipeline" ? "commander" : entrypoint;
      router.push(`/runs/${detail.run_id}?agent=${initialAgent}`);
    } catch (e) {
      const msg = String(e);
      // Try to parse Schema 422 errors from the server.
      const m = /HTTP 422: (.+)/.exec(msg);
      if (m) {
        try {
          const body = JSON.parse(m[1]);
          if (body.detail?.errors && Array.isArray(body.detail.errors)) {
            setSchemaErrors(
              body.detail.errors as { path: string; message: string }[],
            );
            setErr(t("newrun.submit.failed"));
            setBusy(false);
            return;
          }
        } catch {
          /* fall through */
        }
      }
      setErr(msg);
      setBusy(false);
    }
  }

  return (
    <div className="grid h-screen grid-rows-[auto_1fr] bg-mars-bg">
      <TopBar />
      <main className="container mx-auto max-w-4xl overflow-auto px-6 py-8">
        <header className="mb-6 flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-100">New Run</h1>
            <p className="mt-1 text-sm text-slate-400">
              Entry: <span className="text-mars-accent">{entrypoint}</span>
              <span className="ml-3 text-slate-500">
                {usesTemplate ? t("newrun.mode.template") : t("newrun.mode.research")}
              </span>
            </p>
          </div>
          <Link
            href="/"
            className="rounded border border-mars-border bg-mars-panel px-3 py-1.5 text-xs text-slate-300 hover:bg-mars-subtle"
          >
            ← Lab
          </Link>
        </header>

        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="Task slug">
              <input
                value={task}
                onChange={(e) => setTask(e.target.value)}
                className="input"
              />
            </Field>
            <Field label="Project">
              {projects.length > 0 ? (
                <select
                  value={project}
                  onChange={(e) => {
                    setProject(e.target.value);
                    setSelectedProject(e.target.value);
                  }}
                  className="input"
                >
                  {projects.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}
                      {item.repo_exists ? "" : " (repo missing)"}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  value={project}
                  onChange={(e) => {
                    setProject(e.target.value);
                    setSelectedProject(e.target.value);
                  }}
                  className="input"
                />
              )}
            </Field>
          </div>

          {!usesTemplate ? (
            <Field label={t("newrun.field.research")}>
              <textarea
                value={userRequest}
                onChange={(e) => setUserRequest(e.target.value)}
                rows={6}
                className="input font-mono"
              />
            </Field>
          ) : (
            <>
              <div className="rounded border border-mars-accent/40 bg-mars-accent/10 px-3 py-2 text-xs text-slate-200">
                <p className="font-medium">{t("newrun.template.show")}</p>
                <p className="mt-1 text-[11px] text-slate-400">
                  {t("newrun.template.note")}
                </p>
              </div>
              <Field label={t("newrun.field.markdown")}>
                <textarea
                  value={seedArtifact}
                  onChange={(e) => setSeedArtifact(e.target.value)}
                  rows={22}
                  spellCheck={false}
                  className="input font-mono text-xs leading-relaxed"
                />
              </Field>
            </>
          )}

          {schemaErrors ? (
            <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              <p className="font-semibold">{t("newrun.submit.failed")}</p>
              <ul className="mt-2 list-disc pl-4">
                {schemaErrors.map((se, i) => (
                  <li key={i}>
                    <span className="font-mono">{se.path}</span>: {se.message}
                  </li>
                ))}
              </ul>
            </div>
          ) : err ? (
            <pre className="whitespace-pre-wrap rounded border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">
              {err}
            </pre>
          ) : null}

          <button
            disabled={busy}
            onClick={submit}
            className="rounded bg-mars-accent px-5 py-2 font-medium text-white disabled:opacity-50"
          >
            {busy ? "Creating…" : "Start Run"}
          </button>
        </div>

        <style>{`.input { width:100%; padding:0.5rem 0.75rem; border-radius:0.375rem; background:#0b0d12; border:1px solid #23262d; color:#e2e8f0; }`}</style>
      </main>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <label className="block">
      <span className="mb-1 block text-xs uppercase tracking-wider text-slate-400">
        {label}
      </span>
      {children}
    </label>
  );
}
