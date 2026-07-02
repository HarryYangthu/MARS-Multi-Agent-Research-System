"use client";

import { useEffect, useMemo, useState, type ChangeEvent } from "react";

import { SidebarToggleButton } from "@/components/SidebarToggleButton";
import {
  createAgentContextItem,
  deleteAgentContextItem,
  getAgentContext,
  updateAgentContextItem,
  updateAgentResearchSites,
  type AgentContextBlueprintItem,
  type AgentContextFile,
  type AgentContextView,
  type AgentResearchSite,
} from "@/lib/api";

type UploadKind = "docs" | "code";

export function AgentContextPanel({
  agent,
  project = "pimc",
  variant = "full",
}: {
  agent: string;
  project?: string;
  variant?: "full" | "compact";
}): JSX.Element {
  const [context, setContext] = useState<AgentContextView | null>(null);
  const [selectedPath, setSelectedPath] = useState("");
  const [draft, setDraft] = useState("");
  const [uploadKind, setUploadKind] = useState<UploadKind>("docs");
  const [sites, setSites] = useState<AgentResearchSite[]>([]);
  const [status, setStatus] = useState("");
  const [fileSidebarCollapsed, setFileSidebarCollapsed] = useState(false);

  const selected = useMemo(
    () => context?.files.find((item) => item.path === selectedPath) ?? null,
    [context?.files, selectedPath],
  );

  async function refresh(nextSelectedPath?: string): Promise<void> {
    const next = await getAgentContext(agent, project);
    setContext(next);
    setSites(next.research_sites);
    const preferred = nextSelectedPath || selectedPath || next.files[0]?.path || "";
    setSelectedPath(preferred);
    const file = next.files.find((item) => item.path === preferred) ?? next.files[0] ?? null;
    setDraft(file?.content ?? "");
  }

  useEffect(() => {
    void refresh().catch((err: unknown) => setStatus(String(err)));
  }, [agent, project]);

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
  const fullGridClass = fileSidebarCollapsed
    ? "grid-cols-[minmax(0,1fr)]"
    : "grid-cols-[280px,1fr]";

  if (variant === "compact") {
    return (
      <section className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="grid grid-cols-3 gap-2 text-center text-[11px] text-slate-400">
          <Metric label="文件" value={context.files.length} />
          <Metric label="可编辑" value={editableCount} />
          <Metric label="只读" value={runtimeCount} />
        </div>

        <div className="rounded border border-mars-border bg-mars-panel/30 p-3">
          <div className="text-xs font-semibold text-slate-100">上下文策略</div>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
            {context.blueprint.goal || `${agentLabel(agent)} Agent context loading strategy`}
          </p>
          <div className="mt-2 flex flex-col gap-1">
            {context.blueprint.items.slice(0, 5).map((item) => (
              <div
                key={`${item.order}-${item.layer}`}
                className="flex items-center gap-2 rounded bg-mars-bg px-2 py-1 text-[11px]"
              >
                <span className="w-5 shrink-0 font-mono text-slate-500">{item.order}</span>
                <span className="min-w-0 flex-1 truncate text-slate-300">{item.layer}</span>
                <span className="shrink-0 text-slate-500">{item.packing_position}</span>
              </div>
            ))}
          </div>
          <div className="mt-2 break-all font-mono text-[10px] text-slate-500">
            {context.blueprint.storage_layout.agent_root}
          </div>
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
          <div className="flex items-center gap-2">
            <div className="grid grid-cols-3 gap-2 text-center text-[11px] text-slate-400">
              <Metric label="文件" value={context.files.length} />
              <Metric label="可编辑" value={editableCount} />
              <Metric label="只读代码" value={runtimeCount} />
            </div>
            <SidebarToggleButton
              collapsed={fileSidebarCollapsed}
              side="left"
              label="上下文文件边栏"
              onToggle={() => setFileSidebarCollapsed((current) => !current)}
            />
          </div>
        </div>
      </div>

      <section className="rounded border border-mars-border bg-mars-panel/30">
        <div className="border-b border-mars-border px-3 py-2">
          <h3 className="text-sm font-semibold text-slate-100">统一上下文存储区</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">
            {context.blueprint.goal || `${agentLabel(agent)} Agent context loading strategy`}
          </p>
        </div>
        <div className="grid gap-2 p-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
          <StoragePath label="长期配置" value={context.blueprint.storage_layout.long_term_root} />
          <StoragePath label="Agent 运行区" value={context.blueprint.storage_layout.agent_root} />
          <StoragePath label="Manifest" value={context.blueprint.storage_layout.manifests} />
          <StoragePath label="Raw/卸载" value={context.blueprint.storage_layout.raw} />
          <StoragePath label="Packed" value={context.blueprint.storage_layout.packed} />
          <StoragePath label="Memory" value={context.blueprint.storage_layout.memory} />
        </div>
      </section>

      <div className={`grid min-h-0 flex-1 gap-3 ${fullGridClass}`}>
        {!fileSidebarCollapsed ? (
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
        ) : null}

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
                  当前 V2 不强制联网；这些站点会作为可选调研来源进入 Idea 上下文。
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

          <section className="rounded border border-mars-border bg-mars-panel/30">
            <div className="border-b border-mars-border px-3 py-2">
              <h3 className="text-sm font-semibold text-slate-100">上下文装载顺序与策略</h3>
              <p className="mt-0.5 text-[11px] text-slate-500">
                每个 Agent 独立配置装载路径、上下文工程策略和 Packing 位置。
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-[980px] text-left text-xs">
                <thead className="border-b border-mars-border text-[11px] uppercase text-slate-500">
                  <tr>
                    <th className="w-12 px-3 py-2">顺序</th>
                    <th className="w-32 px-3 py-2">上下文层</th>
                    <th className="px-3 py-2">具体内容</th>
                    <th className="w-56 px-3 py-2">保存位置</th>
                    <th className="w-16 px-3 py-2">必需性</th>
                    <th className="w-36 px-3 py-2">风险</th>
                    <th className="w-56 px-3 py-2">工程策略</th>
                    <th className="w-36 px-3 py-2">Packing</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-mars-border">
                  {context.blueprint.items.map((item) => (
                    <BlueprintRow key={`${item.order}-${item.layer}`} item={item} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="border-t border-mars-border px-3 py-2">
              <div className="text-[11px] uppercase text-slate-500">推荐 Packing 顺序</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {context.blueprint.packing_order.map((step, index) => (
                  <span
                    key={`${step}-${index}`}
                    className="rounded border border-mars-border bg-mars-bg px-2 py-1 font-mono text-[11px] text-slate-300"
                  >
                    {index + 1}. {step}
                  </span>
                ))}
              </div>
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

function StoragePath({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="rounded border border-mars-border bg-mars-bg px-3 py-2">
      <div className="text-[10px] uppercase text-slate-500">{label}</div>
      <div className="mt-1 break-all font-mono text-[11px] leading-relaxed text-slate-200">
        {value}
      </div>
    </div>
  );
}

function BlueprintRow({ item }: { item: AgentContextBlueprintItem }): JSX.Element {
  return (
    <tr className="align-top text-slate-300">
      <td className="px-3 py-2 font-mono text-slate-400">{item.order}</td>
      <td className="px-3 py-2 font-medium text-slate-100">{item.layer}</td>
      <td className="px-3 py-2 leading-relaxed">{item.content}</td>
      <td className="px-3 py-2">
        <div className="flex flex-col gap-1">
          {item.storage.map((path) => (
            <code
              key={path}
              className="break-all rounded bg-mars-bg px-1.5 py-0.5 text-[11px] text-slate-300"
            >
              {path}
            </code>
          ))}
        </div>
      </td>
      <td className="px-3 py-2">{item.required}</td>
      <td className="px-3 py-2 leading-relaxed">{item.risk}</td>
      <td className="px-3 py-2 leading-relaxed">{item.strategy}</td>
      <td className="px-3 py-2 font-mono text-[11px] text-slate-400">
        {item.packing_position}
      </td>
    </tr>
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
