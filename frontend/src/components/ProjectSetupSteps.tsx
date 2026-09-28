"use client";

import { useEffect, useState } from "react";
import { OnboardingModelSetup } from "./OnboardingModelSetup";
import { ProjectCodeFolder } from "./ProjectCodeFolder";
import { ProjectContextFiles } from "./ProjectContextFiles";
import { uploadProjectBackground, type ProjectSummary } from "@/lib/api";

const BUTTON = "rounded-lg border border-mars-border px-4 py-2 text-sm hover:bg-mars-panel2 disabled:opacity-40";
const STEPS = ["项目文件夹", "配置 API", "代码与背景", "完成"];

export function ProjectSetupSteps({ project, onDone, onBusy }: {
  project: ProjectSummary;
  onDone: () => void;
  onBusy: (busy: boolean) => void;
}): JSX.Element {
  const [step, setStep] = useState(1);
  const [apiSaved, setApiSaved] = useState(false);
  const [modelBusy, setModelBusy] = useState(false);
  const [codeBusy, setCodeBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const busy = modelBusy || uploading || codeBusy;
  useEffect(() => { onBusy(busy); }, [busy, onBusy]);

  async function upload(file: File): Promise<void> {
    if (busy) return;
    if (!/[.](md|txt)$/i.test(file.name)) { setError("请选择 Markdown（.md）或 TXT（.txt）文本文件。"); return; }
    setUploading(true); setError(""); setMessage("");
    try {
      const saved = await uploadProjectBackground(project.name, file);
      setRevision((value) => value + 1);
      setMessage(`已上传 ${saved.path}，可在下方预览。`);
    } catch (cause: unknown) { setError(cause instanceof Error ? cause.message : "上传未能确认，请重新扫描资料列表后再试。"); }
    finally { setUploading(false); }
  }

  return <div className="mt-4 space-y-5">
    <ol aria-label="新建项目步骤" className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
      {STEPS.map((title, index) => <li key={title} aria-current={index === step ? "step" : undefined} className={`rounded-lg border px-3 py-2 ${index === step ? "border-indigo-400 bg-indigo-500/10 text-indigo-100" : "border-mars-border text-slate-400"}`}>{index === 0 ? "✓" : index + 1} {title}</li>)}
    </ol>
    <div className="rounded-lg bg-mars-panel p-3 text-xs leading-5 text-slate-400"><p>项目资料目录：</p><p className="mt-1 break-all text-slate-200">{project.folder_path || project.repo_path}</p></div>
    <h3 className="text-base font-semibold">{step === 3 ? "项目已创建" : STEPS[step]}</h3>
    <div hidden={step !== 1}><OnboardingModelSetup onStatus={setApiSaved} onBusy={setModelBusy} /></div>
    <div hidden={step !== 2} className="space-y-4">
      <p className="text-sm leading-6 text-slate-400">这里仅准备仿真基线代码仓与补充背景，不填写研究目标。本次想做什么，完成配置后再在对话中告诉系统。</p>
      {project.project_type === "folder" ? <ProjectCodeFolder project={project} onBusy={setCodeBusy} /> : null}
      <p className="text-sm leading-6 text-slate-400">背景信息可整理成一份文件，例如已有工作、领域术语、数据说明和参考方法。已有 README 足够时可跳过。</p>
      {project.project_type === "folder" ? <label className="block rounded-lg border border-dashed border-indigo-400/40 p-4 text-sm">上传补充背景文件<input aria-label="上传补充背景文件" type="file" accept=".md,.txt,text/markdown,text/plain" disabled={busy} className="mt-3 block w-full min-w-0 text-sm file:mr-3 file:rounded file:border-0 file:bg-indigo-500/20 file:px-3 file:py-2 file:text-indigo-100" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file); }} /><span className="mt-3 block text-xs leading-5 text-slate-400">选择一份 Markdown 或 TXT 文件即可。TXT 会转存为同名 Markdown，内容不改写；同名资料不覆盖。文档仅作背景参考。</span></label> : <p className="text-sm text-slate-400">此项目的背景资料由项目包管理，可在项目资料中查看。</p>}
      {uploading ? <p role="status" className="text-sm">正在上传…</p> : null}
      {message ? <p role="status" className="text-sm text-emerald-200">{message}</p> : null}
      {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
      <ProjectContextFiles key={revision} project={project.name} />
    </div>
    {step === 3 ? <div className="space-y-4 text-sm leading-6">
      <p className="text-slate-400">{apiSaved ? "已保存模型配置。" : "API 可稍后在模型连接设置中配置。"}代码和背景准备完成后，再在对话中描述本次研究目标。</p>
      <p className="text-slate-400">关闭后，仍可从此项目的「项目配置」继续操作。</p>
      <p className="text-xs text-amber-200">当前可保存研究任务，完整研究启动尚未开放。</p>
    </div> : null}
    <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-mars-border pt-4">
      {step > 1 ? <button type="button" disabled={busy} className={BUTTON} onClick={() => setStep((value) => value - 1)}>上一步</button> : <span className="text-xs text-slate-500">已保存的内容会保留</span>}
      <div className="flex flex-wrap gap-2">{step === 3 ? <><button type="button" className={`${BUTTON} bg-mars-accent text-white`} onClick={onDone}>完成</button></> : <>{step === 1 ? <button type="button" disabled={busy} className={BUTTON} onClick={() => setStep(2)}>稍后配置</button> : null}<button type="button" disabled={busy} className={`${BUTTON} bg-mars-accent text-white`} onClick={() => setStep((value) => value + 1)}>{step === 1 ? "下一步：代码与背景" : "下一步：完成"}</button></>}</div>
    </footer>
  </div>;
}
