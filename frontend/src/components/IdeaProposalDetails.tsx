"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { researchSourceUrl } from "@/lib/api";

const LABELS: Record<string, string> = {
  inputs: "输入", outputs: "输出", input: "输入", output: "输出",
  baseline: "基线", candidate: "候选方法", mechanism: "方法原理",
  equations: "公式", algorithm: "算法步骤", initialization: "初始化",
  training: "训练方式", objective: "优化目标", assumptions: "适用假设",
  limitations: "局限", changes: "具体改动", evaluation: "验证办法",
  metric: "指标", expected_direction: "预期方向", steps: "步骤",
};

function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function Value({ value }: { value: unknown }): JSX.Element {
  if (value === null || value === undefined) return <span>未提供</span>;
  if (Array.isArray(value)) return (
    <ol className="ml-5 list-decimal space-y-2">{value.map((item, index) => <li key={index}><Value value={item} /></li>)}</ol>
  );
  if (typeof value === "object") return (
    <dl className="space-y-3">{Object.entries(object(value)).map(([key, item]) => (
      <div key={key}><dt className="font-medium text-slate-200">{LABELS[key] ?? key}</dt>
        <dd className="mt-1 whitespace-pre-wrap text-slate-300"><Value value={item} /></dd></div>
    ))}</dl>
  );
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{String(value)}</ReactMarkdown>;
}

export function IdeaProposalDetails({ metadata, runId }: {
  metadata: Record<string, unknown>; runId?: string;
}): JSX.Element | null {
  if (metadata.schema !== "proposal.v1" || !metadata.method_spec) return null;
  const research = object(metadata.research_context);
  const sources = Array.isArray(research.sources) ? research.sources.map(object) : [];
  return (
    <section aria-label="研究方案与依据" className="space-y-6 border-t border-mars-border pt-5">
      <div><h3 className="mb-3 text-base font-semibold">方案</h3><Value value={metadata.method_spec} /></div>
      <div><h3 className="mb-3 text-base font-semibold">验证办法</h3><Value value={metadata.decision_rule} /></div>
      {sources.length > 0 ? <div>
        <h3 className="mb-2 text-base font-semibold">研究依据</h3>
        <p className="mb-3 text-slate-400">记录 {sources.length} 个候选，采用 {sources.filter(s => s.decision === "use").length} 篇。</p>
        <Value value={research.selection_principles} />
        <div className="mt-4 space-y-4">{sources.map((source, index) => (
          <article key={String(source.source_id || source.url || index)} className="rounded border border-mars-border p-4">
            <h4 className="font-semibold">{String(source.title ?? "参考资料")} · {{
              use: "采用", reject: "未采用", defer: "待补充",
            }[String(source.decision)] ?? "已考察"}</h4>
            <p className="my-2">{String(source.reason ?? "")}</p>
            {source.method_summary ? <div className="mt-3"><strong>原方法</strong><Value value={source.method_summary} /></div> : null}
            {source.transfer ? <div className="mt-3"><strong>如何用于本方案</strong><Value value={source.transfer} /></div> : null}
            {source.limitations ? <div className="mt-3"><strong>适用条件与局限</strong><Value value={source.limitations} /></div> : null}
            <div className="mt-3 flex gap-4 text-cyan-300">
              {typeof source.url === "string" && /^https?:\/\//.test(source.url) ? <a href={source.url} target="_blank" rel="noreferrer" className="underline">来源页面</a> : null}
              {runId && typeof source.source_id === "string" && /^source_[0-9a-f]{16}$/.test(source.source_id)
                ? <a href={researchSourceUrl(runId, source.source_id)} target="_blank" rel="noreferrer" className="underline">打开已保存原文</a> : null}
            </div>
          </article>
        ))}</div>
        <div className="mt-4"><strong>结束调研的理由</strong><Value value={research.stop_reason} /></div>
        {Array.isArray(research.open_questions) && research.open_questions.length > 0
          ? <div className="mt-4"><strong>待解决的问题</strong><Value value={research.open_questions} /></div> : null}
      </div> : null}
      <p className="text-xs text-slate-400">方案收益仍需实验验证。</p>
    </section>
  );
}
