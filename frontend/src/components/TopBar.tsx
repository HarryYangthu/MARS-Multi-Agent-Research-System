"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ProjectSwitcher } from "@/components/ProjectSwitcher";
import { RuntimeOpsPanel } from "@/components/RuntimeOpsPanel";
import { useRuntimeSnapshot } from "@/lib/dashboard";
import { useI18n } from "@/lib/i18n";
import { useProject } from "@/lib/project";
import { CLIENT_POLICY } from "@/lib/clientPolicy";

const NAVIGATION = [
  { href: "/projects", zh: "项目", en: "Projects" },
  { href: "/runs", zh: "研究任务", en: "Research" },
  { href: "/results", zh: "结果", en: "Results" },
  { href: "/config", zh: "设置", en: "Settings" },
] as const;

export function TopBar(): JSX.Element {
  const pathname = usePathname();
  const { lang, toggle } = useI18n();
  const { selectedProject } = useProject();
  const { readiness, error, loading } = useRuntimeSnapshot(selectedProject, CLIENT_POLICY.readinessRefreshMs);
  const zh = lang === "zh";
  const status = error
    ? (zh ? "连接中断" : "Connection lost")
    : loading || !readiness
      ? (zh ? "检查连接…" : "Checking connection…")
      : readiness.ready
        ? (zh ? "连接检查通过" : "Connection checked")
        : (zh ? "有配置项待处理" : "Setup needs attention");
  return (
    <header className="relative z-30 flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-mars-border bg-mars-panel px-4 py-3">
      <Link href="/projects" aria-label={zh ? "MARS 项目主页" : "MARS projects"} className="shrink-0 text-lg font-semibold tracking-wide">MARS</Link>
      <nav aria-label={zh ? "主导航" : "Main navigation"} className="flex flex-wrap items-center gap-1">
        {NAVIGATION.map(({ href, ...label }) => {
          const active = pathname === href || pathname?.startsWith(`${href}/`);
          return <Link key={href} href={href} aria-current={active ? "page" : undefined}
            className={`rounded-md px-3 py-2 text-sm ${active ? "bg-mars-accent/15 font-medium text-indigo-100" : "text-slate-400 hover:bg-mars-panel2 hover:text-white"}`}>
            {label[lang]}
          </Link>;
        })}
      </nav>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <ProjectSwitcher compact />
        {pathname !== "/runs/new" ? <Link href="/runs/new" className="rounded-md bg-mars-accent px-3 py-2 text-sm font-medium text-white hover:brightness-110">{zh ? "启动研究" : "Start research"}</Link> : null}
        <details className="relative">
          <summary className="cursor-pointer rounded-md border border-mars-border px-3 py-2 text-xs text-slate-300">{zh ? "高级" : "Advanced"}</summary>
          <div className="absolute right-0 top-full mt-2 w-72 space-y-3 rounded-lg border border-mars-border bg-mars-panel p-4 shadow-xl">
            <p className={`text-xs ${error || readiness?.ready === false ? "text-amber-200" : "text-slate-400"}`} role="status">{status}</p>
            {readiness && !error ? <p className="break-words font-mono text-xs text-slate-500">{readiness.runtime_mode} · {readiness.execution_backend}</p> : null}
            <div className="grid gap-2 text-sm text-slate-300">
              <Link href="/lab">{zh ? "实验曲线" : "Experiment curves"}</Link>
              <Link href="/context">{zh ? "上下文详情" : "Context details"}</Link>
              <Link href="/runs/new?mode=contract">{zh ? "研究合同表单" : "Research contract form"}</Link>
              <Link href="/entries">{zh ? "阶段调试入口" : "Stage debugging"}</Link>
              <Link href="/v31/runs/new">{zh ? "实验性模型发现" : "Experimental discovery"}</Link>
            </div>
            <RuntimeOpsPanel project={selectedProject} />
            <button type="button" onClick={toggle} className="rounded border border-mars-border px-3 py-2 text-xs hover:bg-mars-subtle">{zh ? "Switch to English" : "切换到中文"}</button>
          </div>
        </details>
      </div>
    </header>
  );
}
