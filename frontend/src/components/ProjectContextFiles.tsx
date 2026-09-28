"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getProjectAutoContext, getProjectContextDocument, type ProjectAutoContext, type ProjectContextDocument } from "@/lib/api";

export function ProjectContextFiles({ project }: { project: string }) {
  return <ContextFiles key={project} project={project} />;
}
function ContextFiles({ project }: { project: string }) {
  const [record, setRecord] = useState<ProjectAutoContext | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void getProjectAutoContext(project, controller.signal).then((data) => {
      if (!controller.signal.aborted) { setRecord(data); setError(""); }
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) { setRecord(null); setError(cause instanceof Error ? cause.message : "无法读取项目资料"); }
    });
    return () => controller.abort();
  }, [project, revision]);
  const references = record?.files.filter((file) => file.role !== "instructions" && !file.is_template) ?? [];
  const rules = record?.files.find((file) => file.role === "instructions");
  const hasRules = rules !== undefined && !rules.is_template && rules.chars > 0;
  return <section aria-label="系统将读取的资料" className="rounded border border-mars-border bg-mars-panel p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold text-slate-100">系统将读取的资料</h3><button type="button" className="text-xs text-cyan-200 hover:underline" onClick={() => setRevision((v) => v + 1)}>重新扫描</button></div>
    <p className="mt-2 text-xs leading-relaxed text-slate-400">背景帮助 Agent 理解项目，规则约束 Agent 的操作。代码仓单独关联，按需读取；本次研究目标在对话中填写。</p>
    {record ? <>
      <p className="mt-2 break-all text-xs text-slate-500">{record.folder}</p>
      <h4 className="mt-4 text-xs font-medium text-slate-200">项目背景 · {references.length} 份</h4>
      {references.length > 0 ? <ul className="mt-2 space-y-2">{references.map((file) => <li key={file.path}><DocumentRow file={file} onPreview={setSelected} /></li>)}</ul> : <p className="mt-2 text-xs text-slate-400">尚无背景资料。可上传一份 Markdown / TXT，自动保存为 README.md；此项可稍后补充。</p>}
      <section aria-label="Agent 规则说明" className="mt-4 rounded border border-mars-border p-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-medium text-slate-100">Agent 规则 · AGENTS.md</h4><span className="text-xs text-slate-400">{hasRules ? "已填写" : "可选 · 待填写"}</span></div>
        <dl className="mt-3 space-y-2 text-xs leading-5 text-slate-400">
          <div><dt className="inline font-medium text-slate-200">写什么：</dt><dd className="inline">长期操作规则，例如哪些基线文件不能修改、允许写入哪些目录、运行仿真必须遵守什么要求。</dd></div>
          <div><dt className="inline font-medium text-slate-200">谁来写：</dt><dd className="inline">你或项目维护者确定；也可以让 AI 起草，由你审核后保存。</dd></div>
          <div><dt className="inline font-medium text-slate-200">什么时候写：</dt><dd className="inline">有明确约束时，最好在 Agent 修改代码或运行仿真前补充。现在可跳过，以后可随时更新；研究目标仍在对话中写。</dd></div>
        </dl>
        <p className="mt-3 text-xs leading-5 text-slate-400">在项目文件夹中编辑 <code className="text-slate-200">AGENTS.md</code>，保存后点击“重新扫描”。更新供新任务读取，已有任务保留创建时的资料快照。</p>
        {hasRules ? <div className="mt-3"><DocumentRow file={rules} onPreview={setSelected} /></div> : <p className="mt-2 text-xs text-slate-500">{rules?.is_template ? "系统生成的空模板尚未填写，不作为项目规则加载。" : "尚无已填写的规则；有需要时可在项目文件夹中补充。"}</p>}
      </section>
      {record.warnings.map((warning) => <p key={warning} className="mt-2 text-xs text-amber-200">{warning}</p>)}
    </> : !error ? <p role="status" className="mt-2 text-xs text-slate-400">正在扫描项目文档…</p> : null}
    {error ? <p role="alert" className="mt-3 text-xs text-rose-300">{error}</p> : null}
    {selected ? <ContextDocument key={`${project}:${selected}:${revision}`} project={project} path={selected} onClose={() => setSelected("")} /> : null}
  </section>;
}
function DocumentRow({ file, onPreview }: { file: ProjectContextDocument; onPreview: (path: string) => void }) {
  return <div className="flex items-center justify-between gap-2 rounded border border-mars-border px-3 py-2"><div className="min-w-0"><p className="break-words text-xs text-slate-200">{file.path}</p><p className="mt-1 text-[11px] text-slate-500">{file.role === "instructions" ? "项目规则" : "背景资料"} · {file.chars.toLocaleString()} 字符</p></div><button type="button" aria-label={`预览 ${file.path}`} className="shrink-0 rounded border border-mars-border px-2 py-1 text-xs text-cyan-200" onClick={() => onPreview(file.path)}>预览</button></div>;
}
function ContextDocument({ project, path, onClose }: { project: string; path: string; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void getProjectContextDocument(project, path, controller.signal).then((data) => { if (!controller.signal.aborted) setText(data.content); }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "文档读取失败"); });
    return () => controller.abort();
  }, [project, path]);
  return <section aria-label={`上下文预览：${path}`} className="mt-3 rounded border border-cyan-500/30 p-3"><div className="flex items-start justify-between gap-2"><h4 className="break-all text-xs text-slate-100">{path}</h4><button type="button" onClick={onClose} className="text-xs text-slate-400">关闭预览</button></div>{error ? <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p> : text === null ? <p className="mt-2 text-xs text-slate-400">读取中…</p> : <div className="prose prose-invert mt-3 max-h-96 max-w-none overflow-auto text-sm"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>}</section>;
}
