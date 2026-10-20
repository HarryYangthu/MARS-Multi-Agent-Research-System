"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "@/lib/i18n";
import { getGlobalModelProfiles, saveGlobalModelProfile, setConversationModel,
  type Conversation, type GlobalModelProfile, type SaveGlobalModelProfile } from "@/lib/api";

const blank: SaveGlobalModelProfile = { name: "", provider: "custom", model: "", base_url: "", api_key: "",
  context_window: 200000, compatibility: "qwen_200k" };
const fieldClass = "mt-1 w-full rounded-lg border border-mars-border bg-mars-bg px-3 py-2 text-sm text-slate-100";

export function ConversationModelPicker({ conversationId, selectedId, disabled, onChanged, onBusyChange }: {
  conversationId: string; selectedId: string; disabled: boolean;
  onChanged: (value: Conversation) => void; onBusyChange: (busy: boolean) => void;
}): JSX.Element {
  const { ui } = useI18n();
  const id = useId();
  const [profiles, setProfiles] = useState<GlobalModelProfile[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState<SaveGlobalModelProfile | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(true);
  const inFlight = useRef(false);
  const opener = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    alive.current = true;
    let active = true;
    void getGlobalModelProfiles().then(value => { if (active) { setProfiles(value.profiles); setLoaded(true); } })
      .catch(() => { if (active) setError(ui("模型列表读取失败，请重新读取。")); });
    return () => { active = false; alive.current = false; };
  }, [ui]);

  useEffect(() => {
    const element = dialog.current;
    if (form && element && !element.open) element.showModal();
  }, [form]);

  function close(): void {
    if (busy) return;
    setForm(null); setError(""); opener.current?.focus();
  }

  async function choose(value: string): Promise<void> {
    if (disabled || inFlight.current) return;
    if (value === "__new__") { setError(""); setForm({ ...blank }); return; }
    inFlight.current = true; setBusy(true); onBusyChange(true); setError("");
    try {
      const updated = await setConversationModel(conversationId, value);
      if (alive.current) onChanged(updated);
    } catch (cause: unknown) {
      if (alive.current) setError(cause instanceof Error ? cause.message : ui("模型选择未能保存，请重试。"));
    } finally {
      inFlight.current = false; onBusyChange(false); if (alive.current) setBusy(false);
    }
  }

  async function save(): Promise<void> {
    if (!form || inFlight.current) return;
    inFlight.current = true; setBusy(true); onBusyChange(true); setError("");
    try {
      const saved = await saveGlobalModelProfile(form);
      if (!alive.current) return;
      setProfiles(current => [...current.filter(item => item.id !== saved.id), saved]);
      const updated = await setConversationModel(conversationId, saved.id);
      if (alive.current) { onChanged(updated); setForm(null); opener.current?.focus(); }
    } catch (cause: unknown) {
      if (alive.current) setError(cause instanceof Error ? cause.message : ui("模型配置未能保存，请重试。"));
    } finally {
      inFlight.current = false; onBusyChange(false); if (alive.current) setBusy(false);
    }
  }

  const selected = profiles.find(item => item.id === selectedId);
  return <>
    <div className="mb-2 flex flex-wrap items-center gap-2 border-b border-mars-border px-2 pb-2">
      <label htmlFor={`${id}-pick`} className="text-xs text-slate-400">{ui("对话模型")}</label>
      <select ref={opener} id={`${id}-pick`} value={selectedId} disabled={disabled || busy || !loaded}
        onChange={event => void choose(event.target.value)} className="max-w-[18rem] rounded-lg border border-mars-border bg-mars-bg px-2 py-1.5 text-xs text-slate-200 disabled:opacity-50">
        <option value="">{ui("默认配置")}</option>
        {selectedId && !selected ? <option value={selectedId}>{ui("已选模型配置暂不可用")}</option> : null}
        {profiles.map(item => <option key={item.id} value={item.id}>{item.name}{item.configured ? "" : ` · ${ui("未配置密钥")}`}</option>)}
        <option value="__new__">＋ {ui("配置自定义模型")}</option>
      </select>
      {selected ? <button type="button" disabled={disabled || busy} className="text-xs text-slate-400 underline disabled:opacity-50"
        onClick={() => { setError(""); setForm({ id: selected.id, name: selected.name, provider: selected.provider,
          model: selected.model, base_url: selected.base_url, context_window: selected.context_window,
          compatibility: selected.compatibility, api_key: "" }); }}>{ui("编辑配置")}</button> : null}
      <span className="text-[11px] text-slate-500">{ui("全系统共用 · 仅用于对话主控")}</span>
    </div>
    {error && !form ? <div role="alert" className="mb-2 flex gap-2 px-2 text-xs text-amber-200"><p>{error}</p>
      {!loaded ? <button type="button" onClick={() => { setError(""); void getGlobalModelProfiles().then(value => {
        if (alive.current) { setProfiles(value.profiles); setLoaded(true); }
      }).catch(() => { if (alive.current) setError(ui("模型列表读取失败，请重新读取。")); }); }} className="underline">{ui("重试")}</button> : null}
    </div> : null}
    {form ? createPortal(<dialog ref={dialog} aria-labelledby={`${id}-title`} onCancel={event => { event.preventDefault(); close(); }}
      className="m-auto max-h-[90vh] w-[min(34rem,94vw)] overflow-y-auto rounded-2xl border border-mars-border bg-mars-panel p-5 text-slate-200 shadow-2xl backdrop:bg-black/70">
      <form onSubmit={event => { event.preventDefault(); void save(); }}>
        <h2 id={`${id}-title`} className="text-lg font-medium">{ui("全局模型配置")}</h2>
        <p className="mt-2 text-xs leading-5 text-slate-400">{ui("保存一次，所有项目均可选择。仅切换对话主控，研究与编码任务仍使用原来的模型配置。")}</p>
        <fieldset disabled={busy} className="mt-4 space-y-3">
          <label className="block text-xs">{ui("显示名称")}<input autoFocus required maxLength={80} value={form.name}
            onChange={event => setForm({ ...form, name: event.target.value })} className={fieldClass} placeholder="Company Qwen" /></label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-xs">{ui("服务类型")}<select value={form.provider} onChange={event => setForm({ ...form, provider: event.target.value })} className={fieldClass}>
              <option value="custom">OpenAI Compatible</option><option value="local_vllm">vLLM / Local</option>
              <option value="qwen">Qwen</option><option value="zhipu">Zhipu / GLM</option><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option>
            </select></label>
            <label className="block text-xs">{ui("兼容配置")}<select value={form.compatibility} onChange={event => {
              const value = event.target.value as SaveGlobalModelProfile["compatibility"];
              setForm({ ...form, compatibility: value, context_window: value === "qwen_200k" ? 200000 : value === "gemma_160k" ? 160000 : 128000 });
            }} className={fieldClass}><option value="qwen_200k">Qwen · 200k</option><option value="gemma_160k">Gemma · 160k</option><option value="generic">{ui("通用兼容")}</option></select></label>
          </div>
          <label className="block text-xs">{ui("真实模型 ID")}<input required maxLength={200} value={form.model} onChange={event => setForm({ ...form, model: event.target.value })} className={fieldClass} placeholder={ui("填写公司接口要求的真实名称")} /></label>
          <label className="block text-xs">{ui("API 地址")}<input required type="url" maxLength={2048} value={form.base_url} onChange={event => setForm({ ...form, base_url: event.target.value })} className={fieldClass} placeholder="http://company-gateway/v1" /></label>
          <label className="block text-xs">API Key<input type="password" autoComplete="new-password" value={form.api_key} onChange={event => setForm({ ...form, api_key: event.target.value })} className={fieldClass} placeholder={ui(form.id ? "留空保留已保存密钥" : "密钥仅保存在本机，不会回传")} /></label>
          <label className="block text-xs">{ui("部署上下文容量（Token）")}<input required type="number" min={4096} max={2000000} step={1} value={form.context_window} onChange={event => setForm({ ...form, context_window: Number(event.target.value) })} className={fieldClass} /></label>
        </fieldset>
        {error ? <p role="alert" className="mt-3 text-sm text-amber-200">{error}</p> : null}
        <div className="mt-5 flex justify-end gap-3"><button type="button" disabled={busy} onClick={close} className="rounded-lg border border-mars-border px-3 py-2 text-sm disabled:opacity-50">{ui("取消")}</button>
          <button type="submit" disabled={busy} className="rounded-lg bg-mars-accent px-4 py-2 text-sm text-white disabled:opacity-50">{ui(busy ? "正在保存…" : "保存并使用")}</button></div>
      </form>
    </dialog>, document.body) : null}
  </>;
}
