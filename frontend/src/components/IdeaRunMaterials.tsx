"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getIdeaMaterialContent, getIdeaMaterials, researchSourceUrl, type IdeaMaterial, type IdeaMaterialsView } from "@/lib/api";

const buttonClass = "rounded border border-mars-border px-2.5 py-1.5 text-xs text-cyan-100 hover:bg-cyan-500/10 focus-visible:outline focus-visible:outline-cyan-300";
const decisions: Record<string, string> = { use: "采用", reject: "排除", defer: "暂缓" };
const ResearchPdfPreview = dynamic(() => import("./ResearchPdfPreview"), {
  ssr: false, loading: () => <p className="mt-3 text-xs text-slate-400">正在打开 PDF 阅读器…</p>,
});

export function IdeaRunMaterials({ runId, running }: { runId: string; running: boolean }) {
  // A run-specific key also resets the preview when the user switches tasks.
  return <Materials key={runId} runId={runId} running={running} />;
}

function Materials({ runId, running }: { runId: string; running: boolean }) {
  const [data, setData] = useState<IdeaMaterialsView | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<{ material: IdeaMaterial; pdf: boolean } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      try {
        const result = await getIdeaMaterials(runId, controller.signal);
        if (!controller.signal.aborted) { setData(result); setError(""); }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "材料加载失败");
      } finally {
        if (running && !controller.signal.aborted) timer = setTimeout(refresh, 5000);
      }
    };
    void refresh();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [runId, running, revision]);

  const items = data?.items ?? [];
  const papers = items.filter((item) => item.kind === "paper");
  const contexts = items.filter((item) => item.kind === "context" || item.kind === "code");
  const searches = items.filter((item) => item.kind === "search");
  const conclusions = items.find((item) => item.kind === "research");
  const downloaded = papers.filter((item) => item.archive_available).length;
  const open = (material: IdeaMaterial, pdf = false) => setSelected({ material, pdf });

  return (
    <section aria-label="研究资料与上下文" className="border-b border-mars-border bg-mars-panel/15 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-100">研究资料与上下文</h3>
        <button className={buttonClass} onClick={() => setRevision((value) => value + 1)}>刷新材料</button>
      </div>
      {error ? <p role="alert" className="mt-2 text-xs text-rose-300">{error}。可点击刷新重试。</p> : null}
      {!data && !error ? <p role="status" className="mt-3 text-xs text-slate-400">正在读取本次任务的材料…</p> : null}
      {data?.warnings.map((warning) => <p key={warning} className="mt-2 text-xs text-amber-200">{warning}</p>)}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm text-slate-200">论文与调研 <span className="text-xs text-slate-400">{papers.length} 篇已记录 · {downloaded} 篇可打开原文</span></h4>
        {conclusions ? <button className={buttonClass} onClick={() => open(conclusions)}>查看调研结论</button> : null}
      </div>
      <p className="mt-1 text-xs leading-relaxed text-slate-400">下载与阅读分别记录；阅读范围以保存的页码和字符片段为准。</p>
      <div className="mt-3 grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))" }}>
        {papers.map((paper) => (
          <article key={paper.id} className="min-w-0 rounded border border-mars-border bg-mars-bg p-3">
            <div className="flex flex-wrap gap-2 text-[11px]">
              <span className={paper.status.includes("失败") ? "text-rose-300" : "text-cyan-200"}>{paper.status}</span>
              {paper.decision ? <span className="text-amber-200">{decisions[paper.decision] ?? paper.decision}</span> : null}
            </div>
            <h5 className="mt-2 break-words text-sm font-medium text-slate-100">{paper.title}</h5>
            {paper.description ? <p className="mt-2 break-words text-xs leading-relaxed text-amber-200">{paper.description}</p> : null}
            {paper.reason ? <p className="mt-2 text-xs leading-relaxed text-slate-300"><span className="text-slate-500">选择理由：</span>{paper.reason}</p> : null}
            {paper.transfer ? <details className="mt-2 text-xs leading-relaxed text-slate-300"><summary className="cursor-pointer text-cyan-200">提取的思路</summary><p className="mt-2">{paper.transfer}</p></details> : null}
            {paper.read_windows.length ? <p className="mt-2 text-[11px] text-slate-400">已保存 {paper.read_windows.length} 个阅读片段</p> : null}
            <div className="mt-3 flex flex-wrap gap-2">
              {paper.archive_available && paper.source_type === "pdf" ? <button className={buttonClass} onClick={() => open(paper, true)}>查看 PDF</button> : null}
              {paper.archive_available ? <a className={buttonClass} href={researchSourceUrl(runId, paper.source_id)} target="_blank" rel="noreferrer">打开原文件</a> : null}
              {paper.preview_available ? <button className={buttonClass} onClick={() => open(paper)}>查看阅读内容</button> : null}
              {paper.source_url ? <a className={buttonClass} href={paper.source_url} target="_blank" rel="noreferrer">来源页面 ↗</a> : null}
            </div>
          </article>
        ))}
      </div>
      {data && !papers.length ? <p className="mt-3 text-xs text-slate-400">尚无论文获取记录，执行调研后会显示在这里。</p> : null}
      {searches.length ? <details className="mt-3 rounded border border-mars-border p-3 text-xs text-slate-300"><summary className="cursor-pointer">检索记录 · {searches.length} 次</summary><ul className="mt-2 space-y-2">{searches.map((item) => <li key={item.id} className="flex items-center justify-between gap-2"><div className="min-w-0"><p className="break-words">{item.title}</p><p className="mt-1 text-slate-500">{item.description}</p></div><button className={`${buttonClass} shrink-0`} onClick={() => open(item)}>查看结果</button></li>)}</ul></details> : null}
      <h4 className="mt-5 text-sm text-slate-200">本次使用的背景与代码</h4>
      <p className="mt-1 text-xs text-slate-400">显示运行时保存的背景快照和实际代码读取记录，便于核对模型收到的内容。</p>
      <div className="mt-3 grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))" }}>
        {contexts.map((item) => <article key={item.id} className="flex min-w-0 items-start justify-between gap-2 rounded border border-mars-border bg-mars-bg p-3"><div className="min-w-0"><h5 className="break-words text-xs font-medium text-slate-200">{item.title}</h5><p className="mt-1 text-[11px] text-cyan-200">{item.status}</p><p className="mt-1 break-words text-[11px] leading-relaxed text-slate-400">{item.description}</p></div>{item.preview_available ? <button className={`${buttonClass} shrink-0`} onClick={() => open(item)}>查看内容</button> : null}</article>)}
      </div>
      {data && !contexts.length ? <p className="mt-3 text-xs text-slate-400">尚无背景快照或代码读取记录。</p> : null}
      {selected ? <MaterialPreview key={`${selected.material.id}:${selected.pdf}:${revision}`} runId={runId} material={selected.material} pdf={selected.pdf} onClose={() => setSelected(null)} /> : null}
    </section>
  );
}

function MaterialPreview({ runId, material, pdf, onClose }: { runId: string; material: IdeaMaterial; pdf: boolean; onClose: () => void }) {
  const preview = useRef<HTMLElement>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { preview.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, []);
  useEffect(() => {
    if (pdf) return;
    const controller = new AbortController();
    void getIdeaMaterialContent(runId, material.id, controller.signal).then((result) => {
      if (!controller.signal.aborted) setText(result.text);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法加载正文");
    });
    return () => controller.abort();
  }, [runId, material.id, pdf]);

  return <section ref={preview} aria-label={`材料预览：${material.title}`} className="mt-4 rounded border border-cyan-500/40 bg-mars-bg p-3">
    <div className="flex items-start justify-between gap-3"><div className="min-w-0"><h4 className="break-words text-sm font-medium text-slate-100">{material.title}</h4><p className="mt-1 break-all text-[10px] text-slate-500">{material.provenance || material.source_url}</p></div><button className={`${buttonClass} shrink-0`} onClick={onClose}>关闭预览</button></div>
    {pdf ? <ResearchPdfPreview url={researchSourceUrl(runId, material.source_id)} title={material.title} /> :
      error ? <p role="alert" className="mt-3 text-xs text-rose-300">{error}</p> : text === null ? <p role="status" className="mt-3 text-xs text-slate-400">加载正文…</p> :
        <div className="mt-3 max-h-[650px] overflow-auto rounded border border-mars-border p-4">
          {material.kind === "code" || material.kind === "paper" || material.kind === "search" ? <pre className="whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-300">{text}</pre> : <div className="prose prose-invert max-w-none text-sm"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>}
        </div>}
  </section>;
}
