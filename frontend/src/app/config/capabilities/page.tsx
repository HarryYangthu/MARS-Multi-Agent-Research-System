import Link from "next/link";
import { TopBar } from "@/components/TopBar";
import { CapabilityLibrary } from "@/components/CapabilityLibrary";

export default function CapabilitiesPage(): JSX.Element {
  return <div className="min-h-screen bg-mars-bg text-slate-100"><TopBar /><main className="mx-auto max-w-6xl space-y-6 p-4 md:p-8"><header><Link href="/config" className="text-sm text-indigo-200 hover:underline">← 返回设置</Link><h1 className="mt-4 text-2xl font-semibold">研究能力</h1><p className="mt-2 text-sm text-slate-400">查看工具、技能规则和 MCP 接入的实际注册、配置差异与验证进度。</p></header><CapabilityLibrary /></main></div>;
}
