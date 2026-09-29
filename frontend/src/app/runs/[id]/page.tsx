"use client";

import { Suspense } from "react";
import dynamic from "next/dynamic";
import { useParams, useSearchParams } from "next/navigation";
import { SimpleRunDetail } from "@/components/SimpleRunDetail";

const AdvancedRunWorkbench = dynamic(() => import("@/components/AdvancedRunWorkbench"), {
  loading: () => <p className="p-8 text-sm text-slate-400">正在打开高级视图…</p>,
});
function RunView(): JSX.Element {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  if (!params?.id) return <p className="p-8 text-sm text-slate-400">正在读取任务…</p>;
  if (search?.get("view") === "advanced") return <AdvancedRunWorkbench />;
  return <SimpleRunDetail key={params.id} runId={params.id} initialAgent={search?.get("agent") ?? ""} />;
}
export default function RunDetailPage(): JSX.Element {
  return <Suspense fallback={<p className="p-8 text-sm text-slate-400">正在读取任务…</p>}><RunView /></Suspense>;
}
