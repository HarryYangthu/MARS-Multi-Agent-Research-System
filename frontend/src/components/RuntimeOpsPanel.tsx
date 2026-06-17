"use client";

import { useEffect, useState } from "react";

import {
  getRuntimeStatus,
  type GpuDevice,
  type RuntimeStatus,
} from "@/lib/api";

const REFRESH_MS = 5000;

export function RuntimeOpsPanel({ project }: { project?: string }): JSX.Element {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    const refresh = (): void => {
      void getRuntimeStatus(project)
        .then((next) => {
          if (!alive) return;
          setStatus(next);
          setError("");
        })
        .catch((caught: unknown) => {
          if (!alive) return;
          setError(caught instanceof Error ? caught.message : "运行态不可用");
        });
    };
    refresh();
    const interval = setInterval(refresh, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [open, project]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="rounded border border-mars-border bg-mars-panel2 px-2.5 py-1 text-xs text-slate-200 hover:bg-mars-subtle hover:text-white"
        title="运行态运维"
      >
        运维
      </button>
      {open ? (
        <div className="fixed inset-0 z-50">
          <button
            className="absolute inset-0 bg-black/50"
            aria-label="关闭运行态运维面板"
            onClick={() => setOpen(false)}
          />
          <aside className="absolute right-0 top-0 flex h-full w-full max-w-3xl flex-col border-l border-mars-border bg-mars-panel shadow-2xl">
            <header className="flex items-center justify-between border-b border-mars-border px-5 py-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-100">运行态运维</h2>
                <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                  {status ? `${status.project} · ${status.generated_at.slice(0, 19)}` : "加载中"}
                </p>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="rounded border border-mars-border px-2 py-1 text-xs text-slate-300 hover:bg-mars-subtle"
              >
                关闭
              </button>
            </header>

            <div className="flex-1 overflow-auto px-5 py-4">
              {error ? (
                <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                  {error}
                </div>
              ) : null}
              {status ? (
                <div className="space-y-4">
                  <GpuResourcePanel status={status} />
                  <LangSmithPanel status={status} />
                  <AdvancedConfigPanel status={status} />
                </div>
              ) : (
                <div className="rounded border border-mars-border bg-mars-bg/50 p-4 text-sm text-slate-400">
                  正在读取运行态状态...
                </div>
              )}
            </div>
          </aside>
        </div>
      ) : null}
    </>
  );
}

function GpuResourcePanel({ status }: { status: RuntimeStatus }): JSX.Element {
  const gpu = status.resources.gpu;
  const execution = status.resources.execution;
  const memoryPercent =
    gpu.summary.memory_total_mb > 0
      ? Math.round((gpu.summary.memory_used_mb / gpu.summary.memory_total_mb) * 100)
      : 0;
  return (
    <section className="rounded border border-mars-border bg-mars-bg/40 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold text-slate-400">GPU 资源</h3>
          <p className="mt-0.5 text-xs text-slate-500">{gpu.message}</p>
        </div>
        <span className={gpu.available ? badgeClass("ok") : badgeClass("muted")}>
          {gpu.available ? `检测到 ${gpu.summary.count} 张` : "降级模式"}
        </span>
      </div>
      <div className="grid gap-2 md:grid-cols-4">
        <MetricCell label="后端" value={execution.backend} />
        <MetricCell label="mock" value={execution.mock_mode} />
        <MetricCell label="并发" value={String(execution.max_concurrency)} />
        <MetricCell label="显存" value={`${formatMb(gpu.summary.memory_used_mb)} / ${formatMb(gpu.summary.memory_total_mb)}`} />
      </div>
      <div className="mt-3 h-1.5 rounded bg-mars-panel2">
        <div
          className="h-1.5 rounded bg-cyan-400"
          style={{ width: `${Math.min(100, memoryPercent)}%` }}
        />
      </div>
      {gpu.devices.length > 0 ? (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {gpu.devices.map((device) => (
            <GpuDeviceRow key={device.index} device={device} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function GpuDeviceRow({ device }: { device: GpuDevice }): JSX.Element {
  const memoryPercent =
    device.memory_total_mb > 0
      ? Math.round((device.memory_used_mb / device.memory_total_mb) * 100)
      : 0;
  return (
    <div className="rounded border border-mars-border bg-mars-panel/50 p-2">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-slate-200">
          GPU {device.index} · {device.name}
        </span>
        <span className="font-mono text-[10px] text-cyan-200">
          {device.utilization_gpu_percent}%
        </span>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
        <MetricCell label="显存" value={`${memoryPercent}%`} compact />
        <MetricCell label="温度" value={`${device.temperature_c}C`} compact />
        <MetricCell label="功耗" value={`${device.power_draw_w}W`} compact />
      </div>
    </div>
  );
}

function LangSmithPanel({ status }: { status: RuntimeStatus }): JSX.Element {
  const langsmith = status.observability.langsmith;
  return (
    <section className="rounded border border-mars-border bg-mars-bg/40 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold text-slate-400">LangSmith 追踪</h3>
          <p className="mt-0.5 text-xs text-slate-500">{langsmith.message}</p>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={langsmith.configured ? badgeClass("ok") : badgeClass("muted")}>
            {langsmith.configured ? "已配置" : "未启用"}
          </span>
          {langsmith.ui_url ? (
            <a
              href={langsmith.ui_url}
              target="_blank"
              rel="noreferrer"
              className="rounded border border-mars-border px-2 py-1 text-[10px] text-slate-200 hover:bg-mars-subtle"
            >
              打开
            </a>
          ) : null}
        </div>
      </div>
      <div className="grid gap-2 md:grid-cols-3">
        <MetricCell label="项目" value={langsmith.project} />
        <MetricCell label="包状态" value={langsmith.package_available ? "可用" : "缺失"} />
        <MetricCell label="超时" value={`${langsmith.timeout_ms}ms`} />
      </div>
      {langsmith.embed_url ? (
        <iframe
          title="LangSmith"
          src={langsmith.embed_url}
          className="mt-3 h-64 w-full rounded border border-mars-border bg-white"
          sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
        />
      ) : (
        <div className="mt-3 rounded border border-dashed border-mars-border bg-mars-panel/30 p-3 text-xs text-slate-500">
          文件追踪清单仍然启用：{status.observability.tracing.manifest_path}
        </div>
      )}
    </section>
  );
}

function AdvancedConfigPanel({ status }: { status: RuntimeStatus }): JSX.Element {
  const runtime = status.config.runtime;
  const tools = status.config.tools;
  const context = status.config.context;
  const llm = status.config.llm;
  const mcpRows = Object.entries(status.config.mcp);
  const secretRows = Object.entries(llm.secrets_configured);
  return (
    <section className="rounded border border-mars-border bg-mars-bg/40 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-slate-400">高级配置</h3>
        <span className={status.readiness.ready ? badgeClass("ok") : badgeClass("warn")}>
          {status.readiness.ready ? "就绪" : "检查"}
        </span>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <ConfigGroup
          title="运行时"
          rows={[
            ["模式", runtime.mode],
            ["mock", runtime.mock_mode],
            ["默认项目", runtime.default_project],
            ["LLM 超时", `${runtime.llm_timeout_seconds}s`],
          ]}
        />
        <ConfigGroup
          title="工具"
          rows={[
            ["启用数", `${tools.enabled}/${tools.total}`],
            ["网络工具", tools.network_runtime_enabled ? "启用" : "关闭"],
            ["搜索 provider", tools.web_search_provider || "无"],
            ["网络工具数", String(tools.network_defined)],
          ]}
        />
        <ConfigGroup
          title="上下文"
          rows={[
            ["预算", `${context.target_tokens}/${context.max_tokens}`],
            ["压缩", boolText(context.auto_compress)],
            ["raw 引用", boolText(context.tool_raw_externalize)],
            ["工作台", boolText(context.workbench_enabled)],
          ]}
        />
        <ConfigGroup
          title="执行"
          rows={[
            ["步数", String(status.resources.execution.batch_steps)],
            ["超时", `${status.resources.execution.command_timeout_seconds}s`],
            ["本地命令", String(status.resources.execution.local_command_count)],
            ["远程 GPU", boolText(status.resources.execution.remote_gpu.configured)],
          ]}
        />
      </div>
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <FlagGrid title="Provider 密钥" rows={secretRows} />
        <FlagGrid title="MCP 适配器" rows={mcpRows} />
      </div>
    </section>
  );
}

function ConfigGroup({
  title,
  rows,
}: {
  title: string;
  rows: [string, string][];
}): JSX.Element {
  return (
    <div className="rounded border border-mars-border bg-mars-panel/40 p-2">
      <h4 className="mb-1 text-[11px] font-semibold uppercase text-slate-500">{title}</h4>
      <dl className="space-y-1">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-2 text-[11px]">
            <dt className="text-slate-500">{label}</dt>
            <dd className="truncate font-mono text-slate-200">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function FlagGrid({
  title,
  rows,
}: {
  title: string;
  rows: [string, boolean][];
}): JSX.Element {
  return (
    <div className="rounded border border-mars-border bg-mars-panel/40 p-2">
      <h4 className="mb-2 text-[11px] font-semibold uppercase text-slate-500">{title}</h4>
      <div className="grid gap-1.5 sm:grid-cols-2">
        {rows.map(([label, enabled]) => (
          <div key={label} className="flex items-center justify-between gap-2 rounded bg-mars-bg/50 px-2 py-1 text-[11px]">
            <span className="truncate text-slate-400">{label}</span>
            <span className={enabled ? badgeClass("ok") : badgeClass("muted")}>
              {enabled ? "是" : "否"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MetricCell({
  label,
  value,
  compact = false,
}: {
  label: string;
  value: string;
  compact?: boolean;
}): JSX.Element {
  return (
    <div className={compact ? "rounded bg-mars-bg/50 px-2 py-1" : "rounded border border-mars-border bg-mars-panel/40 px-2 py-1.5"}>
      <div className="text-[10px] uppercase text-slate-500">{label}</div>
      <div className="truncate font-mono text-xs text-slate-200">{value}</div>
    </div>
  );
}

function badgeClass(tone: "ok" | "warn" | "muted"): string {
  const color =
    tone === "ok"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
      : tone === "warn"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
        : "border-slate-600 bg-slate-700/40 text-slate-300";
  return `rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase ${color}`;
}

function boolText(value: boolean): string {
  return value ? "启用" : "关闭";
}

function formatMb(value: number): string {
  if (value <= 0) return "0 GB";
  return `${(value / 1024).toFixed(1)} GB`;
}
