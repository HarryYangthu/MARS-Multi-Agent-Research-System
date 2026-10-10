"use client";

import { useEffect, useState } from "react";
import { OnboardingModelSetup } from "./OnboardingModelSetup";
import { ProjectDataPipeline } from "./ProjectDataPipeline";
import { ProjectGpuConfig } from "./ProjectGpuConfig";
import { ProjectCodeFolder } from "./ProjectCodeFolder";
import { ProjectContextFiles } from "./ProjectContextFiles";
import { uploadProjectBackground, type ProjectSummary } from "@/lib/api";

const BUTTON = "rounded-lg border border-mars-border px-4 py-2 text-sm hover:bg-mars-panel2 disabled:opacity-40";
const STEPS = ["项目文件夹", "模型连接", "代码与资料", "研究数据", "执行环境（可选）", "完成"];

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
  const [backupPath, setBackupPath] = useState("");
  const busy = modelBusy || uploading || codeBusy;
  useEffect(() => { onBusy(busy); }, [busy, onBusy]);

  async function upload(file: File): Promise<void> {
    if (busy) return;
    if (!/[.](md|txt)$/i.test(file.name)) { setError("请选择 Markdown（.md）或 TXT（.txt）文本文件。"); return; }
    setUploading(true); setError(""); setMessage(""); setBackupPath("");
    try {
      const saved = await uploadProjectBackground(project.name, file);
      setRevision((value) => value + 1);
      setMessage(`已保存 ${saved.path}`);
      setBackupPath(saved.previous_path ?? "");
    } catch (cause: unknown) { setError(cause instanceof Error ? cause.message : "上传未能确认，请重新扫描资料列表后再试。"); }
    finally { setUploading(false); }
  }

  return <div className="mt-4 space-y-4">
    <ol aria-label="新建项目步骤" className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-6">
      {STEPS.map((title, index) => <li key={title}>
        <button type="button" aria-current={index === step ? "step" : undefined} disabled={busy}
          onClick={() => setStep(index)}
          className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${index === step ? "border-indigo-400 bg-indigo-500/10 text-indigo-100" : "border-mars-border text-slate-400 hover:bg-mars-panel2 hover:text-slate-200"}`}>
          {index === 0 ? "✓" : index + 1} {title}
        </button>
      </li>)}
    </ol>
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs leading-5"><span className="shrink-0 text-slate-500">项目目录</span><span className="min-w-0 break-all text-slate-300">{project.folder_path || project.repo_path}</span></div>
    {step === 0 ? <div className="space-y-2 text-sm leading-6 text-slate-400">
      <p>项目文件夹在「新建项目 / 打开文件夹」时已选定；项目元数据保存在目录内的 .mars/ 中，背景资料与研究记录都不会写入基线代码仓。</p>
      <p>顶部步骤条可以随时点击跳转，已保存的内容会保留。</p>
    </div> : null}
    {step !== 2 ? <h3 className="text-base font-semibold">{step === 5 ? "项目已创建" : STEPS[step]}</h3> : null}
    <div hidden={step !== 1}><OnboardingModelSetup onStatus={setApiSaved} onBusy={setModelBusy} /></div>
    <div hidden={step !== 2} className="space-y-3">
      {project.project_type === "folder" ? <ProjectCodeFolder project={project} onBusy={setCodeBusy} /> : null}
      {project.project_type === "folder" ? <label className="block rounded-lg border border-mars-border p-4 text-sm"><span className="flex flex-wrap items-center justify-between gap-2"><span className="font-semibold">背景文件</span><span className="text-xs text-slate-500">README.md · Markdown / TXT</span></span><input aria-label="上传背景文件" type="file" accept=".md,.txt,text/markdown,text/plain" disabled={busy} className="mt-3 block w-full min-w-0 text-sm file:mr-3 file:rounded file:border-0 file:bg-indigo-500/20 file:px-3 file:py-2 file:text-indigo-100" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file); }} /></label> : null}
      {uploading ? <p role="status" className="text-sm">正在上传…</p> : null}
      {message ? <p role="status" className="text-sm text-emerald-200">{message}</p> : null}
      {backupPath ? <details className="text-xs leading-5 text-slate-400"><summary className="cursor-pointer">旧版备份</summary><p className="mt-2 break-all">{project.folder_path}/{backupPath}</p></details> : null}
      {error ? <p role="alert" className="text-sm text-amber-200">{error}</p> : null}
      <ProjectContextFiles key={revision} project={project.name} />
    </div>
    {step === 3 ? <ProjectDataPipeline key={project.name} project={project.name} /> : null}
    {step === 4 ? <div className="space-y-4"><p className="rounded-lg border border-mars-border p-4 text-sm text-slate-400">在本机执行可跳过此步骤。使用远程 GPU 时再配置 SSH、代码和数据路径，仿真启动前还会再次核对。</p><ProjectGpuConfig key={project.name} project={project.name} /></div> : null}
    {step === 5 ? <div className="space-y-4 text-sm leading-6">
      <p className="text-slate-400">{apiSaved ? "已保存模型配置。" : "API 可稍后在模型连接设置中配置。"}代码和背景准备完成后，再在对话中描述本次研究目标。</p>
      <p className="text-slate-400">关闭后，仍可从此项目的「项目配置」继续操作。</p>
      <p className="text-sm text-emerald-200">准备就绪后，从项目卡片点击「启动研究」，直接描述研究目标即可。</p>
    </div> : null}
    <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-mars-border pt-4">
      {step > 1 ? <button type="button" disabled={busy} className={BUTTON} onClick={() => setStep((value) => value - 1)}>上一步</button> : <span className="text-xs text-slate-500">已保存的内容会保留</span>}
      <div className="flex flex-wrap gap-2">{step === 5 ? <><button type="button" className={`${BUTTON} bg-mars-accent text-white`} onClick={onDone}>完成</button></> : <>{step === 1 ? <button type="button" disabled={busy} className={BUTTON} onClick={() => setStep(2)}>稍后配置</button> : null}<button type="button" disabled={busy} className={`${BUTTON} bg-mars-accent text-white`} onClick={() => setStep((value) => value + 1)}>{`下一步：${STEPS[step + 1]}`}</button></>}</div>
    </footer>
  </div>;
}
