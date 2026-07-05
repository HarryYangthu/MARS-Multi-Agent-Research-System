"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  dataSourceSpectrumUrl,
  type DataSourceProfile,
  listDataSources,
  setDefaultDataSource,
  updateDataSource,
  uploadDataSource,
} from "@/lib/api";
import {
  readActiveDataSourceId,
  writeActiveDataSourceId,
} from "@/lib/dataSourceSelection";
import { useProject } from "@/lib/project";

export function DataSourcePrepPanel(): JSX.Element {
  const { selectedProject } = useProject();
  const [profiles, setProfiles] = useState<DataSourceProfile[]>([]);
  const [activeId, setActiveId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fsMhz, setFsMhz] = useState("184.32");
  const [kind, setKind] = useState("paper_static");
  const [channels, setChannels] = useState("16");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const active = useMemo(
    () => profiles.find((item) => item.id === activeId) ?? profiles[0] ?? null,
    [activeId, profiles],
  );

  useEffect(() => {
    setActiveId(readActiveDataSourceId(selectedProject));
    void refreshProfiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProject]);

  useEffect(() => {
    if (!active) return;
    setFsMhz(active.fs_mhz === null ? "" : String(active.fs_mhz));
    setKind(active.kind || "auto");
    setChannels(active.channel_count === null ? "" : String(active.channel_count));
    setDescription(active.description || "");
  }, [active?.id]);

  async function refreshProfiles(nextActiveId = ""): Promise<void> {
    try {
      const next = await listDataSources(selectedProject);
      setProfiles(next);
      const backendDefault = next.find((item) => item.is_default)?.id ?? "";
      const saved = nextActiveId || backendDefault || readActiveDataSourceId(selectedProject);
      if (saved && next.some((item) => item.id === saved)) {
        setActiveId(saved);
      } else if (next.length > 0) {
        setActive(next[0].id);
      } else {
        setActiveId("");
      }
    } catch (e) {
      setError(String(e));
    }
  }

  function setActive(id: string): void {
    setActiveId(id);
    writeActiveDataSourceId(selectedProject, id);
    void setDefaultDataSource(selectedProject, id).catch((e) => setError(String(e)));
  }

  async function saveProfile(): Promise<void> {
    setBusy(true);
    setError("");
    try {
      if (file) {
        const uploaded = await uploadDataSource({
          file,
          project: selectedProject,
          fsMhz: parseOptionalNumber(fsMhz),
          kind,
          channelCount: parseOptionalInteger(channels),
          description,
        });
        setFile(null);
        setActive(uploaded.id);
        await refreshProfiles(uploaded.id);
        return;
      }
      if (active) {
        const updated = await updateDataSource(active.id, {
          fs_mhz: parseOptionalNumber(fsMhz),
          kind,
          channel_count: parseOptionalInteger(channels),
          description,
        });
        setActive(updated.id);
        await refreshProfiles(updated.id);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const launchHref = active
    ? `/runs/new?entrypoint=pipeline&data_source=${encodeURIComponent(active.id)}`
    : "/runs/new?entrypoint=pipeline";

  return (
    <section className="rounded border border-cyan-500/30 bg-mars-panel/80">
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        accept=".npz,.npy,.csv,.json,.pth,.pt,.mat,.h5,.hdf5"
        onChange={(event) => {
          const next = event.target.files?.[0] ?? null;
          setFile(next);
          setError("");
          if (next) setExpanded(true);
        }}
      />

      <div className="flex flex-wrap items-center gap-3 px-3 py-2">
        <div className="min-w-[150px]">
          <div className="text-sm font-semibold text-slate-100">数据准备</div>
          <div className="text-[11px] text-slate-500">先分析数据，再启动研究</div>
        </div>

        <div className="min-w-0 flex-1">
          {active ? (
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="rounded bg-cyan-500/15 px-2 py-0.5 text-[11px] text-cyan-100">
                当前数据
              </span>
              <span className="max-w-[280px] truncate font-mono text-sm text-slate-100">
                {active.original_name}
              </span>
              <CompactMetric label="fs" value={active.fs_mhz === null ? "未配置" : `${active.fs_mhz} MHz`} />
              <CompactMetric label="shape" value={active.shape ? `[${active.shape.join(", ")}]` : "未解析"} />
              <CompactMetric label="dtype" value={active.dtype || "未解析"} />
              <CompactMetric
                label="entries"
                value={active.dict_entries.length > 0 ? String(active.dict_entries.length) : "1"}
              />
              {active.warnings.length > 0 ? (
                <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[11px] text-amber-100">
                  {active.warnings.length} 个提示
                </span>
              ) : null}
            </div>
          ) : (
            <div className="text-sm text-slate-400">尚未选择真实数据</div>
          )}
          {file ? (
            <div className="mt-1 text-xs text-amber-100">
              待分析：<span className="font-mono">{file.name}</span>
              <span className="ml-2 text-amber-200/80">{formatBytes(file.size)}</span>
            </div>
          ) : null}
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setExpanded(true);
              inputRef.current?.click();
            }}
            className="rounded border border-mars-border bg-mars-panel2 px-3 py-1.5 text-xs text-slate-200 hover:bg-mars-subtle"
          >
            选择数据
          </button>
          <button
            type="button"
            disabled={busy || (!file && !active)}
            onClick={() => void saveProfile()}
            className="rounded border border-cyan-500/40 bg-cyan-500/10 px-3 py-1.5 text-xs text-cyan-100 disabled:opacity-50"
          >
            {busy ? "分析中…" : file ? "分析" : "刷新"}
          </button>
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="rounded border border-mars-border bg-mars-panel2 px-3 py-1.5 text-xs text-slate-300 hover:bg-mars-subtle"
          >
            {expanded ? "收起详情" : "展开详情"}
          </button>
          <Link
            href={launchHref}
            className="rounded bg-mars-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-mars-accent/90"
          >
            启动研究
          </Link>
        </div>
      </div>

      {error ? (
        <pre className="mx-3 mb-2 whitespace-pre-wrap rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-200">
          {error}
        </pre>
      ) : null}

      {expanded ? (
        <div className="grid gap-3 border-t border-mars-border p-3 lg:grid-cols-[300px_minmax(0,1fr)]">
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <LabeledInput label="fs (MHz)" value={fsMhz} onChange={setFsMhz} />
              <LabeledInput label="通道数" value={channels} onChange={setChannels} />
              <label className="col-span-2 block">
                <span className="mb-1 block text-[11px] text-slate-500">数据类型</span>
                <select
                  value={kind}
                  onChange={(event) => setKind(event.target.value)}
                  className="input h-9 text-xs"
                >
                  <option value="paper_static">paper_static</option>
                  <option value="pim_capture">pim_capture</option>
                  <option value="iq_complex">iq_complex</option>
                  <option value="auto">auto</option>
                </select>
              </label>
              <label className="col-span-2 block">
                <span className="mb-1 block text-[11px] text-slate-500">数据说明</span>
                <input
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  className="input h-9 text-xs"
                  placeholder="例如 38dBm / fr4 / rnd32"
                />
              </label>
            </div>

            <div className="max-h-28 overflow-auto rounded border border-mars-border bg-mars-bg/45">
              {profiles.length > 0 ? (
                profiles.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setActive(item.id)}
                    className={`block w-full border-b border-mars-border/70 px-2 py-2 text-left text-xs last:border-b-0 ${
                      item.id === active?.id ? "bg-cyan-500/15" : "hover:bg-mars-subtle/70"
                    }`}
                  >
                    <div className="truncate font-mono text-slate-200">{item.original_name}</div>
                    <div className="mt-0.5 flex gap-2 text-[11px] text-slate-500">
                      <span>{item.format}</span>
                      <span>{item.fs_mhz ?? "fs?"} MHz</span>
                      <span>{formatBytes(item.size_bytes)}</span>
                    </div>
                  </button>
                ))
              ) : (
                <p className="p-3 text-xs text-slate-500">还没有登记过数据。</p>
              )}
            </div>
          </div>

          <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_320px]">
            {active ? (
              <>
                <div className="space-y-2">
                  <MetricGrid profile={active} />
                  <InfoRow label="checksum" value={active.checksum} />
                  <InfoRow label="存储位置" value={active.stored_path} />
                  {active.dict_entries.length > 0 ? (
                    <DictEntryList entries={active.dict_entries} />
                  ) : null}
                  {active.warnings.length > 0 ? (
                    <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-100">
                      {active.warnings.map((warning) => (
                        <div key={warning}>• {warning}</div>
                      ))}
                    </div>
                  ) : null}
                  <p className="rounded border border-cyan-500/25 bg-cyan-500/10 p-2 text-xs text-cyan-100">
                    数据详情会进入 Idea/Experiment/Execution 上下文；研究前先确认 fs、shape、dtype 和频谱。
                  </p>
                </div>
                <div className="rounded border border-mars-border bg-black/20 p-2">
                  {active.spectrum_available ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={`${dataSourceSpectrumUrl(active.id)}?v=${encodeURIComponent(active.created_at)}`}
                      alt="dataset spectrum"
                      className="h-auto max-h-72 w-full rounded object-contain"
                    />
                  ) : (
                    <div className="flex h-36 items-center justify-center text-xs text-slate-500">
                      暂无频谱预览
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="flex min-h-36 items-center justify-center rounded border border-dashed border-mars-border text-sm text-slate-500 xl:col-span-2">
                先选择真实数据并生成预览，然后再启动研究。
              </div>
            )}
          </div>
        </div>
      ) : null}
      <style>{`.input { width:100%; padding:0.5rem 0.75rem; border-radius:0.375rem; background:#0b0d12; border:1px solid #23262d; color:#e2e8f0; }`}</style>
    </section>
  );
}

function CompactMetric({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <span className="rounded border border-mars-border bg-mars-bg/60 px-2 py-0.5 text-[11px] text-slate-300">
      <span className="text-slate-500">{label}</span>
      <span className="ml-1 font-mono text-slate-200">{value}</span>
    </span>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}): JSX.Element {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-slate-500">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="input h-9 text-xs"
      />
    </label>
  );
}

function MetricGrid({ profile }: { profile: DataSourceProfile }): JSX.Element {
  const rows = [
    ["format", profile.format],
    ["fs", profile.fs_mhz === null ? "未配置" : `${profile.fs_mhz} MHz`],
    ["shape", profile.shape ? `[${profile.shape.join(", ")}]` : "未解析"],
    ["dtype", profile.dtype || "未解析"],
    ["preview", profile.preview_key || "n/a"],
    ["entries", profile.dict_entries.length > 0 ? String(profile.dict_entries.length) : "1"],
    ["points", String(profile.sample_points)],
  ];
  return (
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded border border-mars-border bg-mars-bg/50 p-2">
          <div className="text-[10px] uppercase text-slate-500">{label}</div>
          <div className="mt-1 truncate font-mono text-xs text-slate-200" title={value}>
            {value}
          </div>
        </div>
      ))}
    </div>
  );
}

function DictEntryList({
  entries,
}: {
  entries: DataSourceProfile["dict_entries"];
}): JSX.Element {
  return (
    <div className="max-h-24 overflow-auto rounded border border-mars-border bg-mars-bg/45 p-2">
      <div className="mb-1 text-[10px] uppercase text-slate-500">字典项</div>
      <div className="space-y-1">
        {entries.map((entry) => (
          <div
            key={`${entry.key}-${entry.dtype ?? ""}-${entry.sample_points ?? 0}`}
            className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 text-[11px]"
          >
            <span className="truncate font-mono text-slate-200" title={entry.key}>
              {entry.key}
            </span>
            <span className="font-mono text-slate-500">
              {entry.shape ? `[${entry.shape.join(",")}]` : "shape?"}
              {entry.dtype ? ` · ${entry.dtype}` : ""}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="grid grid-cols-[82px_minmax(0,1fr)] gap-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="truncate font-mono text-slate-300" title={value}>
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
