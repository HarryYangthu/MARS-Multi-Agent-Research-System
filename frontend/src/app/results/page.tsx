import { TopBar } from "@/components/TopBar";
import { RunCatalog } from "@/components/RunCatalog";

export default function ResultsPage(): JSX.Element {
  return <div className="min-h-screen bg-mars-bg"><TopBar /><main className="mx-auto max-w-6xl space-y-6 p-4 md:p-8"><header><h1 className="text-2xl font-semibold">研究结果</h1><p className="mt-2 text-sm text-slate-400">从真实运行记录查看指标、证据与局限，导出可离线阅读的报告。</p></header><RunCatalog results /></main></div>;
}
