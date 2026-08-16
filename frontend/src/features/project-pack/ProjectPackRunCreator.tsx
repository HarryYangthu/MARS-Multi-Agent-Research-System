"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import {
  createV31Run,
  getProjectPackUiSchema,
  getSystemVersion,
  isUnavailable,
  listProjectPacks,
  startV31Run,
  supportsProjectCapability,
} from "./api";
import { DynamicProjectPackForm } from "./DynamicProjectPackForm";
import {
  initialProjectPackValues,
  validateProjectPackValues,
} from "./schema";
import type {
  DynamicProjectPackUiSchema,
  IdeaBudgetProfile,
  IdeaMode,
  JsonObject,
  ProjectPackSummary,
  ProjectPackValidationIssue,
  SystemVersion,
} from "./types";

const MODE_OPTIONS: Array<{ value: IdeaMode; label: string; detail: string }> = [
  { value: "auto", label: "Auto", detail: "首次 deep，修订 fast" },
  { value: "fast", label: "Fast", detail: "沿用 V3.0 Idea 路径" },
  { value: "deep", label: "Deep", detail: "强制 Co-Scientist 深度发现" },
];

const BUDGET_OPTIONS: Array<{ value: IdeaBudgetProfile; label: string; detail: string }> = [
  { value: "fast", label: "Fast", detail: "4 初始 · 1 轮 · 6 比赛" },
  { value: "balanced", label: "Balanced", detail: "8 初始 · 2 轮 · 16 比赛" },
  { value: "thorough", label: "Thorough", detail: "12 初始 · 3 轮 · 32 比赛" },
];

export function ProjectPackRunCreator(): JSX.Element {
  const router = useRouter();
  const [catalog, setCatalog] = useState<ProjectPackSummary[]>([]);
  const [version, setVersion] = useState<SystemVersion | null>(null);
  const [project, setProject] = useState("");
  const [schema, setSchema] = useState<DynamicProjectPackUiSchema | null>(null);
  const [inputs, setInputs] = useState<JsonObject>({});
  const [issues, setIssues] = useState<ProjectPackValidationIssue[]>([]);
  const [mode, setMode] = useState<IdeaMode>("auto");
  const [budget, setBudget] = useState<IdeaBudgetProfile>("balanced");
  const [task, setTask] = useState("model-discovery");
  const [request, setRequest] = useState("");
  const [loading, setLoading] = useState(true);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [compatibilityReason, setCompatibilityReason] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const selectedPack = useMemo(
    () => catalog.find((item) => item.name === project) ?? null,
    [catalog, project],
  );
  const hasDeepCapability = supportsProjectCapability(
    version,
    selectedPack,
    "idea_deep_discovery",
  );
  const compatibilityMode =
    Boolean(compatibilityReason) ||
    selectedPack?.compatibility_mode === "v30_legacy" ||
    !hasDeepCapability;

  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled([
      listProjectPacks(controller.signal),
      getSystemVersion(controller.signal),
    ]).then(([projectsResult, versionResult]) => {
      if (controller.signal.aborted) return;
      if (projectsResult.status === "fulfilled") {
        const projects = projectsResult.value;
        setCatalog(projects);
        setProject((current) =>
          projects.some((item) => item.name === current)
            ? current
            : projects.find((item) => item.compatibility_mode === "v31_pack")?.name ??
              projects[0]?.name ??
              "",
        );
      } else {
        setError(String(projectsResult.reason));
      }
      if (versionResult.status === "fulfilled") {
        setVersion(versionResult.value);
      } else if (isUnavailable(versionResult.reason)) {
        setCompatibilityReason("系统未提供 V3.1 capability API，按 V3.0 兼容模式创建。 ");
      } else {
        setCompatibilityReason("无法确认 V3.1 capability，按 V3.0 兼容模式创建。 ");
      }
      setLoading(false);
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!project || !selectedPack) return;
    if (selectedPack.compatibility_mode === "v30_legacy") {
      setSchema(null);
      setInputs({});
      setMode("fast");
      setCompatibilityReason("该项目未加载 Project Pack，使用 V3.0 经典 Idea 路径。 ");
      return;
    }
    const controller = new AbortController();
    setSchemaLoading(true);
    setError("");
    void getProjectPackUiSchema(project, controller.signal)
      .then((nextSchema) => {
        if (controller.signal.aborted) return;
        setSchema(nextSchema);
        setInputs(initialProjectPackValues(nextSchema));
        setIssues([]);
        setCompatibilityReason("");
      })
      .catch((nextError: unknown) => {
        if (controller.signal.aborted) return;
        setSchema(null);
        setInputs({});
        if (isUnavailable(nextError)) {
          setMode("fast");
          setCompatibilityReason("Project Pack UI Schema 不可用，已切换 V3.0 兼容模式。 ");
        } else {
          setError(String(nextError));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setSchemaLoading(false);
      });
    return () => controller.abort();
  }, [project, selectedPack]);

  async function submit(): Promise<void> {
    if (!project || !request.trim()) {
      setError("请选择项目并填写研究问题。");
      return;
    }
    const nextIssues = schema ? validateProjectPackValues(schema, inputs) : [];
    setIssues(nextIssues);
    if (nextIssues.length) {
      setError("Project Pack 输入尚未通过 UI Schema 校验。");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const created = await createV31Run({
        task: task.trim() || "model-discovery",
        project,
        userRequest: request.trim(),
        ideaMode: compatibilityMode ? "fast" : mode,
        budgetProfile: budget,
        projectInputs: inputs,
        compatibilityMode,
      });
      await startV31Run(created.run_id);
      router.push(`/runs/${encodeURIComponent(created.run_id)}/idea-discovery`);
    } catch (nextError) {
      setError(String(nextError));
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-mars-bg px-5 py-8 text-slate-100 md:px-8">
      <div className="mx-auto max-w-6xl">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.25em] text-indigo-300">
              MARS V3.1 · Idea
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight">Create discovery run</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
              Project Pack 提供领域字段；Core 只负责按 UI Schema 渲染并提交标准化输入。
            </p>
          </div>
          <Link
            href="/runs/new?entrypoint=idea"
            className="rounded-lg border border-mars-border bg-mars-panel px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500 hover:text-white"
          >
            经典 New Run
          </Link>
        </header>

        {compatibilityMode && selectedPack ? (
          <CompatibilityBanner reason={compatibilityReason || "当前项目未声明深度发现能力。"} />
        ) : null}
        {error ? (
          <div role="alert" className="mb-5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            {error}
          </div>
        ) : null}

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
          <div className="space-y-5">
            <section className="rounded-2xl border border-mars-border bg-mars-panel p-5">
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Project">
                  <select
                    className={inputClass()}
                    value={project}
                    disabled={loading || catalog.length === 0}
                    onChange={(event) => setProject(event.target.value)}
                  >
                    {catalog.map((item) => (
                      <option key={item.name} value={item.name}>
                        {item.display_name || item.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Task slug">
                  <input className={inputClass()} value={task} onChange={(event) => setTask(event.target.value)} />
                </Field>
              </div>
              {selectedPack ? (
                <div className="mt-4 flex flex-wrap gap-2 text-xs">
                  <Badge>{selectedPack.compatibility_mode === "v31_pack" ? `Pack ${selectedPack.pack_version ?? "unknown"}` : "V3.0 legacy"}</Badge>
                  <Badge>{selectedPack.pack_distribution ?? "unversioned"}</Badge>
                  {selectedPack.capabilities.map((capability) => <Badge key={capability}>{capability}</Badge>)}
                </div>
              ) : null}
            </section>

            {schemaLoading ? <LoadingCard label="Loading Project Pack UI Schema…" /> : null}
            {!schemaLoading && schema ? (
              <DynamicProjectPackForm schema={schema} values={inputs} issues={issues} onChange={setInputs} />
            ) : null}
            {!schemaLoading && !schema && selectedPack ? (
              <section className="rounded-2xl border border-dashed border-mars-border bg-mars-panel/50 p-5 text-sm text-slate-400">
                兼容模式不要求 Project Pack 字段；创建请求将保持 V3.0 payload。
              </section>
            ) : null}
          </div>

          <aside className="space-y-5">
            <section className="rounded-2xl border border-mars-border bg-mars-panel p-5">
              <h2 className="text-base font-semibold">Idea strategy</h2>
              <div className="mt-4 space-y-3">
                {MODE_OPTIONS.map((option) => (
                  <ChoiceCard
                    key={option.value}
                    name="idea-mode"
                    checked={(compatibilityMode ? "fast" : mode) === option.value}
                    disabled={compatibilityMode && option.value !== "fast"}
                    label={option.label}
                    detail={option.detail}
                    onChange={() => setMode(option.value)}
                  />
                ))}
              </div>
            </section>

            <section className="rounded-2xl border border-mars-border bg-mars-panel p-5">
              <h2 className="text-base font-semibold">Discovery budget</h2>
              <div className="mt-4 grid gap-2">
                {BUDGET_OPTIONS.map((option) => (
                  <ChoiceCard
                    key={option.value}
                    name="idea-budget"
                    checked={budget === option.value}
                    disabled={compatibilityMode}
                    label={option.label}
                    detail={option.detail}
                    onChange={() => setBudget(option.value)}
                  />
                ))}
              </div>
            </section>

            <section className="rounded-2xl border border-mars-border bg-mars-panel p-5">
              <Field label="Research question">
                <textarea
                  className={`${inputClass()} min-h-36 resize-y`}
                  value={request}
                  onChange={(event) => setRequest(event.target.value)}
                  placeholder="Describe the objective, frozen constraints, and falsifiable success criteria."
                />
              </Field>
              <button
                type="button"
                disabled={submitting || loading || !project}
                onClick={() => void submit()}
                className="mt-5 w-full rounded-xl bg-indigo-500 px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-indigo-950/40 transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? "Creating and starting…" : compatibilityMode ? "Create V3.0-compatible run" : "Create V3.1 discovery run"}
              </button>
            </section>
          </aside>
        </div>
      </div>
    </main>
  );
}

function CompatibilityBanner({ reason }: { reason: string }): JSX.Element {
  return (
    <div className="mb-5 rounded-xl border border-amber-400/40 bg-amber-400/10 px-4 py-3">
      <p className="text-sm font-semibold text-amber-200">V3.0 兼容模式</p>
      <p className="mt-1 text-xs leading-5 text-amber-100/70">{reason}</p>
    </div>
  );
}

function ChoiceCard({
  name,
  checked,
  disabled,
  label,
  detail,
  onChange,
}: {
  name: string;
  checked: boolean;
  disabled: boolean;
  label: string;
  detail: string;
  onChange: () => void;
}): JSX.Element {
  return (
    <label className={`flex cursor-pointer gap-3 rounded-xl border p-3 transition ${checked ? "border-indigo-400 bg-indigo-500/10" : "border-mars-border bg-mars-bg/50"} ${disabled ? "cursor-not-allowed opacity-40" : "hover:border-slate-500"}`}>
      <input type="radio" name={name} checked={checked} disabled={disabled} onChange={onChange} className="mt-1 accent-indigo-500" />
      <span>
        <span className="block text-sm font-medium text-slate-100">{label}</span>
        <span className="mt-0.5 block text-xs text-slate-500">{detail}</span>
      </span>
    </label>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <label className="block space-y-2">
      <span className="text-sm font-medium text-slate-200">{label}</span>
      {children}
    </label>
  );
}

function Badge({ children }: { children: React.ReactNode }): JSX.Element {
  return <span className="rounded-full border border-mars-border bg-mars-bg/70 px-2.5 py-1 text-slate-400">{children}</span>;
}

function LoadingCard({ label }: { label: string }): JSX.Element {
  return <div className="rounded-2xl border border-mars-border bg-mars-panel p-5 text-sm text-slate-400">{label}</div>;
}

function inputClass(): string {
  return "w-full rounded-lg border border-mars-border bg-mars-bg/80 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-50";
}
