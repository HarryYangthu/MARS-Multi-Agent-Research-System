"use client";

import { useEffect, useRef, useState } from "react";
import { isUncertainRequestError } from "@/lib/clientPolicy";
import { officeFileUrl, reportRequest, type OfficeBundle, type ReportSkills } from "@/lib/reportExports";

const FORMATS = [
  { kind: "excel", label: "Excel", extension: ".xlsx", detail: "完整指标 · 训练数据 · 可编辑曲线" },
  { kind: "word", label: "Word", extension: ".docx", detail: "完整报告 · 表格 · 实验图像" },
  { kind: "powerpoint", label: "PPT", extension: ".pptx", detail: "研究内容 · 结果 · 局限与来源" },
];
const button = "rounded-lg border border-mars-border px-3 py-2 text-xs text-slate-200 hover:border-indigo-400/60 disabled:cursor-not-allowed disabled:opacity-50";

export function ReportsPanel({ runId, refreshKey = "" }: { runId: string; refreshKey?: string }): JSX.Element {
  const [bundle, setBundle] = useState<OfficeBundle | null>(null);
  const [skills, setSkills] = useState<ReportSkills | null>(null);
  const [selection, setSelection] = useState<string[]>([]);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const action = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setBundle(null); setSkills(null); setSelection([]); setMessage(""); setBusy(""); setFailed(false);
    void Promise.all([reportRequest<OfficeBundle>(runId, "", controller.signal), reportRequest<ReportSkills>(runId, "/skills", controller.signal)])
      .then(([nextBundle, nextSkills]) => { if (!controller.signal.aborted) { setBundle(nextBundle); setSkills(nextSkills); setSelection(nextSkills.selected); } })
      .catch((error: unknown) => { if (!controller.signal.aborted) { setFailed(true); setMessage(error instanceof Error ? error.message : "无法读取报告产物"); } });
    return () => { controller.abort(); action.current?.abort(); };
  }, [runId, refreshKey]);

  async function perform(name: string, operation: (signal: AbortSignal) => Promise<void>): Promise<void> {
    action.current?.abort();
    const controller = new AbortController(); action.current = controller;
    setBusy(name); setMessage(""); setFailed(false);
    try { await operation(controller.signal); }
    catch (error: unknown) { if (!controller.signal.aborted) { setFailed(true); setMessage(isUncertainRequestError(error) ? "连接中断或等待超时，尚不能确认操作结果。刷新后再核对。" : error instanceof Error ? error.message : "操作失败"); } }
    finally { if (!controller.signal.aborted) setBusy(""); }
  }

  async function generate(): Promise<void> {
    await perform("export", async signal => {
      const next = await reportRequest<OfficeBundle>(runId, "/regenerate", signal, "POST");
      signal.throwIfAborted(); setBundle(next);
      const errors = next.metadata?.generation_errors || [];
      setFailed(errors.length > 0);
      setMessage(errors.length ? `部分格式生成失败：${errors.join("；")}` : "Excel、Word 和 PPT 已生成，可以下载。未调用模型或重新运行实验。");
    });
  }

  async function importFile(file: File): Promise<void> {
    if (file.size > 100_000) { setFailed(true); setMessage("SKILL.md 不能超过 100 KB"); return; }
    await perform("import", async signal => {
      const content = await file.text(); signal.throwIfAborted();
      const next = await reportRequest<ReportSkills>(runId, "/skills/import", signal, "POST", { content });
      signal.throwIfAborted(); setSkills(next); setSelection(next.selected);
      setMessage("Skill 已接入。勾选并保存后，用于此项目后续的报告写作。");
    });
  }

  async function saveSkills(): Promise<void> {
    await perform("save", async signal => {
      const next = await reportRequest<ReportSkills>(runId, "/skills", signal, "PUT", { ids: selection });
      signal.throwIfAborted(); setSkills(next); setSelection(next.selected);
      setMessage("报告 skill 已保存，后续写作将使用所选版本。当前已审核报告保持原文。");
    });
  }

  return <section aria-label="报告导出" className="rounded-xl border border-mars-border bg-mars-panel/40">
    <header className="flex flex-wrap items-center justify-between gap-3 px-5 py-4"><div><h3 className="text-base font-medium text-slate-100">报告导出</h3><p className="mt-1 text-xs text-slate-400">基于已审核报告与真实实验记录生成，可继续编辑。</p></div><button type="button" onClick={() => void generate()} disabled={Boolean(busy) || !bundle || bundle.report_ready === false} className={button}>{busy === "export" ? "正在生成…" : bundle?.current ? "重新生成" : "生成导出文件"}</button></header>
    {message ? <p role={failed ? "alert" : "status"} className={`px-5 pb-3 text-xs leading-6 ${failed ? "text-amber-200" : "text-emerald-200"}`}>{message}</p> : null}
    {bundle?.report_ready === false ? <p className="px-5 pb-4 text-xs text-slate-400">请先审核并批准研究报告，再导出。</p> : null}
    {bundle?.exists && !bundle.current ? <p className="px-5 pb-4 text-xs text-amber-200">已有文件需要更新，请先生成本次导出。</p> : null}
    <div className="grid gap-3 px-5 pb-5 sm:grid-cols-3">{FORMATS.map(format => {
      const file = bundle?.metadata?.deliverables.find(item => item.kind === format.kind && item.status === "completed");
      return <div key={format.kind} className="rounded-lg border border-mars-border p-4"><div className="flex items-baseline justify-between gap-2"><h4 className="font-medium text-slate-100">{format.label}</h4><span className="text-xs text-slate-500">{format.extension}</span></div><p className="mt-2 min-h-10 text-xs leading-5 text-slate-400">{format.detail}</p>{file && bundle?.current && bundle.manifest ? <a href={officeFileUrl(runId, file, bundle.manifest)} download className={`${button} mt-3 inline-block`} aria-label={`下载 ${format.label}`}>下载 {format.label}</a> : <span className="mt-3 inline-block text-xs text-slate-500">尚未生成</span>}</div>;
    })}</div>
    <details className="border-t border-mars-border px-5 py-4"><summary className="cursor-pointer text-sm text-slate-300">报告 Skill <span className="ml-2 text-xs text-slate-500">{skills?.selected.length ? `已选择 ${skills.selected.length} 项` : "接入写作方法与风格"}</span></summary><div className="mt-4 space-y-4"><p className="text-xs leading-6 text-slate-400">导入 SKILL.md，选择后用于此项目后续报告写作。指令与版本会随写作保存；已有报告的导出使用原文。当前支持指令型 skill，附带脚本和附件不会自动执行。</p><label className={`${button} inline-block cursor-pointer`}>接入 SKILL.md<input type="file" accept=".md,text/markdown,text/plain" className="sr-only" aria-label="接入 SKILL.md" disabled={Boolean(busy)} onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void importFile(file); }} /></label>
      <div className="space-y-2">{skills?.options.map(option => { const id = `${option.id}@${option.version}`; return <label key={id} className="flex items-start gap-3 rounded-lg border border-mars-border p-3"><input type="checkbox" className="mt-1 accent-indigo-400" checked={selection.includes(id)} disabled={!option.available || Boolean(busy)} onChange={event => setSelection(previous => event.target.checked ? [...previous, id] : previous.filter(value => value !== id))} /><span className="min-w-0"><span className="text-sm text-slate-200">{option.name}</span><span className="mt-1 block text-xs leading-5 text-slate-400">{option.description}</span>{!option.available ? <span className="mt-1 block text-xs text-amber-200">暂不可用：{option.reason}</span> : null}</span></label>; })}</div><button type="button" className={button} disabled={Boolean(busy) || !skills} onClick={() => void saveSkills()}>{busy === "save" ? "正在保存…" : "保存报告 Skill"}</button><details className="text-xs text-slate-500"><summary className="cursor-pointer">SKILL.md 格式示例</summary><pre className="mt-2 overflow-x-auto rounded-lg bg-black/20 p-3">{`---\nname: research-team-report\ndescription: 团队报告的结构与写作要求\n---\n先列研究目标和实验条件，再呈现结果、证据与局限。\n区分事实、解释和待验证假设，不虚构缺失数据。`}</pre></details>
    </div></details>
  </section>;
}
