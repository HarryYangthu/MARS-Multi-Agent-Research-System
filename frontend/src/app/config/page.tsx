import Link from "next/link";
import { TopBar } from "@/components/TopBar";

const CONFIG_SECTIONS = [
  {
    href: "/config/agents",
    title: "模型连接",
    description: "配置研究所用的模型、服务地址和凭据，查看连接检查结果。",
    badge: "连接设置",
  },
  {
    href: "/config/yaml",
    title: "YAML 高级编辑",
    description: "查看和编辑 configs/*.yaml，保留校验、Diff 和 audit 记录。",
    badge: "高级",
  },
];

export default function ConfigIndexPage(): JSX.Element {
  return (
    <div className="min-h-screen bg-mars-bg"><TopBar /><main className="p-6 text-slate-100">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6">
          <Link
            href="/projects"
            className="mb-4 inline-flex rounded border border-mars-border bg-mars-panel2 px-3 py-2 text-sm font-medium text-slate-200 hover:bg-mars-subtle hover:text-white"
          >
            ← 返回项目
          </Link>
          <h1 className="text-2xl font-semibold">设置</h1>
          <p className="mt-2 text-sm text-slate-500">
            配置模型连接；内部参数与变更记录在高级设置中查看。
          </p>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          {CONFIG_SECTIONS.map((section) => (
            <Link
              key={section.href}
              href={section.href}
              className="rounded border border-mars-border bg-mars-panel/70 p-5 hover:border-mars-accent/60 hover:bg-mars-panel"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold">{section.title}</h2>
                  <p className="mt-2 text-sm leading-6 text-slate-400">
                    {section.description}
                  </p>
                </div>
                <span className="rounded bg-mars-accent/15 px-2 py-1 text-[11px] text-indigo-100">
                  {section.badge}
                </span>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </main></div>
  );
}
