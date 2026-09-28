import { TopBar } from "@/components/TopBar";
import { RunCatalog } from "@/components/RunCatalog";

export default function RunsPage(): JSX.Element {
  return <div className="min-h-screen bg-mars-bg"><TopBar /><main className="mx-auto max-w-6xl space-y-6 p-4 md:p-8"><header><h1 className="text-2xl font-semibold">研究任务</h1><p className="mt-2 text-sm text-slate-400">查看研究过程、处理审核与恢复已保存的任务。</p></header><RunCatalog /></main></div>;
}
