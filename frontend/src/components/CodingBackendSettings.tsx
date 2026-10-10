"use client";

import { useEffect, useState } from "react";
import { codingBackendSettings, type CodingBackend, type CodingBackendStatus } from "@/lib/codingBackend";

export function CodingBackendSettings(): JSX.Element {
  const [status, setStatus] = useState<CodingBackendStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let alive = true;
    void codingBackendSettings().then((value) => { if (alive) setStatus(value); })
      .catch((error: unknown) => { if (alive) setMessage(error instanceof Error ? error.message : "设置加载失败"); });
    return () => { alive = false; };
  }, []);

  async function choose(backend: CodingBackend): Promise<void> {
    setSaving(true);
    setMessage("");
    try {
      setStatus(await codingBackendSettings(backend));
      setMessage("已保存，下次编码开始时生效。");
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally { setSaving(false); }
  }

  return (
    <section className="mt-6 rounded border border-mars-border bg-mars-panel/70 p-5" aria-labelledby="coding-engine-heading">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="coding-engine-heading" className="text-lg font-semibold">编码引擎</h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">在后台完成代码修改，过程、改动与审核都保留在研究对话中。</p>
        </div>
        {status && <span className="text-xs text-slate-400">ZCode {status.zcode_available ? "已安装" : "未就绪"}</span>}
      </div>
      <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="选择编码引擎">
        {([{ id: "zcode", label: "ZCode" }, { id: "native_llm", label: "MARS 内置" }] as const).map((option) => (
          <button key={option.id} type="button" aria-pressed={status?.selected === option.id}
            disabled={!status || saving || (option.id === "zcode" && !status.zcode_available)}
            onClick={() => { void choose(option.id); }}
            className={`rounded border px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40 ${status?.selected === option.id ? "border-mars-accent bg-mars-accent/15 text-indigo-100" : "border-mars-border text-slate-300 hover:bg-mars-panel2"}`}>
            {option.label}{status?.selected === option.id ? " · 当前" : ""}
          </button>
        ))}
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-500">已有编码任务恢复时沿用原引擎。ZCode 使用「模型连接」中编码 Agent 的配置。</p>
      {status?.reason && <p className="mt-3 text-sm text-amber-200">{status.reason}</p>}
      {message && <p className="mt-3 text-sm text-slate-300" role="status">{message}</p>}
    </section>
  );
}
