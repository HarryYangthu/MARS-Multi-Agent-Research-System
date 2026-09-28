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
  return <section aria-label="项目文件" className="rounded-lg border border-mars-border p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold text-slate-100">项目文件</h3><button type="button" className="text-xs text-cyan-200 hover:underline" onClick={() => setRevision((v) => v + 1)}>重新扫描</button></div>
    {record ? <>
      {references.length > 0 || rules ? <ul className="mt-3 divide-y divide-mars-border">
        {references.map((file) => <li key={file.path}><DocumentRow file={file} onPreview={setSelected} /></li>)}
        {rules ? <li><DocumentRow file={rules} onPreview={setSelected} /></li> : null}
      </ul> : null}
      {references.length === 0 ? <p className="mt-3 text-xs text-slate-400">未上传背景</p> : null}
      {record.warnings.map((warning) => <p key={warning} className="mt-2 text-xs text-amber-200">{warning}</p>)}
    </> : !error ? <p role="status" className="mt-2 text-xs text-slate-400">正在扫描项目文档…</p> : null}
    {error ? <p role="alert" className="mt-3 text-xs text-rose-300">{error}</p> : null}
    {selected ? <ContextDocument key={`${project}:${selected}:${revision}`} project={project} path={selected} onClose={() => setSelected("")} /> : null}
  </section>;
}
function DocumentRow({ file, onPreview }: { file: ProjectContextDocument; onPreview: (path: string) => void }) {
  const pendingRules = file.role === "instructions" && (file.is_template || file.chars === 0);
  return <div className="flex items-center justify-between gap-3 py-3"><div className="min-w-0"><p className="break-words text-xs text-slate-200">{file.path}</p><p className="mt-1 text-[11px] text-slate-500">{pendingRules ? "规则 · 可选 · 未填写" : `${file.role === "instructions" ? "规则" : "背景"} · ${file.chars.toLocaleString()} 字符`}</p></div><button type="button" aria-label={`预览 ${file.path}`} className="shrink-0 rounded border border-mars-border px-2 py-1 text-xs text-cyan-200" onClick={() => onPreview(file.path)}>{file.is_template ? "查看模板" : "预览"}</button></div>;
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
