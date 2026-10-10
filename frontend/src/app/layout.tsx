import type { Metadata } from "next";
import "./globals.css";

import { I18nProvider } from "@/lib/i18n";
import { ProjectProvider } from "@/lib/project";

export const metadata: Metadata = {
  title: "MARS · 多 Agent 研究系统",
  description: "Multi-Agent Research System",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}): JSX.Element {
  return (
    <html lang="zh-CN">
      <body className="bg-mars-bg text-slate-100 antialiased min-h-screen">
        <I18nProvider>
          <ProjectProvider>{children}</ProjectProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
