"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getAgentLlmConfig, updateAgentLlmConfig, type AgentLlmConfigView } from "@/lib/api";

const INPUT = "mt-2 w-full rounded-lg border border-mars-border bg-mars-bg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-400";
const BUTTON = "rounded-lg border border-mars-border px-4 py-2 text-sm hover:bg-mars-panel2 disabled:opacity-40";

export function OnboardingModelSetup({ onStatus }: { onStatus: (saved: boolean) => void }): JSX.Element {
  const [config, setConfig] = useState<AgentLlmConfigView | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    void getAgentLlmConfig().then((value) => {
      if (!active) return;
      setConfig(value); setError("");
      const first = value.agents.find((row) => row.agent === "idea_author") || value.agents.find((row) => row.enabled);
      setProvider(first?.provider || ""); setModel(first?.model || "");
      setUrl(first?.base_url || value.provider_defaults[first?.provider || ""]?.base_url || "");
      const enabled = value.agents.filter((row) => row.enabled);
      onStatus(enabled.length > 0 && enabled.every((row) => row.api_key_configured && !!row.model));
    }).catch(() => { if (active) setError("暂时无法读取模型配置，请检查本地服务后重试。"); });
    return () => { active = false; };
  }, [revision, onStatus]);
  const enabled = config?.agents.filter((row) => row.enabled) || [];
  const savedCredential = config?.provider_defaults[provider]?.configured;
  async function save(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!config || busy) return;
    const defaults = config.provider_defaults[provider];
    if (!defaults?.api_key_env) { setError("此服务需要高级连接设置，请使用下方的逐角色配置。"); return; }
    if (!key.trim() && !savedCredential) { setError("请填写服务商提供的 API Key。"); return; }
    setBusy(true); setError(""); setMessage("");
    try {
      const value = await updateAgentLlmConfig({ actor: "onboarding", agents: enabled.map((row, index) => ({
        agent: row.agent, enabled: row.enabled, provider, model: model.trim(), temperature: row.temperature,
        max_tokens: row.max_tokens, api_key_env: defaults.api_key_env, base_url: url.trim(), base_url_env: "",
        ...(index === 0 && key.trim() ? { api_key: key.trim() } : {}),
      })) });
      setConfig(value); setKey("");
      onStatus(value.agents.filter((row) => row.enabled).every((row) => row.api_key_configured && !!row.model));
      setMessage("模型配置已保存。密钥已交给本地服务保存，输入框已清空；尚未进行模型连接验证。");
    } catch { setError("保存未能确认，请重新读取配置核对。API Key 不会写入浏览器存储。"); }
    finally { setBusy(false); }
  }
  return <div className="space-y-4">
    <p className="text-sm leading-6 text-slate-400">先连接你要使用的模型服务。默认配置是智谱 GLM-5.3；从服务商控制台获取 API Key 后在这里填写。</p>
    {config ? <form onSubmit={(event) => void save(event)} className="space-y-4">
      <fieldset disabled={busy} className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm">服务商<select className={INPUT} value={provider} onChange={(event) => { const next = event.target.value; setProvider(next); setUrl(config.provider_defaults[next]?.base_url || ""); setModel(""); setKey(""); setMessage(""); }}>{config.providers.map((item) => <option key={item} value={item}>{item === "zhipu" ? "智谱（GLM）" : item}</option>)}</select></label>
        <label className="text-sm">模型名称<input required className={INPUT} value={model} onChange={(event) => setModel(event.target.value)} placeholder="例如 glm-5.3" /></label>
        <label className="text-sm sm:col-span-2">API 地址<input required type="url" className={INPUT} value={url} onChange={(event) => setUrl(event.target.value)} placeholder="使用服务商提供的 API Base URL" /></label>
        <label className="text-sm sm:col-span-2">API Key<input type="password" autoComplete="off" spellCheck={false} className={INPUT} value={key} onChange={(event) => setKey(event.target.value)} placeholder={savedCredential ? "已保存密钥，留空继续使用；填写则替换" : "粘贴你的 API Key"} /></label>
      </fieldset>
      <p className="text-xs leading-5 text-slate-400">保存会将以上连接应用到当前 {enabled.length} 个已启用的研究角色，保留各角色的其他参数。配置保存在本机，API 调用时会向你选择的服务商发送研究内容。</p>
      <div className="flex flex-wrap gap-3"><button className={`${BUTTON} bg-mars-accent text-white`} disabled={busy || !enabled.length}>{busy ? "正在保存…" : "保存 API 配置"}</button><Link className={BUTTON} href="/config/agents">逐角色高级配置</Link></div>
    </form> : !error ? <p role="status">正在读取模型配置…</p> : null}
    {message ? <p role="status" className="text-sm text-emerald-200">{message}</p> : null}
    {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
    <button type="button" disabled={busy} className="text-sm text-indigo-200 underline underline-offset-4" onClick={() => { setRevision((value) => value + 1); setKey(""); }}>重新读取已保存的配置</button>
  </div>;
}
