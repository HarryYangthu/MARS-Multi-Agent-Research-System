"use client";

import { Suspense } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { TopBar } from "@/components/TopBar";
import { NewResearchWizard } from "@/components/NewResearchWizard";

const LegacyResearchForm = dynamic(() => import("@/components/LegacyResearchForm"), {
  loading: () => <p role="status" className="p-8 text-sm text-slate-400">正在加载旧项目表单…</p>,
});

export default function NewResearchPage(): JSX.Element {
  return <Suspense fallback={<p role="status" className="p-8 text-slate-400">正在打开研究向导…</p>}><NewResearchPageInner /></Suspense>;
}
function NewResearchPageInner(): JSX.Element {
  const params = useSearchParams();
  // Explicit old stage links remain advanced entries. The single main entry
  // has no query string and always opens the generic contract wizard.
  if (params?.get("mode") === "legacy" || params?.has("entrypoint")) return <LegacyResearchForm />;
  return <div className="grid h-screen grid-rows-[auto_1fr] bg-mars-bg"><TopBar />
    <main className="mx-auto w-full max-w-5xl overflow-y-auto px-4 py-6 sm:px-6 sm:py-8">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-semibold text-slate-100">新建研究</h1>
        <p className="mt-2 text-sm text-slate-400">连接已有项目，明确目标、命令与有限预算，保存一份可核对的研究计划。</p></div>
        <Link href="/runs" className="rounded-md border border-mars-border px-3 py-2 text-sm text-slate-300 hover:bg-mars-panel2">研究任务</Link></header>
      <NewResearchWizard />
    </main>
  </div>;
}
