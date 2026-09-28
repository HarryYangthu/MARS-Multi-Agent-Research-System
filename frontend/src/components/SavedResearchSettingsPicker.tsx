"use client";

import { useEffect, useRef, useState } from "react";
import { listSavedSettings, loadSavedSettings, type SavedSettings, type SavedSettingsSummary } from "@/lib/researchTemplates";

export function SavedResearchSettingsPicker({ disabled, onLoad, onBusyChange }: { disabled: boolean; onLoad: (value: SavedSettings) => void; onBusyChange: (value: boolean) => void }): JSX.Element {
  const [items, setItems] = useState<SavedSettingsSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const pending = useRef<AbortController | null>(null);
  const unavailable = useRef(0);
  const blocked = useRef(disabled); blocked.current = disabled;
  useEffect(() => () => { pending.current?.abort(); onBusyChange(false); }, [onBusyChange]);
  useEffect(() => { if (disabled) { pending.current?.abort(); pending.current = null; setBusy(false); onBusyChange(false); } }, [disabled, onBusyChange]);

  async function act(kind: "list" | "load", more = false): Promise<void> {
    if (blocked.current || pending.current) return;
    const controller = new AbortController(); pending.current = controller; setBusy(true); onBusyChange(true); setMessage("");
    try {
      if (kind === "list") {
        const page = await listSavedSettings(controller.signal, more ? cursor : null);
        if (controller.signal.aborted || blocked.current) return;
        setItems((previous) => more ? [...previous, ...page.items.filter((item) => !previous.some((old) => old.run_id === item.run_id))] : page.items);
        setCursor(page.next_cursor); setLoaded(true);
        unavailable.current = (more ? unavailable.current : 0) + page.unavailable_count;
        setMessage(unavailable.current ? `${unavailable.current} 项保存证据不完整，已排除；不会从旧投影恢复设置。` : "");
      } else {
        const settings = await loadSavedSettings(selected, controller.signal);
        if (controller.signal.aborted || blocked.current) return;
        onLoad(settings);
        setMessage("已载入项目、全部预算与模式。保留当前研究名称和目标；原任务未修改，仍须重新预检与冻结。");
      }
    } catch (error: unknown) {
      if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : "读取设置失败，请检查连接。");
    } finally {
      if (pending.current === controller) { pending.current = null; setBusy(false); onBusyChange(false); }
    }
  }

  return <section className="space-y-3 rounded-md border border-mars-border p-4" aria-labelledby="saved-settings-heading">
    <h3 id="saved-settings-heading" className="text-sm font-medium">复用已保存研究的完整项目设置</h3>
    <p className="text-xs leading-6 text-slate-400">仅载入项目声明、23 项预算与研究模式。当前新名称、目标会保留，不继承旧输入指纹，不自动保存或启动。</p>
    <button type="button" disabled={disabled || busy} onClick={() => void act("list")} className="rounded border border-mars-border px-3 py-2 text-sm disabled:opacity-40">{busy ? "正在读取…" : loaded ? "刷新已保存设置" : "读取已保存设置"}</button>
    {items.length ? <div className="flex flex-wrap gap-2"><label className="min-w-0 flex-1 text-sm" htmlFor="saved-research-settings">选择来源研究<select id="saved-research-settings" value={selected} disabled={disabled || busy} onChange={(event) => setSelected(event.target.value)} className="mt-2 w-full rounded border border-mars-border bg-mars-bg px-3 py-2"><option value="">请选择</option>{items.map((item) => <option key={item.run_id} value={item.run_id}>{item.name} · {item.display_name} · {item.run_id}</option>)}</select></label>
      <button type="button" disabled={disabled || busy || !selected} onClick={() => void act("load")} className="self-end rounded border border-mars-border px-3 py-2 text-sm disabled:opacity-40">载入所选设置</button></div> : loaded ? <p className="text-sm text-slate-400">当前页没有可复用的完整设置。</p> : null}
    {cursor ? <button type="button" disabled={disabled || busy} onClick={() => void act("list", true)} className="text-sm underline disabled:opacity-40">读取下一页</button> : null}
    {message ? <p role="status" className="text-sm leading-6 text-amber-100">{message}</p> : null}
  </section>;
}
