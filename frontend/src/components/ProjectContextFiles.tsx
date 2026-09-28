"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getProjectAutoContext, getProjectContextDocument, type ProjectAutoContext } from "@/lib/api";

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
  return <section aria-label="系统将读取的资料" className="rounded border border-mars-border bg-mars-panel p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold text-slate-100">系统将读取的资料</h3><button type="button" className="text-xs text-cyan-200 hover:underline" onClick={() => setRevision((v) => v + 1)}>重新扫描</button></div>
    <p className="mt-2 text-xs leading-relaxed text-slate-400">这里列出系统已发现的背景说明和项目约定，不是研究目标，也不是额外必填项。AGENTS.md 描述项目约束，README.md 和上传文件补充背景；列出文件不代表内容已经充分。代码工程单独关联，由 Agent 按需读取。</p>
    {record ? <><p className="mt-2 break-all text-xs text-slate-500">{record.folder}</p><p className="mt-2 text-xs text-cyan-200">{record.files.length} 份文档 · {record.total_chars.toLocaleString()} 字符</p><ul className="mt-3 space-y-2">{record.files.map((file) => <li key={file.path} className="flex items-center justify-between gap-2 rounded border border-mars-border px-3 py-2"><div className="min-w-0"><p className="break-words text-xs text-slate-200">{file.path}</p><p className="mt-1 text-[11px] text-slate-500">{file.role === "instructions" ? "项目约定" : "背景资料"} · {file.chars.toLocaleString()} 字符</p></div><button type="button" className="shrink-0 rounded border border-mars-border px-2 py-1 text-xs text-cyan-200" onClick={() => setSelected(file.path)}>预览</button></li>)}</ul>{record.files.length === 0 ? <p className="mt-3 text-xs text-amber-200">尚无背景文档。可在项目中添加 README.md、AGENTS.md，或在 context/ 中放入 Markdown 资料。</p> : null}{record.warnings.map((warning) => <p key={warning} className="mt-2 text-xs text-amber-200">{warning}</p>)}</> : !error ? <p role="status" className="mt-2 text-xs text-slate-400">正在扫描项目文档…</p> : null}
    {error ? <p role="alert" className="mt-3 text-xs text-rose-300">{error}</p> : null}
    {selected ? <ContextDocument key={`${project}:${selected}:${revision}`} project={project} path={selected} onClose={() => setSelected("")} /> : null}
  </section>;
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
