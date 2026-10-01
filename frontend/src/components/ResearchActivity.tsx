"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getRun, getRunActivity, getRunWorkLog, type RunDetail } from "@/lib/api";
import { CLIENT_POLICY } from "@/lib/clientPolicy";
import { agentLabel, agentNodes, runActivities, statusLabel, type Activity } from "@/lib/researchActivity";

export function useResearchActivity(runId: string | null | undefined, project: string): { run: RunDetail | null; activities: Activity[]; error: string; updated: string; refresh: () => Promise<void> } {
  const [snapshot, setSnapshot] = useState<{ run: RunDetail; activities: Activity[]; updated: string } | null>(null);
  const [error, setError] = useState("");
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const refreshNow = useCallback(() => refreshRef.current(), []);
  useEffect(() => {
    setSnapshot(null); setError("");
    if (!runId) return;
    let active = true, reading = false;
    const refresh = async (): Promise<void> => {
      if (reading) return;
      reading = true;
      try {
        const [run, worklog, observation] = await Promise.all([getRun(runId), getRunWorkLog(runId), getRunActivity(runId)]);
        if (run.project !== project || worklog.project !== project || observation.project !== project) throw new Error("任务所属项目不匹配");
        if (active) { setSnapshot({ run, activities: runActivities(worklog, observation), updated: new Date().toISOString() }); setError(""); }
      } catch { if (active) setError("运行状态更新失败，正在重试；下方为最近记录。"); }
      finally { reading = false; }
    };
    refreshRef.current = refresh;
    void refresh();
    const timer = setInterval(() => void refresh(), CLIENT_POLICY.controlRefreshMs);
    return () => { active = false; refreshRef.current = async () => {}; clearInterval(timer); };
  }, [runId, project]);
  const visible = snapshot && snapshot.run.run_id === runId && snapshot.run.project === project ? snapshot : null;
  return { run: visible?.run ?? null, activities: visible?.activities ?? [], error, updated: visible?.updated ?? "", refresh: refreshNow };
}

export function ActivityRow({ activity }: { activity: Activity }): JSX.Element {
  return <div className="flex gap-3 py-2 text-xs text-slate-400">
    <span aria-hidden className={activity.status === "failed" ? "text-rose-400" : "text-indigo-300"}>◇</span>
    <div className="min-w-0 flex-1"><p className="break-words text-slate-300">{activity.agent ? `${agentLabel(activity.agent)} · ` : ""}{activity.title}</p>
      {activity.detail ? <p className="mt-1 whitespace-pre-wrap break-words leading-5 text-slate-500">{activity.detail}</p> : null}
    </div>
    <time className="shrink-0 text-[10px] text-slate-500" dateTime={activity.timestamp}>{timeLabel(activity.timestamp)}</time>
  </div>;
}
function timeLabel(value: string): string { return Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""; }

export function ResearchAgentPanel({ run, runId, activities, processing, current, error, updated }: { run: RunDetail | null; runId?: string | null; activities: Activity[]; processing: boolean; current?: string; error: string; updated: string }): JSX.Element {
  const nodes = run ? agentNodes(run) : [];
  return <aside aria-label="Agent 运行情况" className="w-full shrink-0 border-t border-mars-border bg-mars-panel/30 p-4 lg:w-80 lg:overflow-y-auto lg:border-l lg:border-t-0">
    <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-medium">Agent 运行情况</h2><span className="text-xs text-slate-400">{error ? "连接异常" : run ? statusLabel(nodes.some(node => node.state === "waiting_review") && !nodes.some(node => node.state === "running") ? "waiting_review" : run.status || "unknown") : processing ? "处理中" : "待命"}</span></div>
    {error ? <p role="status" className="mb-3 text-xs leading-5 text-amber-300">{error}</p> : null}
    <div className={`mb-3 rounded-xl border p-3 ${processing ? "border-indigo-400/50 bg-indigo-400/5" : "border-mars-border"}`}><div className="flex items-center justify-between text-sm"><span>总控 Agent</span><span className="text-xs text-slate-400">{processing ? "处理中" : "待命"}</span></div>{processing && current ? <p className="mt-2 text-xs leading-5 text-indigo-200">{current}</p> : null}</div>
    {runId && !run && !error ? <p role="status" className="text-xs text-slate-400">正在读取任务状态…</p> : null}
    <div className="space-y-2">{nodes.map(node => {
      const latest = [...activities].reverse().find(item => item.agent === node.key);
      return <div key={node.key} className={`rounded-xl border p-3 ${node.state === "running" && !error ? "border-indigo-400/50 bg-indigo-400/5" : "border-mars-border"}`}>
        <div className="flex items-center justify-between gap-2 text-sm"><span>{agentLabel(node.key)} Agent</span><span className={`text-xs ${node.state === "failed" ? "text-rose-300" : node.state === "waiting_review" ? "text-amber-300" : "text-slate-400"}`}>{statusLabel(node.state)}</span></div>
        {latest ? <p className="mt-2 break-words text-xs leading-5 text-slate-400">{latest.title}</p> : null}
      </div>;
    })}</div>
    {!runId ? <p className="mt-5 text-xs text-slate-500">研究启动后显示各 Agent 状态</p> : <Link href={`/runs/${encodeURIComponent(runId)}`} className="mt-5 block text-xs text-indigo-300 hover:underline">查看任务与审核 →</Link>}
    {updated ? <p className="mt-3 text-[10px] text-slate-600">更新于 {timeLabel(updated)}</p> : null}
  </aside>;
}

export function ActivityGroup({ activities, expanded }: { activities: Activity[]; expanded: boolean }): JSX.Element {
  const list = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const lastId = activities.at(-1)?.id;
  useEffect(() => { if (expanded && following.current && list.current) list.current.scrollTop = list.current.scrollHeight; }, [lastId, expanded]);
  const first = activities[0];
  const last = activities[activities.length - 1];
  const elapsed = Math.max(0, Math.round((Date.parse(last.ended_at || last.timestamp) - Date.parse(first.timestamp)) / 1000));
  return <details open={expanded} className="rounded-xl border border-mars-border bg-mars-panel/20 px-4 py-3">
    <summary className="cursor-pointer text-xs text-slate-400">处理记录 · {activities.length} 项{Number.isFinite(elapsed) && elapsed > 0 ? ` · ${elapsed >= 60 ? `${Math.floor(elapsed / 60)} 分 ` : ""}${elapsed % 60} 秒` : ""}</summary>
    <div ref={list} onScroll={event => { const el = event.currentTarget; following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }} className="mt-3 max-h-80 overflow-y-auto">{activities.map(activity => <ActivityRow key={activity.id} activity={activity} />)}</div>
  </details>;
}
