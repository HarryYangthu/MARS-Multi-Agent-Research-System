"use client";

import { useEffect, useMemo, useState } from "react";

import { AgentContextPanel } from "@/components/AgentContextPanel";
import { SidebarToggleButton } from "@/components/SidebarToggleButton";
import {
  getCodingWorkspace,
  getCodingWorkspaceFile,
  updateCodingMemoryItems,
  type CodeFileContent,
  type CodeTreeItem,
  type CodingMemoryItem,
  type CodingWorkspace,
  type UpstreamContextItem,
} from "@/lib/api";

type InspectorTab = "preview" | "context" | "memory" | "upstream";
type ChatRole = "assistant" | "user";
type ChatMessage = {
  role: ChatRole;
  content: string;
  timestamp: string;
};

export function CodingWorkspacePanel({
  runId,
  project,
}: {
  runId: string;
  project: string;
}): JSX.Element {
  const [workspace, setWorkspace] = useState<CodingWorkspace | null>(null);
  const [source, setSource] = useState("auto");
  const [selectedPath, setSelectedPath] = useState("");
  const [file, setFile] = useState<CodeFileContent | null>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<InspectorTab>("preview");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [memoryDrafts, setMemoryDrafts] = useState<CodingMemoryItem[]>([]);
  const [selectedContextId, setSelectedContextId] = useState("");
  const [status, setStatus] = useState("");
  const [codeSidebarCollapsed, setCodeSidebarCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);

  useEffect(() => {
    let alive = true;
    void getCodingWorkspace({ project, runId, source })
      .then((next) => {
        if (!alive) return;
        setWorkspace(next);
        setMemoryDrafts(next.memory_items);
        const firstFile = next.files.find((item) => item.kind === "file");
        setSelectedPath((prev) => prev || firstFile?.path || "");
        setSelectedContextId((prev) => prev || next.upstream_context[0]?.id || "");
      })
      .catch((err: unknown) => {
        if (alive) setStatus(String(err));
      });
    return () => {
      alive = false;
    };
  }, [project, runId, source]);

  const activeSource = workspace?.selected_source ?? source;

  useEffect(() => {
    if (!workspace || !selectedPath) {
      setFile(null);
      return;
    }
    let alive = true;
    void getCodingWorkspaceFile({
      project,
      source: activeSource,
      path: selectedPath,
    })
      .then((next) => {
        if (alive) setFile(next);
      })
      .catch(() => {
        if (alive) setFile(null);
      });
    return () => {
      alive = false;
    };
  }, [activeSource, project, selectedPath, workspace]);

  useEffect(() => {
    if (!workspace || messages.length > 0) return;
    setMessages([
      {
        role: "assistant",
        content: [
          `已载入 ${workspace.project} 的 Coding 工作区。`,
          `当前代码源：${sourceLabel(workspace.selected_source, workspace)}。`,
          `上游上下文：${workspace.upstream_context.length} 项。`,
        ].join("\n"),
        timestamp: new Date().toISOString(),
      },
    ]);
  }, [messages.length, workspace]);

  const visibleFiles = useMemo(() => {
    if (!workspace) return [];
    const needle = query.trim().toLowerCase();
    if (!needle) return workspace.files;
    return workspace.files.filter((item) => item.path.toLowerCase().includes(needle));
  }, [query, workspace]);

  const selectedContext = useMemo(() => {
    if (!workspace) return null;
    return workspace.upstream_context.find((item) => item.id === selectedContextId) ?? null;
  }, [selectedContextId, workspace]);
  const workspaceGridClass =
    codeSidebarCollapsed && inspectorCollapsed
      ? "grid-cols-[minmax(340px,1fr)]"
      : codeSidebarCollapsed
        ? "grid-cols-[minmax(340px,1fr),340px]"
        : inspectorCollapsed
          ? "grid-cols-[260px,minmax(340px,1fr)]"
          : "grid-cols-[260px,minmax(340px,1fr),340px]";

  function selectSource(nextSource: string): void {
    setSource(nextSource);
    setSelectedPath("");
    setFile(null);
  }

  function sendMessage(): void {
    const text = draft.trim();
    if (!text) return;
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text, timestamp: new Date().toISOString() },
      {
        role: "assistant",
        content: codingEcho(text, selectedPath, selectedContext),
        timestamp: new Date().toISOString(),
      },
    ]);
    setDraft("");
  }

  function patchMemory(index: number, patch: Partial<CodingMemoryItem>): void {
    setMemoryDrafts((prev) =>
      prev.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    );
  }

  async function saveMemory(): Promise<void> {
    const saved = await updateCodingMemoryItems(memoryDrafts);
    setMemoryDrafts(saved);
    setStatus("记忆已保存");
  }

  if (!workspace) {
    return (
      <section className="flex min-h-[520px] flex-1 items-center justify-center rounded border border-mars-border bg-mars-panel/35 text-sm text-slate-400">
        正在读取 Coding Agent 工作区…
      </section>
    );
  }

  return (
    <section className={`grid min-h-[640px] flex-1 gap-0 overflow-hidden rounded border border-mars-border bg-mars-bg ${workspaceGridClass}`}>
      {!codeSidebarCollapsed ? (
      <aside className="flex min-h-0 flex-col border-r border-mars-border bg-mars-panel/35">
        <div className="border-b border-mars-border p-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-100">项目代码</h2>
            <div className="flex items-center gap-2">
              <span className="rounded bg-mars-bg px-2 py-0.5 text-[10px] uppercase text-slate-400">
                {visibleFiles.length}
              </span>
              <SidebarToggleButton
                collapsed={codeSidebarCollapsed}
                side="left"
                label="项目代码边栏"
                onToggle={() => setCodeSidebarCollapsed((current) => !current)}
              />
            </div>
          </div>
          <select
            value={activeSource}
            onChange={(event) => selectSource(event.target.value)}
            className="mt-3 w-full rounded border border-mars-border bg-mars-bg px-2 py-2 text-xs text-slate-200 outline-none"
          >
            {workspace.sources.map((item) => (
              <option key={item.id} value={item.id} disabled={!item.exists}>
                {item.label}
                {item.exists ? "" : " (不可用)"}
              </option>
            ))}
          </select>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索文件"
            className="mt-2 w-full rounded border border-mars-border bg-mars-bg px-2 py-2 text-xs text-slate-200 outline-none placeholder:text-slate-600"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-2">
          {visibleFiles.length === 0 ? (
            <div className="rounded border border-dashed border-mars-border px-3 py-6 text-center text-xs text-slate-500">
              空项目
            </div>
          ) : null}
          {visibleFiles.map((item) => (
            <CodeTreeButton
              key={item.path}
              item={item}
              active={selectedPath === item.path}
              onSelect={() => {
                if (item.kind === "file") {
                  setSelectedPath(item.path);
                  setTab("preview");
                }
              }}
            />
          ))}
        </div>
      </aside>
      ) : null}

      <section className="flex min-h-0 flex-col">
        <div className="border-b border-mars-border px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-2">
                {codeSidebarCollapsed ? (
                  <SidebarToggleButton
                    collapsed={codeSidebarCollapsed}
                    side="left"
                    label="项目代码边栏"
                    onToggle={() => setCodeSidebarCollapsed((current) => !current)}
                  />
                ) : null}
                <h2 className="text-base font-semibold text-slate-100">Coding Agent</h2>
              </div>
              <p className="mt-0.5 text-xs text-slate-500">
                {selectedPath || "未选择文件"}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2 text-[11px] text-slate-400">
              <Metric label="context" value={workspace.upstream_context.length} />
              <Metric label="memory" value={memoryDrafts.filter((item) => item.enabled).length} />
              <Metric label="files" value={workspace.files.filter((item) => item.kind === "file").length} />
              {inspectorCollapsed ? (
                <SidebarToggleButton
                  collapsed={inspectorCollapsed}
                  side="right"
                  label="Inspector 边栏"
                  onToggle={() => setInspectorCollapsed((current) => !current)}
                />
              ) : null}
            </div>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
          <div className="mb-3 flex flex-wrap gap-2">
            {workspace.upstream_context.slice(0, 4).map((item) => (
              <button
                key={item.id}
                onClick={() => {
                  setSelectedContextId(item.id);
                  setTab("upstream");
                }}
                className="rounded border border-mars-border bg-mars-panel/50 px-2.5 py-1.5 text-left text-[11px] text-slate-300 hover:bg-mars-panel"
              >
                <span className="text-slate-500">{agentLabel(item.agent)}</span>{" "}
                <span>{item.title}</span>
              </button>
            ))}
          </div>

          <div className="space-y-3">
            {messages.map((message, index) => (
              <div
                key={`${message.timestamp}-${index}`}
                className={`max-w-[86%] rounded border px-3 py-2 text-sm leading-relaxed ${
                  message.role === "user"
                    ? "ml-auto border-mars-accent/40 bg-mars-accent/15 text-slate-100"
                    : "border-mars-border bg-mars-panel/45 text-slate-300"
                }`}
              >
                <pre className="whitespace-pre-wrap break-words font-sans">{message.content}</pre>
              </div>
            ))}
          </div>
        </div>

        <div className="border-t border-mars-border p-3">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                sendMessage();
              }
            }}
            placeholder="给 Coding Agent 的补充指令"
            className="min-h-[86px] w-full resize-none rounded border border-mars-border bg-mars-panel/40 p-3 text-sm leading-relaxed text-slate-100 outline-none placeholder:text-slate-600"
          />
          <div className="mt-2 flex items-center justify-between">
            <span className="text-[11px] text-slate-500">{status}</span>
            <button
              onClick={sendMessage}
              className="rounded bg-mars-accent px-4 py-1.5 text-sm font-medium text-white"
            >
              发送
            </button>
          </div>
        </div>
      </section>

      {!inspectorCollapsed ? (
      <aside className="flex min-h-0 flex-col border-l border-mars-border bg-mars-panel/30">
        <div className="border-b border-mars-border p-2">
          <div className="flex items-center gap-2">
            <div className="grid flex-1 grid-cols-4 gap-1 rounded border border-mars-border bg-mars-bg p-0.5 text-xs">
              {(["preview", "context", "memory", "upstream"] as const).map((item) => (
                <button
                  key={item}
                  onClick={() => setTab(item)}
                  className={`rounded px-2 py-1.5 ${
                    tab === item ? "bg-mars-accent text-white" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {tabLabel(item)}
                </button>
              ))}
            </div>
            <SidebarToggleButton
              collapsed={inspectorCollapsed}
              side="right"
              label="Inspector 边栏"
              onToggle={() => setInspectorCollapsed((current) => !current)}
            />
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-3">
          {tab === "preview" ? <PreviewPanel file={file} selectedPath={selectedPath} /> : null}
          {tab === "context" ? (
            <AgentContextPanel agent="coding" project={project} variant="compact" />
          ) : null}
          {tab === "memory" ? (
            <MemoryPanel
              memoryDrafts={memoryDrafts}
              kbMemory={workspace.kb_memory_items}
              onPatch={patchMemory}
              onAdd={() =>
                setMemoryDrafts((prev) => [
                  ...prev,
                  {
                    id: `custom_${Date.now()}`,
                    label: "New memory",
                    text: "",
                    enabled: true,
                    source: "custom",
                    editable: true,
                  },
                ])
              }
              onDelete={(index) => setMemoryDrafts((prev) => prev.filter((_, i) => i !== index))}
              onSave={() => void saveMemory()}
            />
          ) : null}
          {tab === "upstream" ? (
            <UpstreamPanel
              items={workspace.upstream_context}
              selectedId={selectedContextId}
              onSelect={setSelectedContextId}
              selected={selectedContext}
            />
          ) : null}
        </div>
      </aside>
      ) : null}
    </section>
  );
}

function CodeTreeButton({
  item,
  active,
  onSelect,
}: {
  item: CodeTreeItem;
  active: boolean;
  onSelect: () => void;
}): JSX.Element {
  const isFile = item.kind === "file";
  return (
    <button
      onClick={onSelect}
      disabled={!isFile}
      className={`mb-1 flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs ${
        active ? "bg-mars-accent/20 text-white" : "text-slate-300 hover:bg-mars-bg disabled:hover:bg-transparent"
      } ${isFile ? "" : "cursor-default text-slate-500"}`}
      style={{ paddingLeft: `${8 + item.depth * 14}px` }}
    >
      <span className="min-w-0 truncate font-mono">
        {isFile ? item.name : `${item.name}/`}
      </span>
      {isFile ? (
        <span className="ml-2 shrink-0 text-[10px] text-slate-500">{item.language}</span>
      ) : null}
    </button>
  );
}

function PreviewPanel({
  file,
  selectedPath,
}: {
  file: CodeFileContent | null;
  selectedPath: string;
}): JSX.Element {
  if (!selectedPath) {
    return (
      <div className="rounded border border-dashed border-mars-border px-3 py-10 text-center text-xs text-slate-500">
        请选择左侧文件
      </div>
    );
  }
  if (!file) {
    return (
      <div className="rounded border border-mars-border bg-mars-bg/60 px-3 py-4 text-xs text-slate-500">
        无法读取文件
      </div>
    );
  }
  return (
    <section className="flex min-h-full flex-col rounded border border-mars-border bg-mars-bg">
      <div className="border-b border-mars-border px-3 py-2">
        <h3 className="truncate font-mono text-xs font-semibold text-slate-100">{file.path}</h3>
        <p className="mt-0.5 text-[10px] text-slate-500">
          {file.language} · {file.size_chars} chars{file.truncated ? " · truncated" : ""}
        </p>
      </div>
      <pre className="min-h-[520px] flex-1 overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-[11px] leading-relaxed text-slate-300">
        {file.content}
      </pre>
    </section>
  );
}

function MemoryPanel({
  memoryDrafts,
  kbMemory,
  onPatch,
  onAdd,
  onDelete,
  onSave,
}: {
  memoryDrafts: CodingMemoryItem[];
  kbMemory: CodingMemoryItem[];
  onPatch: (index: number, patch: Partial<CodingMemoryItem>) => void;
  onAdd: () => void;
  onDelete: (index: number) => void;
  onSave: () => void;
}): JSX.Element {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-100">记忆管理</h3>
        <div className="flex gap-2">
          <button
            onClick={onAdd}
            className="rounded border border-mars-border px-2.5 py-1 text-xs text-slate-200 hover:bg-mars-bg"
          >
            新增
          </button>
          <button
            onClick={onSave}
            className="rounded bg-mars-accent px-2.5 py-1 text-xs font-medium text-white"
          >
            保存
          </button>
        </div>
      </div>
      {memoryDrafts.map((item, index) => (
        <div key={`${item.id}-${index}`} className="rounded border border-mars-border bg-mars-bg/70 p-2">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={item.enabled}
              onChange={(event) => onPatch(index, { enabled: event.target.checked })}
            />
            <input
              value={item.label}
              onChange={(event) => onPatch(index, { label: event.target.value })}
              className="min-w-0 flex-1 rounded border border-mars-border bg-mars-panel/40 px-2 py-1.5 text-xs text-slate-100 outline-none"
            />
            <button
              onClick={() => onDelete(index)}
              className="rounded border border-mars-border px-2 py-1.5 text-[11px] text-slate-300 hover:bg-mars-panel"
            >
              删除
            </button>
          </div>
          <textarea
            value={item.text}
            onChange={(event) => onPatch(index, { text: event.target.value })}
            className="mt-2 min-h-[96px] w-full resize-none rounded border border-mars-border bg-mars-panel/40 p-2 text-xs leading-relaxed text-slate-100 outline-none"
          />
        </div>
      ))}
      {kbMemory.length > 0 ? (
        <div className="space-y-2 border-t border-mars-border pt-3">
          <h4 className="text-xs font-semibold uppercase text-slate-500">KB Memory</h4>
          {kbMemory.map((item, index) => (
            <div key={`${item.id}-${index}`} className="rounded border border-mars-border bg-mars-bg/50 p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-medium text-slate-200">{item.label}</span>
                <span className="shrink-0 rounded bg-mars-panel px-1.5 py-0.5 text-[10px] text-slate-500">
                  {item.source}
                </span>
              </div>
              <p className="mt-1 line-clamp-4 text-[11px] leading-relaxed text-slate-500">
                {item.text}
              </p>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function UpstreamPanel({
  items,
  selectedId,
  onSelect,
  selected,
}: {
  items: UpstreamContextItem[];
  selectedId: string;
  onSelect: (id: string) => void;
  selected: UpstreamContextItem | null;
}): JSX.Element {
  if (items.length === 0) {
    return (
      <div className="rounded border border-dashed border-mars-border px-3 py-10 text-center text-xs text-slate-500">
        暂无上游上下文
      </div>
    );
  }
  return (
    <section className="flex min-h-full flex-col gap-3">
      <div className="space-y-1">
        {items.map((item) => (
          <button
            key={item.id}
            onClick={() => onSelect(item.id)}
            className={`w-full rounded px-2 py-2 text-left text-xs ${
              selectedId === item.id ? "bg-mars-accent/20 text-white" : "text-slate-300 hover:bg-mars-bg"
            }`}
          >
            <span className="block truncate font-medium">{item.title}</span>
            <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">
              {item.path}
            </span>
          </button>
        ))}
      </div>
      {selected ? (
        <pre className="min-h-[420px] flex-1 overflow-auto whitespace-pre-wrap break-words rounded border border-mars-border bg-mars-bg p-3 font-mono text-[11px] leading-relaxed text-slate-300">
          {selected.content}
        </pre>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number }): JSX.Element {
  return (
    <div className="rounded border border-mars-border bg-mars-bg px-2 py-1 text-center">
      <div className="font-mono text-xs text-slate-100">{value}</div>
      <div className="mt-0.5 text-[9px] uppercase text-slate-500">{label}</div>
    </div>
  );
}

function tabLabel(tab: InspectorTab): string {
  const labels: Record<InspectorTab, string> = {
    preview: "预览",
    context: "上下文",
    memory: "记忆",
    upstream: "输入",
  };
  return labels[tab];
}

function agentLabel(agent: string): string {
  const labels: Record<string, string> = {
    commander: "主Agent",
    idea: "Idea",
    experiment: "Experiment",
    system: "System",
  };
  return labels[agent] ?? agent;
}

function sourceLabel(sourceId: string, workspace: CodingWorkspace): string {
  return workspace.sources.find((item) => item.id === sourceId)?.label ?? sourceId;
}

function codingEcho(
  text: string,
  selectedPath: string,
  selectedContext: UpstreamContextItem | null,
): string {
  const lines = ["已记录补充指令。"];
  if (selectedPath) {
    lines.push(`当前关注文件：${selectedPath}`);
  }
  if (selectedContext) {
    lines.push(`当前参考输入：${selectedContext.title}`);
  }
  lines.push(`指令摘要：${text.slice(0, 180)}`);
  return lines.join("\n");
}
