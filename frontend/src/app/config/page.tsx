import Link from "next/link";

const CONFIG_SECTIONS = [
  {
    href: "/config/agents",
    title: "Agent 模型与 Key",
    description: "配置每个 Agent 的 provider、模型、温度、token 上限和本地 API key。",
    badge: ".env.local",
  },
  {
    href: "/config/yaml",
    title: "YAML 高级编辑",
    description: "查看和编辑 configs/*.yaml，保留校验、Diff 和 audit 记录。",
    badge: "audit",
  },
];

export default function ConfigIndexPage(): JSX.Element {
  return (
    <main className="min-h-screen bg-mars-bg p-6 text-slate-100">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6">
          <Link
            href="/"
            className="mb-4 inline-flex rounded border border-mars-border bg-mars-panel2 px-3 py-2 text-sm font-medium text-slate-200 hover:bg-mars-subtle hover:text-white"
          >
            ← 返回实验台
          </Link>
          <h1 className="text-2xl font-semibold">配置</h1>
          <p className="mt-2 text-sm text-slate-500">
            这里是配置入口；具体配置放在子页面里，避免和实验运行工作台混在一起。
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
    </main>
  );
}
