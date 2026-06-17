"use client";

import { useEffect, useMemo, useState, type ChangeEvent } from "react";

import {
  createAgentContextItem,
  deleteAgentContextItem,
  getAgentContext,
  updateAgentContextItem,
  updateAgentResearchSites,
  type AgentContextFile,
  type AgentContextView,
  type AgentResearchSite,
} from "@/lib/api";

type UploadKind = "docs" | "code";

export function AgentContextPanel({
  agent,
  variant = "full",
}: {
  agent: string;
  variant?: "full" | "compact";
}): JSX.Element {
  const [context, setContext] = useState<AgentContextView | null>(null);
  const [selectedPath, setSelectedPath] = useState("");
  const [draft, setDraft] = useState("");
  const [uploadKind, setUploadKind] = useState<UploadKind>("docs");
  const [sites, setSites] = useState<AgentResearchSite[]>([]);
  const [status, setStatus] = useState("");

  const selected = useMemo(
    () => context?.files.find((item) => item.path === selectedPath) ?? null,
    [context?.files, selectedPath],
  );

  async function refresh(nextSelectedPath?: string): Promise<void> {
    const next = await getAgentContext(agent);
    setContext(next);
    setSites(next.research_sites);
    const preferred = nextSelectedPath || selectedPath || next.files[0]?.path || "";
    setSelectedPath(preferred);
    const file = next.files.find((item) => item.path === preferred) ?? next.files[0] ?? null;
    setDraft(file?.content ?? "");
  }

  useEffect(() => {
    void refresh().catch((err: unknown) => setStatus(String(err)));
  }, [agent]);

  useEffect(() => {
    setDraft(selected?.content ?? "");
  }, [selected?.path]);

  async function saveSelected(): Promise<void> {
    if (!selected || !selected.editable) return;
    const updated = await updateAgentContextItem(agent, {
      path: selected.path,
      content: draft,
    });
    setStatus(`已保存 ${updated.path}`);
    await refresh(updated.path);
  }

  async function deleteSelected(): Promise<void> {
    if (!selected || !selected.deletable) return;
    const ok = window.confirm(`删除 ${selected.path}?`);
    if (!ok) return;
    await deleteAgentContextItem(agent, selected.path);
    setStatus(`已删除 ${selected.path}`);
    await refresh("");
  }

  async function uploadFiles(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) return;
    const category = uploadKind === "code" ? "uploads/code" : "uploads/docs";
    let lastPath = "";
    for (const file of files) {
      const content = await file.text();
      const created = await createAgentContextItem(agent, {
        category,
        filename: file.name,
        content,
      });
      lastPath = created.path;
    }
    event.target.value = "";
    setStatus(`已上传 ${files.length} 个文件`);
    await refresh(lastPath);
  }

  function patchSite(index: number, patch: Partial<AgentResearchSite>): void {
    setSites((prev) =>
      prev.map((site, i) => (i === index ? { ...site, ...patch } : site)),
    );
  }

  async function saveSites(): Promise<void> {
    const saved = await updateAgentResearchSites(agent, sites);
    setSites(saved);
    setStatus("调研站点已保存");
    await refresh(selectedPath);
  }

  if (context === null) {
    return (
      <section className="rounded border border-mars-border bg-mars-panel/35 p-4 text-sm text-slate-400">
        正在读取 Agent 上下文配置…
      </section>
    );
  }

  const editableCount = context.files.filter((file) => file.editable).length;
  const runtimeCount = context.files.filter((file) => file.source === "runtime_code").length;

  if (variant === "compact") {
    return (
      <section className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="grid grid-cols-3 gap-2 text-center text-[11px] text-slate-400">
          <Metric label="文件" value={context.files.length} />
          <Metric label="可编辑" value={editableCount} />
          <Metric label="只读" value={runtimeCount} />
        </div>

        <div className="flex items-center gap-2">
          <select
            value={uploadKind}
            onChange={(event) => setUploadKind(event.target.value as UploadKind)}
            className="rounded border border-mars-border bg-mars-bg px-2 py-1.5 text-xs text-slate-200 outline-none"
          >
            <option value="docs">文档</option>
            <option value="code">代码</option>
          </select>
          <label className="cursor-pointer rounded border border-mars-border px-3 py-1.5 text-xs text-slate-200 hover:bg-mars-panel">
            上传
            <input
              type="file"
              multiple
              className="hidden"
              onChange={(event) => void uploadFiles(event)}
            />
          </label>
        </div>

        <div className="min-h-[150px] overflow-auto rounded border border-mars-border bg-mars-bg/60 p-2">
          {context.files.map((file) => (
            <button
              key={file.path}
              onClick={() => setSelectedPath(file.path)}
              className={`mb-1 w-full rounded px-2 py-2 text-left text-xs ${
                selectedPath === file.path ? "bg-mars-accent/20 text-white" : "text-slate-300 hover:bg-mars-panel"
              }`}
            >
              <span className="block truncate font-mono">{file.path}</span>
              <span className="mt-1 flex items-center gap-1 text-[10px] text-slate-500">
                <span>{sourceLabel(file)}</span>
                <span>{file.size_chars} chars</span>
              </span>
            </button>
          ))}
        </div>

        <div className="flex min-h-[260px] flex-1 flex-col rounded border border-mars-border bg-mars-bg">
          <div className="flex items-center justify-between border-b border-mars-border px-3 py-2">
            <div className="min-w-0">
              <h3 className="truncate text-xs font-semibold text-slate-100">
                {selected?.path ?? "未选择文件"}
              </h3>
              <p className="mt-0.5 text-[10px] text-slate-500">
                {selected ? sourceLabel(selected) : "选择文件"}
              </p>
            </div>
            <div className="flex shrink-0 gap-1.5">
              <button
                onClick={() => void deleteSelected()}
                disabled={!selected?.deletable}
                className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-[11px] text-red-200 hover:bg-red-500/20 disabled:opacity-40"
              >
                删除
              </button>
              <button
                onClick={() => void saveSelected()}
                disabled={!selected?.editable}
                className="rounded bg-mars-accent px-2 py-1 text-[11px] font-medium text-white disabled:opacity-40"
              >
                保存
              </button>
            </div>
          </div>
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            readOnly={!selected?.editable}
            className="min-h-[220px] flex-1 resize-none bg-transparent p-3 font-mono text-xs leading-relaxed text-slate-100 outline-none read-only:text-slate-400"
          />
        </div>

        {status ? (
          <div className="rounded border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
            {status}
          </div>
        ) : null}
      </section>
    );
  }

  return (
    <section className="flex min-h-[520px] flex-1 flex-col gap-3">
      <div className="rounded border border-mars-border bg-mars-panel/35 p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-100">{agentLabel(agent)} Agent 上下文配置</h2>
            <p className="mt-1 max-w-3xl text-xs leading-relaxed text-slate-400">
              默认文档和提示词长期保存在 Agent 配置中；上传的文档或代码会进入下一次该 Agent
              的 self-context。运行时代码只读展示，用来理解当前行为。
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center text-[11px] text-slate-400">
            <Metric label="文件" value={context.files.length} />
            <Metric label="可编辑" value={editableCount} />
            <Metric label="只读代码" value={runtimeCount} />
          </div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[280px,1fr] gap-3">
        <aside className="flex min-h-0 flex-col rounded border border-mars-border bg-mars-panel/30">
          <div className="border-b border-mars-border p-3">
            <div className="flex items-center gap-2">
              <select
                value={uploadKind}
                onChange={(event) => setUploadKind(event.target.value as UploadKind)}
                className="rounded border border-mars-border bg-mars-bg px-2 py-1.5 text-xs text-slate-200 outline-none"
              >
                <option value="docs">文档</option>
                <option value="code">代码</option>
              </select>
              <label className="cursor-pointer rounded border border-mars-border px-3 py-1.5 text-xs text-slate-200 hover:bg-mars-bg">
                上传
                <input
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(event) => void uploadFiles(event)}
                />
              </label>
            </div>
            <p className="mt-2 text-[11px] text-slate-500">
              文档默认写入 `uploads/docs`，代码默认写入 `uploads/code`。
            </p>
          </div>

          <div className="min-h-0 flex-1 overflow-auto p-2">
            {context.files.map((file) => (
              <button
                key={file.path}
                onClick={() => setSelectedPath(file.path)}
                className={`mb-1 w-full rounded px-2 py-2 text-left text-xs ${
                  selectedPath === file.path ? "bg-mars-accent/20 text-white" : "text-slate-300 hover:bg-mars-bg"
                }`}
              >
                <span className="block truncate font-mono">{file.path}</span>
                <span className="mt-1 flex items-center gap-1 text-[10px] text-slate-500">
                  <span>{sourceLabel(file)}</span>
                  <span>{file.size_chars} chars</span>
                </span>
              </button>
            ))}
          </div>
        </aside>

        <div className="flex min-h-0 flex-col gap-3">
          <section className="flex min-h-[300px] flex-1 flex-col rounded border border-mars-border bg-mars-bg">
            <div className="flex items-center justify-between border-b border-mars-border px-3 py-2">
              <div>
                <h3 className="text-sm font-semibold text-slate-100">
                  {selected?.path ?? "未选择文件"}
                </h3>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  {selected ? sourceLabel(selected) : "选择左侧文件后可查看内容"}
                </p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => void deleteSelected()}
                  disabled={!selected?.deletable}
                  className="rounded border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs text-red-200 hover:bg-red-500/20 disabled:opacity-40"
                >
                  删除
                </button>
                <button
                  onClick={() => void saveSelected()}
                  disabled={!selected?.editable}
                  className="rounded bg-mars-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                >
                  保存
                </button>
              </div>
            </div>
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              readOnly={!selected?.editable}
              className="min-h-[260px] flex-1 resize-none bg-transparent p-4 font-mono text-sm leading-relaxed text-slate-100 outline-none read-only:text-slate-400"
            />
          </section>

          <section className="rounded border border-mars-border bg-mars-panel/30">
            <div className="flex items-center justify-between border-b border-mars-border px-3 py-2">
              <div>
                <h3 className="text-sm font-semibold text-slate-100">默认调研站点</h3>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  当前 V1 不强制联网；这些站点会作为可选调研来源进入 Idea 上下文。
                </p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() =>
                    setSites((prev) => [
                      ...prev,
                      {
                        id: `custom_${Date.now()}`,
                        label: "New source",
                        url: "https://",
                        enabled: true,
                        source: "custom",
                      },
                    ])
                  }
                  className="rounded border border-mars-border px-3 py-1.5 text-xs text-slate-200 hover:bg-mars-bg"
                >
                  新增
                </button>
                <button
                  onClick={() => void saveSites()}
                  className="rounded bg-mars-accent px-3 py-1.5 text-xs font-medium text-white"
                >
                  保存站点
                </button>
              </div>
            </div>
            <div className="divide-y divide-mars-border">
              {sites.map((site, index) => (
                <div key={`${site.id}-${index}`} className="grid grid-cols-[44px,160px,1fr,70px] gap-2 px-3 py-2">
                  <label className="flex items-center justify-center">
                    <input
                      type="checkbox"
                      checked={site.enabled}
                      onChange={(event) => patchSite(index, { enabled: event.target.checked })}
                    />
                  </label>
                  <input
                    value={site.label}
                    onChange={(event) => patchSite(index, { label: event.target.value })}
                    className="rounded border border-mars-border bg-mars-bg px-2 py-1.5 text-xs text-slate-100 outline-none"
                  />
                  <input
                    value={site.url}
                    onChange={(event) => patchSite(index, { url: event.target.value })}
                    className="rounded border border-mars-border bg-mars-bg px-2 py-1.5 font-mono text-xs text-slate-100 outline-none"
                  />
                  <button
                    onClick={() => setSites((prev) => prev.filter((_, i) => i !== index))}
                    className="rounded border border-mars-border px-2 py-1.5 text-xs text-slate-300 hover:bg-mars-bg"
                  >
                    删除
                  </button>
                </div>
              ))}
            </div>
          </section>

          {status ? (
            <div className="rounded border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
              {status}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number }): JSX.Element {
  return (
    <div className="rounded border border-mars-border bg-mars-bg px-3 py-2">
      <div className="font-mono text-sm text-slate-100">{value}</div>
      <div className="mt-0.5 text-[10px] uppercase text-slate-500">{label}</div>
    </div>
  );
}

function sourceLabel(file: AgentContextFile): string {
  if (file.source === "runtime_code") return "运行时代码 · 只读";
  if (file.source === "uploaded") return "用户上传";
  return "系统默认";
}

function agentLabel(agent: string): string {
  const labels: Record<string, string> = {
    commander: "Commander",
    idea: "Idea",
    experiment: "Experiment",
    coding: "Coding",
    execution: "Execution",
    writing: "Writing",
  };
  return labels[agent] ?? agent;
}
