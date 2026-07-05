"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

import { TopBar } from "@/components/TopBar";
import {
  createRun,
  dataSourceSpectrumUrl,
  type DataSourceProfile,
  getDataSource,
  getDefaultDataSource,
  getTemplateByAgent,
  startRun,
  updateDataSource,
  uploadDataSource,
} from "@/lib/api";
import {
  readActiveDataSourceId,
  writeActiveDataSourceId,
} from "@/lib/dataSourceSelection";
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

  const [task, setTask] = useState("PIMC static 残差指标优化");
  const [project, setProject] = useState(selectedProject);
  const [userRequest, setUserRequest] = useState(
    "针对当前 paper_static PIMC 模型（train_static.py --cfg configs/static.yaml）和已接入的真实 static capture，如何在不修改 baseline 受保护代码的前提下，通过可消费的配置、训练或数据处理消融改善 PIM 抵消后的 residual 指标？请同时报告 PIM、paper_RES_db、paper_APE_db，并明确 MARS 指标映射 RES=-paper_APE_db、loss=10**(-paper_APE_db/10)。请用中文生成研究假设、实验方案和后续产物。",
  );

  useEffect(() => {
    setProject(selectedProject);
  }, [selectedProject]);
  const [seedArtifact, setSeedArtifact] = useState("");
  const [dataFile, setDataFile] = useState<File | null>(null);
  const [dataSource, setDataSource] = useState<DataSourceProfile | null>(null);
  const [dataFsMhz, setDataFsMhz] = useState("184.32");
  const [dataKind, setDataKind] = useState("paper_static");
  const [dataChannels, setDataChannels] = useState("16");
  const [dataDescription, setDataDescription] = useState("");
  const [dataBusy, setDataBusy] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [schemaErrors, setSchemaErrors] = useState<{ path: string; message: string }[] | null>(
    null,
  );

  useEffect(() => {
    const requested = params?.get("data_source") ?? "";
    const saved = requested || readActiveDataSourceId(project);
    let alive = true;
    const load = saved ? getDataSource(saved) : getDefaultDataSource(project);
    void load
      .then((profile) => {
        if (!alive) return;
        setDataSource(profile);
        setDataFile(null);
        setDataFsMhz(profile.fs_mhz === null ? "" : String(profile.fs_mhz));
        setDataKind(profile.kind || "auto");
        setDataChannels(profile.channel_count === null ? "" : String(profile.channel_count));
        setDataDescription(profile.description || "");
        writeActiveDataSourceId(project, profile.id);
      })
      .catch((e) => {
        if (!alive) return;
        if (saved || requested) {
          setDataError(`无法加载默认数据源：${e}`);
        } else {
          setDataSource(null);
        }
      });
    return () => {
      alive = false;
    };
  }, [params, project]);

  // Load schema template when standalone agent entry needs one.
  useEffect(() => {
    if (!usesTemplate) return;
    void getTemplateByAgent(entrypoint)
      .then((tpl) => setSeedArtifact(tpl.text))
      .catch((e) => setErr(`Failed to load template: ${e}`));
  }, [entrypoint, usesTemplate]);

  async function uploadSelectedData(): Promise<DataSourceProfile | null> {
    if (!dataFile) {
      return dataSource;
    }
    setDataBusy(true);
    setDataError(null);
    try {
      const profile = await uploadDataSource({
        file: dataFile,
        project,
        fsMhz: parseOptionalNumber(dataFsMhz),
        kind: dataKind,
        channelCount: parseOptionalInteger(dataChannels),
        description: dataDescription,
      });
      setDataSource(profile);
      writeActiveDataSourceId(project, profile.id);
      return profile;
    } catch (e) {
      const message = String(e);
      setDataError(message);
      throw e;
    } finally {
      setDataBusy(false);
    }
  }

  async function refreshDataProfile(): Promise<void> {
    if (!dataSource) {
      await uploadSelectedData();
      return;
    }
    setDataBusy(true);
    setDataError(null);
    try {
      const profile = await updateDataSource(dataSource.id, {
        fs_mhz: parseOptionalNumber(dataFsMhz),
        kind: dataKind,
        channel_count: parseOptionalInteger(dataChannels),
        description: dataDescription,
      });
      setDataSource(profile);
      writeActiveDataSourceId(project, profile.id);
    } catch (e) {
      setDataError(String(e));
    } finally {
      setDataBusy(false);
    }
  }

  async function submit(): Promise<void> {
    setBusy(true);
    setErr(null);
    setSchemaErrors(null);
    try {
      const activeDataSource = dataFile && !dataSource ? await uploadSelectedData() : dataSource;
      const body: Parameters<typeof createRun>[0] = {
        task,
        project,
        entrypoint,
        user_request: userRequest,
      };
      if (activeDataSource) {
        body.data_source = {
          id: activeDataSource.id,
          fs_mhz: parseOptionalNumber(dataFsMhz),
          kind: dataKind,
          channel_count: parseOptionalInteger(dataChannels),
          description: dataDescription,
        };
      }
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

          <section className="rounded border border-mars-border bg-mars-panel p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-100">仿真数据</h2>
                <p className="mt-1 text-xs text-slate-400">
                  使用系统文件选择器接入真实数据；MARS 会复制到 workspace/uploads 并沉淀 checksum、fs 和频谱预览。
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  accept=".npz,.npy,.csv,.json,.pth,.pt,.mat,.h5,.hdf5"
                  onChange={(event) => {
                    const file = event.target.files?.[0] ?? null;
                    setDataFile(file);
                    setDataSource(null);
                    setDataError(null);
                  }}
                />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="rounded border border-mars-border bg-mars-panel2 px-3 py-1.5 text-xs text-slate-200 hover:bg-mars-subtle"
                >
                  选择数据文件
                </button>
                <button
                  type="button"
                  disabled={dataBusy || !dataFile}
                  onClick={() => void refreshDataProfile()}
                  className="rounded bg-mars-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                >
                  {dataBusy ? "生成中…" : dataSource ? "刷新频谱" : "生成预览"}
                </button>
              </div>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-4">
              <Field label="fs (MHz)">
                <input
                  value={dataFsMhz}
                  onChange={(e) => setDataFsMhz(e.target.value)}
                  className="input"
                  placeholder="184.32"
                />
              </Field>
              <Field label="数据类型">
                <select
                  value={dataKind}
                  onChange={(e) => setDataKind(e.target.value)}
                  className="input"
                >
                  <option value="paper_static">paper_static</option>
                  <option value="pim_capture">pim_capture</option>
                  <option value="iq_complex">iq_complex</option>
                  <option value="auto">auto</option>
                </select>
              </Field>
              <Field label="通道 / 端口数">
                <input
                  value={dataChannels}
                  onChange={(e) => setDataChannels(e.target.value)}
                  className="input"
                  placeholder="16"
                />
              </Field>
              <Field label="说明">
                <input
                  value={dataDescription}
                  onChange={(e) => setDataDescription(e.target.value)}
                  className="input"
                  placeholder="例如 38dBm fr4 rnd32"
                />
              </Field>
            </div>

            <div className="mt-3 rounded border border-mars-border bg-mars-bg/60 p-3">
              {dataFile ? (
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
                  <span className="font-mono text-slate-100">{dataFile.name}</span>
                  <span>{formatBytes(dataFile.size)}</span>
                  <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-200">
                    {dataSource ? "已登记" : "待生成预览"}
                  </span>
                </div>
              ) : (
                <p className="text-xs text-slate-500">
                  尚未选择真实数据；若不选择，Execution 会使用当前系统配置里的默认数据/仿真后端。
                </p>
              )}

              {dataSource ? (
                <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_360px]">
                  <div className="space-y-2 text-xs">
                    <InfoRow label="存储位置" value={dataSource.stored_path} mono />
                    <InfoRow label="checksum" value={dataSource.checksum} mono />
                    <InfoRow
                      label="shape"
                      value={dataSource.shape ? `[${dataSource.shape.join(", ")}]` : "未解析"}
                      mono
                    />
                    <InfoRow label="dtype" value={dataSource.dtype || "未解析"} mono />
                    <InfoRow label="预览数组" value={dataSource.preview_key || "n/a"} mono />
                    {dataSource.warnings.length > 0 ? (
                      <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-amber-100">
                        {dataSource.warnings.map((warning) => (
                          <div key={warning}>• {warning}</div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <div className="rounded border border-mars-border bg-black/20 p-2">
                    {dataSource.spectrum_available ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={`${dataSourceSpectrumUrl(dataSource.id)}?v=${encodeURIComponent(dataSource.created_at)}`}
                        alt="dataset spectrum"
                        className="h-auto w-full rounded"
                      />
                    ) : (
                      <div className="flex h-40 items-center justify-center text-xs text-slate-500">
                        暂无频谱预览
                      </div>
                    )}
                  </div>
                </div>
              ) : null}

              {dataError ? (
                <pre className="mt-3 whitespace-pre-wrap rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-200">
                  {dataError}
                </pre>
              ) : null}
            </div>
          </section>

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

function InfoRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="grid grid-cols-[80px_minmax(0,1fr)] gap-2">
      <span className="text-slate-500">{label}</span>
      <span className={`truncate text-slate-200 ${mono ? "font-mono" : ""}`} title={value}>
        {value}
      </span>
    </div>
  );
}

function parseOptionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseOptionalInteger(value: string): number | null {
  const parsed = parseOptionalNumber(value);
  return parsed === null ? null : Math.trunc(parsed);
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  for (const unit of units) {
    if (size < 1024) return `${size.toFixed(size >= 100 ? 0 : 1)} ${unit}`;
    size /= 1024;
  }
  return `${size.toFixed(1)} PB`;
}
