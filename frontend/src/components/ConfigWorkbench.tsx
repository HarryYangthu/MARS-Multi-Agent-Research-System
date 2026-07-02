"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import {
  applyConfigFile,
  getAgentLlmConfig,
  getConfigSnapshot,
  previewConfigDiff,
  updateAgentLlmConfig,
  validateConfigFile,
  type AgentLlmConfigRow,
  type AgentLlmConfigView,
  type AgentLlmUpdateRow,
  type ConfigDiffResult,
  type ConfigFile,
  type ConfigSnapshot,
  type ConfigValidationResult,
} from "@/lib/api";

export type ConfigSection = "agents" | "yaml";
type AgentDraft = AgentLlmConfigRow & { apiKey: string };

export function ConfigWorkbench({ section }: { section: ConfigSection }): JSX.Element {
  const [snapshot, setSnapshot] = useState<ConfigSnapshot | null>(null);
  const [agentConfig, setAgentConfig] = useState<AgentLlmConfigView | null>(null);
  const [agentDrafts, setAgentDrafts] = useState<AgentDraft[]>([]);
  const [activeName, setActiveName] = useState("agents");
  const [draft, setDraft] = useState("");
  const [validation, setValidation] = useState<ConfigValidationResult | null>(null);
  const [diff, setDiff] = useState<ConfigDiffResult | null>(null);
  const [confirmHighRisk, setConfirmHighRisk] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let alive = true;
    void Promise.all([getConfigSnapshot(), getAgentLlmConfig()])
      .then(([nextSnapshot, nextAgentConfig]) => {
        if (!alive) return;
        setSnapshot(nextSnapshot);
        setAgentConfig(nextAgentConfig);
        setAgentDrafts(toDrafts(nextAgentConfig.agents));
        const first =
          nextSnapshot.files.find((file) => file.name === "agents") ??
          nextSnapshot.files[0];
        if (first) {
          setActiveName(first.name);
          setDraft(first.text);
        }
      })
      .catch((error) => {
        if (alive) {
          setMessage(error instanceof Error ? error.message : "config load failed");
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  const active = useMemo(
    () => snapshot?.files.find((file) => file.name === activeName) ?? null,
    [activeName, snapshot],
  );

  const agentChanged = useMemo(() => {
    if (!agentConfig) return false;
    const original = new Map(
      agentConfig.agents.map((row) => [row.agent, comparableAgent(row)]),
    );
    return agentDrafts.some(
      (row) => row.apiKey.trim() || original.get(row.agent) !== comparableAgent(row),
    );
  }, [agentConfig, agentDrafts]);

  function selectFile(file: ConfigFile): void {
    setActiveName(file.name);
    setDraft(file.text);
    setValidation(null);
    setDiff(null);
    setConfirmHighRisk(false);
    setMessage("");
  }

  function updateAgentDraft(agent: string, patch: Partial<AgentDraft>): void {
    setAgentDrafts((rows) =>
      rows.map((row) => (row.agent === agent ? { ...row, ...patch } : row)),
    );
  }

  async function saveAgentConfig(): Promise<void> {
    const next = await updateAgentLlmConfig({
      actor: "frontend",
      agents: agentDrafts.map(toUpdateRow),
    });
    const refreshed = await getConfigSnapshot();
    setAgentConfig(next);
    setAgentDrafts(toDrafts(next.agents));
    setSnapshot(refreshed);
    const agentsFile = refreshed.files.find((file) => file.name === activeName);
    if (agentsFile) setDraft(agentsFile.text);
    setMessage(`已保存 Agent 模型配置，密钥写入 ${next.secrets_path}`);
  }

  async function validate(): Promise<void> {
    const result = await validateConfigFile(activeName, draft);
    setValidation(result);
    setMessage(result.valid ? "校验通过" : "校验失败");
  }

  async function preview(): Promise<void> {
    const result = await previewConfigDiff(activeName, draft);
    setDiff(result);
    setValidation({ valid: result.valid, errors: result.errors, data: {} });
    setMessage(result.valid ? "Diff 已生成" : "配置未通过校验");
  }

  async function apply(): Promise<void> {
    const next = await applyConfigFile({
      name: activeName,
      text: draft,
      confirmHighRisk,
      actor: "frontend",
    });
    const refreshed = await getConfigSnapshot();
    const nextAgentConfig = await getAgentLlmConfig();
    setSnapshot(refreshed);
    setAgentConfig(nextAgentConfig);
    setAgentDrafts(toDrafts(nextAgentConfig.agents));
    setDraft(next.text);
    setDiff(null);
    setValidation(null);
    setMessage(`已保存 ${next.path}，audit event 已写入`);
  }

  const topLevelKeys = active ? Object.keys(active.data) : [];
  const changed = active ? active.text !== draft : false;

  return (
    <main className="grid min-h-screen grid-cols-[280px,1fr] bg-mars-bg text-slate-100">
      <aside className="border-r border-mars-border bg-mars-panel/70 p-4">
        <Link
          href="/"
          className="mb-4 flex items-center justify-center rounded border border-mars-border bg-mars-panel2 px-3 py-2 text-sm font-medium text-slate-200 hover:bg-mars-subtle hover:text-white"
        >
          ← 返回实验台
        </Link>

        <Link href="/config" className="text-base font-semibold hover:text-white">
          配置
        </Link>
        <p className="mt-1 text-xs text-slate-500">模型密钥、YAML、运行环境</p>

        <div className="mt-5 space-y-1">
          <ConfigNavLink active={section === "agents"} href="/config/agents" badge="local">
            Agent 模型与 Key
          </ConfigNavLink>
          <ConfigNavLink active={section === "yaml"} href="/config/yaml" badge="audit">
            YAML 高级编辑
          </ConfigNavLink>
        </div>

        {section === "yaml" ? (
          <nav className="mt-6 space-y-1 border-t border-mars-border pt-4">
            {(snapshot?.files ?? []).map((file) => (
              <button
                key={file.name}
                onClick={() => selectFile(file)}
                className={`flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm ${
                  activeName === file.name
                    ? "bg-mars-accent/20 text-white"
                    : "hover:bg-mars-panel2"
                }`}
              >
                <span>{file.name}</span>
                {file.high_risk ? (
                  <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-100">
                    high
                  </span>
                ) : null}
              </button>
            ))}
          </nav>
        ) : null}
      </aside>

      <section className="min-w-0 p-4">
        {message ? (
          <p className="mb-3 rounded border border-mars-border bg-mars-panel px-3 py-2 text-sm text-slate-300">
            {message}
          </p>
        ) : null}

        {section === "agents" ? (
          <AgentModelPanel
            config={agentConfig}
            drafts={agentDrafts}
            changed={agentChanged}
            onUpdate={updateAgentDraft}
            onSave={saveAgentConfig}
          />
        ) : (
          <YamlConfigPanel
            activeName={activeName}
            active={active}
            draft={draft}
            validation={validation}
            diff={diff}
            confirmHighRisk={confirmHighRisk}
            changed={changed}
            topLevelKeys={topLevelKeys}
            onDraftChange={setDraft}
            onConfirmHighRiskChange={setConfirmHighRisk}
            onValidate={validate}
            onPreview={preview}
            onApply={apply}
          />
        )}
      </section>
    </main>
  );
}

function ConfigNavLink({
  active,
  href,
  badge,
  children,
}: {
  active: boolean;
  href: string;
  badge: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <Link
      href={href}
      className={`flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm ${
        active ? "bg-mars-accent/20 text-white" : "hover:bg-mars-panel2"
      }`}
    >
      <span>{children}</span>
      <span
        className={`rounded px-1.5 py-0.5 text-[10px] ${
          badge === "audit"
            ? "bg-amber-500/10 text-amber-100"
            : "bg-emerald-500/10 text-emerald-100"
        }`}
      >
        {badge}
      </span>
    </Link>
  );
}

function AgentModelPanel({
  config,
  drafts,
  changed,
  onUpdate,
  onSave,
}: {
  config: AgentLlmConfigView | null;
  drafts: AgentDraft[];
  changed: boolean;
  onUpdate: (agent: string, patch: Partial<AgentDraft>) => void;
  onSave: () => Promise<void>;
}): JSX.Element {
  const providers = config?.providers ?? [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Agent 模型与 Key</h1>
          <p className="mt-1 text-xs text-slate-500">
            模型路由保存到 configs/agents.yaml，密钥只保存到{" "}
            {config?.secrets_path ?? ".env.local"}。
          </p>
        </div>
        <button
          onClick={() => void onSave()}
          disabled={!changed}
          className="rounded bg-mars-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          保存 Agent 配置
        </button>
      </div>

      <div className="grid gap-3">
        {drafts.map((row) => {
          const defaults = config?.provider_defaults[row.provider];
          return (
            <section
              key={row.agent}
              className="rounded border border-mars-border bg-mars-panel/60 p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-base font-semibold capitalize">
                      {row.agent} Agent
                    </h2>
                    <span
                      className={`rounded border px-2 py-0.5 text-[11px] ${
                        row.api_key_configured
                          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
                          : "border-amber-500/40 bg-amber-500/10 text-amber-100"
                      }`}
                    >
                      {row.api_key_configured ? "key 已配置" : "key 未配置"}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {row.provider} / {row.model}
                  </p>
                </div>
                <label className="flex items-center gap-2 text-xs text-slate-300">
                  <input
                    type="checkbox"
                    checked={row.enabled}
                    onChange={(event) =>
                      onUpdate(row.agent, { enabled: event.target.checked })
                    }
                  />
                  enabled
                </label>
              </div>

              <div className="mt-4 grid gap-3 lg:grid-cols-4">
                <label className="space-y-1">
                  <span className="text-xs text-slate-500">provider</span>
                  <select
                    value={row.provider}
                    onChange={(event) => {
                      const provider = event.target.value;
                      const nextDefault = config?.provider_defaults[provider];
                      onUpdate(row.agent, {
                        provider,
                        api_key_env: nextDefault?.api_key_env ?? row.api_key_env,
                        base_url: nextDefault?.base_url ?? "",
                        base_url_env: nextDefault?.base_url_env ?? "",
                      });
                    }}
                    className="w-full rounded border border-mars-border bg-black/30 px-3 py-2 text-sm outline-none focus:border-mars-accent"
                  >
                    {providers.map((provider) => (
                      <option key={provider} value={provider}>
                        {provider}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="space-y-1 lg:col-span-2">
                  <span className="text-xs text-slate-500">model</span>
                  <input
                    value={row.model}
                    onChange={(event) =>
                      onUpdate(row.agent, { model: event.target.value })
                    }
                    className="w-full rounded border border-mars-border bg-black/30 px-3 py-2 font-mono text-sm outline-none focus:border-mars-accent"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-xs text-slate-500">api_key_env</span>
                  <input
                    value={row.api_key_env}
                    placeholder={defaults?.api_key_env || "AGENT_API_KEY"}
                    onChange={(event) =>
                      onUpdate(row.agent, { api_key_env: event.target.value })
                    }
                    className="w-full rounded border border-mars-border bg-black/30 px-3 py-2 font-mono text-sm outline-none focus:border-mars-accent"
                  />
                </label>
              </div>

              <div className="mt-3 grid gap-3 lg:grid-cols-[1.2fr,0.7fr,0.7fr]">
                <label className="space-y-1">
                  <span className="text-xs text-slate-500">API key</span>
                  <input
                    type="password"
                    value={row.apiKey}
                    placeholder={row.api_key_configured ? "保持当前密钥" : "粘贴新密钥"}
                    onChange={(event) =>
                      onUpdate(row.agent, { apiKey: event.target.value })
                    }
                    className="w-full rounded border border-mars-border bg-black/30 px-3 py-2 font-mono text-sm outline-none focus:border-mars-accent"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-xs text-slate-500">temperature</span>
                  <input
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={row.temperature}
                    onChange={(event) =>
                      onUpdate(row.agent, { temperature: Number(event.target.value) })
                    }
                    className="w-full rounded border border-mars-border bg-black/30 px-3 py-2 font-mono text-sm outline-none focus:border-mars-accent"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-xs text-slate-500">max tokens</span>
                  <input
                    type="number"
                    min={1}
                    value={row.max_tokens}
                    onChange={(event) =>
                      onUpdate(row.agent, { max_tokens: Number(event.target.value) })
                    }
                    className="w-full rounded border border-mars-border bg-black/30 px-3 py-2 font-mono text-sm outline-none focus:border-mars-accent"
                  />
                </label>
              </div>

              <details className="mt-3 rounded border border-mars-border bg-black/20 px-3 py-2">
                <summary className="cursor-pointer text-xs text-slate-400">
                  Endpoint / base URL
                </summary>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                  <label className="space-y-1">
                    <span className="text-xs text-slate-500">base_url</span>
                    <input
                      value={row.base_url}
                      placeholder={defaults?.base_url || "https://..."}
                      onChange={(event) =>
                        onUpdate(row.agent, { base_url: event.target.value })
                      }
                      className="w-full rounded border border-mars-border bg-black/30 px-3 py-2 font-mono text-sm outline-none focus:border-mars-accent"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs text-slate-500">base_url_env</span>
                    <input
                      value={row.base_url_env}
                      placeholder={defaults?.base_url_env || "AGENT_BASE_URL"}
                      onChange={(event) =>
                        onUpdate(row.agent, { base_url_env: event.target.value })
                      }
                      className="w-full rounded border border-mars-border bg-black/30 px-3 py-2 font-mono text-sm outline-none focus:border-mars-accent"
                    />
                  </label>
                </div>
              </details>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function YamlConfigPanel({
  activeName,
  active,
  draft,
  validation,
  diff,
  confirmHighRisk,
  changed,
  topLevelKeys,
  onDraftChange,
  onConfirmHighRiskChange,
  onValidate,
  onPreview,
  onApply,
}: {
  activeName: string;
  active: ConfigFile | null;
  draft: string;
  validation: ConfigValidationResult | null;
  diff: ConfigDiffResult | null;
  confirmHighRisk: boolean;
  changed: boolean;
  topLevelKeys: string[];
  onDraftChange: (value: string) => void;
  onConfirmHighRiskChange: (value: boolean) => void;
  onValidate: () => Promise<void>;
  onPreview: () => Promise<void>;
  onApply: () => Promise<void>;
}): JSX.Element {
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{active?.path ?? activeName}</h1>
          <p className="mt-1 text-xs text-slate-500">
            保存只影响新 run；已运行任务继续使用创建时的配置快照。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => void onValidate()}
            className="rounded border border-mars-border px-3 py-1.5 text-sm hover:bg-mars-panel"
          >
            校验
          </button>
          <button
            onClick={() => void onPreview()}
            className="rounded border border-mars-border px-3 py-1.5 text-sm hover:bg-mars-panel"
          >
            预览 Diff
          </button>
          <button
            onClick={() => void onApply()}
            disabled={!changed || (active?.high_risk && !confirmHighRisk)}
            className="rounded bg-mars-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr,420px]">
        <div className="space-y-3">
          <div className="rounded border border-mars-border bg-mars-panel/60 p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-wider text-slate-500">
                  受控编辑
                </p>
                <p className="mt-1 text-sm text-slate-300">
                  Top-level keys: {topLevelKeys.join(", ") || "empty"}
                </p>
              </div>
              {active?.high_risk ? (
                <label className="flex items-center gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-100">
                  <input
                    type="checkbox"
                    checked={confirmHighRisk}
                    onChange={(event) => onConfirmHighRiskChange(event.target.checked)}
                  />
                  确认高风险变更
                </label>
              ) : null}
            </div>
            <textarea
              value={draft}
              onChange={(event) => onDraftChange(event.target.value)}
              spellCheck={false}
              className="mt-3 min-h-[58vh] w-full resize-y rounded border border-mars-border bg-black/30 p-3 font-mono text-xs leading-relaxed text-slate-200 outline-none focus:border-mars-accent"
            />
          </div>
        </div>

        <aside className="space-y-3">
          <div className="rounded border border-mars-border bg-mars-panel/60 p-3">
            <h2 className="text-sm font-semibold">校验状态</h2>
            {validation ? (
              <div className="mt-2">
                <span
                  className={`rounded border px-2 py-1 text-xs ${
                    validation.valid
                      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
                      : "border-red-500/40 bg-red-500/10 text-red-100"
                  }`}
                >
                  {validation.valid ? "valid" : "invalid"}
                </span>
                <ul className="mt-2 space-y-1 text-xs text-red-200">
                  {validation.errors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="mt-2 text-xs text-slate-500">尚未校验</p>
            )}
          </div>
          <div className="rounded border border-mars-border bg-mars-panel/60 p-3">
            <h2 className="text-sm font-semibold">YAML Diff</h2>
            <pre className="mt-2 max-h-[52vh] overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-[11px] leading-relaxed text-slate-300">
              {diff?.diff || "点击“预览 Diff”查看变更。"}
            </pre>
          </div>
        </aside>
      </div>
    </>
  );
}

function toDrafts(rows: AgentLlmConfigRow[]): AgentDraft[] {
  return rows.map((row) => ({ ...row, apiKey: "" }));
}

function toUpdateRow(row: AgentDraft): AgentLlmUpdateRow {
  return {
    agent: row.agent,
    enabled: row.enabled,
    provider: row.provider,
    model: row.model,
    temperature: row.temperature,
    max_tokens: row.max_tokens,
    api_key_env: row.api_key_env,
    api_key: row.apiKey.trim() || undefined,
    base_url: row.base_url,
    base_url_env: row.base_url_env,
  };
}

function comparableAgent(row: AgentLlmConfigRow): string {
  return JSON.stringify({
    enabled: row.enabled,
    provider: row.provider,
    model: row.model,
    temperature: row.temperature,
    max_tokens: row.max_tokens,
    api_key_env: row.api_key_env,
    base_url: row.base_url,
    base_url_env: row.base_url_env,
  });
}
