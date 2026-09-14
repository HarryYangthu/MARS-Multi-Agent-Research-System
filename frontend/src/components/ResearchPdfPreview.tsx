"use client";

import { useEffect, useRef, useState } from "react";
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";

// Package the worker with the app so local archives need no third-party viewer/CDN.
GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

export default function ResearchPdfPreview({ url, title }: { url: string; title: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(true);
  useEffect(() => {
    let active = true;
    const task = getDocument({ url, isEvalSupported: false });
    void task.promise.then((pdf) => { if (active) setDocument(pdf); }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : "PDF 加载失败");
    });
    return () => { active = false; void task.destroy(); };
  }, [url]);
  useEffect(() => {
    if (!document || !canvas.current) return;
    let active = true;
    let render: RenderTask | undefined;
    const target = canvas.current;
    setRendering(true);
    setError("");
    void document.getPage(page).then(async (pdfPage) => {
      if (!active) return;
      const context = target.getContext("2d");
      if (!context) throw new Error("浏览器无法绘制 PDF 页面");
      const viewport = pdfPage.getViewport({ scale: 1.5 });
      target.width = viewport.width;
      target.height = viewport.height;
      render = pdfPage.render({ canvasContext: context, viewport });
      await render.promise;
      if (active) setRendering(false);
    }).catch((cause: unknown) => {
      if (active) { setRendering(false); setError(cause instanceof Error ? cause.message : "PDF 页面绘制失败"); }
    });
    return () => { active = false; render?.cancel(); };
  }, [document, page]);
  const button = "rounded border border-mars-border px-3 py-1.5 text-xs text-slate-200 hover:bg-mars-panel disabled:opacity-40";
  return <div className="mt-3">
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <button className={button} disabled={!document || page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
      <label className="flex items-center gap-1 text-xs text-slate-300">第 <input aria-label="PDF 页码" type="number" min={1} max={document?.numPages ?? 1} value={page} disabled={!document} onChange={(event) => {
        const value = Number(event.target.value);
        if (Number.isInteger(value) && value >= 1 && value <= (document?.numPages ?? 0)) setPage(value);
      }} className="w-14 rounded border border-mars-border bg-mars-bg px-2 py-1" /> / {document?.numPages ?? "…"} 页</label>
      <button className={button} disabled={!document || page >= document.numPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
      <a href={url} target="_blank" rel="noreferrer" className={button}>打开 / 保存原文件</a>
    </div>
    {error ? <p role="alert" className="mb-2 text-xs text-rose-300">{error}。可用“打开 / 保存原文件”查看。</p> : rendering ? <p role="status" className="mb-2 text-xs text-slate-400">正在绘制第 {page} 页…</p> : <p role="status" className="mb-2 text-xs text-slate-400">第 {page} 页已显示</p>}
    <div className="max-h-[720px] overflow-auto rounded bg-slate-800 p-2"><canvas key={page} ref={canvas} role="img" aria-label={`${title}，第 ${page} 页`} className="mx-auto h-auto w-full max-w-[1000px] bg-white" /></div>
  </div>;
}
