"use client";

import { useEffect, useState } from "react";
import { loadCapabilityCatalog, type CapabilityCatalog, type CatalogSkill, type CatalogTool } from "@/lib/capabilityCatalog";

const ROLE_NAMES: Record<string, string> = { idea: "研究方案", experiment: "实验设计", coding: "代码实现", execution: "实验执行", writing: "报告整理" };
const ORIGINS = { registered: "进程已注册", bridge_only: "编排层能力", runtime_bound: "按调用绑定", unbound: "尚未绑定" };
const fieldClass = "rounded-md border border-mars-border bg-mars-panel px-3 py-2 text-sm text-slate-100";
function Adapter({ value }: { value: CatalogTool["contract_adapter"] | CatalogSkill["contract_adapter"] }): JSX.Element {
  return <span className={value === "requires_host_scope" ? "text-slate-300" : "text-amber-200"}>{value === "requires_host_scope" ? "需任务授权" : value === "unsupported" ? "托管研究尚未接入" : "适配范围未知"}</span>;
}
function Hash({ name, value }: { name: string; value: string | null }): JSX.Element {
  return <p className="break-all text-xs text-slate-400">{name}：<code>{value ?? "未提供"}</code></p>;
}

export function CapabilityLibrary(): JSX.Element {
  const [catalog, setCatalog] = useState<CapabilityCatalog | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0), [query, setQuery] = useState(""), [kind, setKind] = useState("all"), [role, setRole] = useState("all");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setCatalog(null);
    void loadCapabilityCatalog(controller.signal).then((value) => { if (!controller.signal.aborted) setCatalog(value); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(failure instanceof DOMException ? "读取超时或连接中断，请检查本地服务后刷新。" : failure instanceof Error && !(failure instanceof TypeError) ? failure.message : "无法连接本地服务，请恢复连接后刷新。"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);
  const search = query.trim().toLocaleLowerCase();
  const tools = catalog?.tools.filter((tool) => (kind === "all" || kind === tool.kind) && tool.name.toLocaleLowerCase().includes(search)
    && (role === "all" || tool.roles.some((item) => item.role === role && item.effective_tool_granted))) ?? [];
  const skills = catalog?.skills.filter((skill) => (kind === "all" || kind === "skill") && role === "all" && skill.name.toLocaleLowerCase().includes(search)) ?? [];
  const roles = Array.from(new Set(catalog?.tools.flatMap((tool) => tool.roles.map((item) => item.role)) ?? []));
  return <div className="space-y-5">
    <div className="flex flex-wrap items-end gap-3"><label className="flex flex-1 flex-col gap-1 text-xs text-slate-400" htmlFor="capability-search">搜索名称<input id="capability-search" type="search" className={fieldClass} placeholder="例如 repo_reader" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <label className="flex flex-col gap-1 text-xs text-slate-400" htmlFor="capability-kind">类型<select id="capability-kind" className={fieldClass} value={kind} onChange={(event) => setKind(event.target.value)}><option value="all">全部类型</option><option value="tool">工具</option><option value="mcp_binding">MCP 接入</option><option value="skill">技能规则</option></select></label>
      <label className="flex flex-col gap-1 text-xs text-slate-400" htmlFor="capability-role">配置授予的角色<select id="capability-role" className={fieldClass} value={role} onChange={(event) => setRole(event.target.value)}><option value="all">全部角色</option>{roles.map((name) => <option key={name} value={name}>{ROLE_NAMES[name] ?? name}</option>)}</select></label>
      <button type="button" className={`${fieldClass} disabled:opacity-50`} onClick={() => setRefresh((value) => value + 1)} disabled={loading}>刷新目录</button></div>
    {loading ? <p role="status" className="py-10 text-slate-400">正在读取当前服务的能力目录…</p> : error ? <p role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">{error}</p> : catalog ? <>
      <div className="rounded-lg border border-mars-border bg-mars-panel p-4 text-sm leading-6 text-slate-300"><p>当前服务有 {catalog.tools.length} 项工具声明或注册、{catalog.skills.length} 项技能规则。依赖尚未探测，完整调用链认证尚未完成。</p><p className="text-slate-400">本页只读取配置与注册信息。任务执行前仍需通过项目授权、审批与预算检查。</p></div>
      {catalog.agent_configuration_drift || catalog.tools.some((tool) => tool.drift_status === "detected") ? <p role="status" className="rounded border border-amber-500/30 p-3 text-sm text-amber-200">检测到配置与当前进程存在差异。查看下方高级详情；修改配置不会在这里自动重载正在运行的能力。</p> : null}
      <p className="text-sm text-slate-400">筛选结果：{tools.length + skills.length} 项{role !== "all" ? "；角色筛选显示其配置授予的工具，技能选择由具体任务决定。" : ""}</p>
      {tools.length + skills.length === 0 ? <p className="rounded border border-dashed border-mars-border p-8 text-center text-slate-400">没有匹配的能力，请调整搜索或筛选条件。</p> : <div className="space-y-3">{tools.map((tool) => <article key={tool.name} className="rounded-lg border border-mars-border bg-mars-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="break-all font-mono text-sm font-semibold text-slate-100">{tool.name}</h2><span className="text-xs text-slate-400">{tool.kind === "mcp_binding" ? "MCP 接入" : "工具"} · {ORIGINS[tool.origin]}</span></div>
        <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4"><p>配置：{tool.configured_enabled === null ? "未声明" : tool.configured_enabled ? "已开启" : "已关闭"}</p><p>当前绑定：{tool.registered ? "已注册" : "未注册"}</p><p className="text-slate-400">依赖：未检查</p><p className="text-amber-200">认证：未完成</p></div>
        <p className="mt-2 text-sm"><Adapter value={tool.contract_adapter} /></p>
        <details className="mt-3 border-t border-mars-border pt-3 text-sm"><summary className="cursor-pointer text-indigo-200">高级详情 · 权限与版本指纹{tool.drift_status === "detected" ? " · 存在差异" : ""}</summary><div className="mt-3 space-y-2"><p className="text-slate-400">权限：{tool.mutation_level === "read" ? "只读" : tool.mutation_level === "write" ? "可修改" : "未知"}；审批要求：{tool.requires_approval === null ? "未知" : tool.requires_approval ? "需要" : "按任务策略"}；注册方式：{tool.effective_transport}</p><p className="text-slate-400">漂移检查：{tool.drift_status === "detected" ? tool.drift_fields.join("、") : tool.drift_status === "unknown" ? "无法比较" : "未检测到差异"}</p><ul className="space-y-1 text-xs text-slate-400">{tool.roles.filter((item) => item.effective_tool_granted).map((item) => <li key={item.role}>{ROLE_NAMES[item.role] ?? item.role}：{!item.effective_enabled ? "角色已关闭" : item.policy_intersection ? "配置权限相交，仍需任务授权" : "当前策略不允许"}</li>)}</ul><Hash name="输入 Schema" value={tool.input_schema_sha256} /><Hash name="输出 Schema" value={tool.output_schema_sha256} /></div></details>
      </article>)}{skills.map((skill) => <article key={`skill:${skill.name}`} className="rounded-lg border border-mars-border bg-mars-panel p-4"><div className="flex flex-wrap justify-between gap-2"><h2 className="break-all font-mono text-sm font-semibold">{skill.name}</h2><span className="text-xs text-slate-400">技能规则 · {skill.version ?? "版本未知"}</span></div><div className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4"><p>定义：{skill.definition_valid ? "已核验" : "无法核验"}</p><p>按任务选择</p><p className="text-slate-400">依赖：未检查</p><p className="text-amber-200">认证：未完成</p></div><p className="mt-2 text-sm"><Adapter value={skill.contract_adapter} /></p><details className="mt-3 border-t border-mars-border pt-3 text-sm"><summary className="cursor-pointer text-indigo-200">高级详情 · 所需工具</summary><p className="my-2 break-words text-xs text-slate-400">{skill.required_tools.join("、") || "无已核验的工具声明"}</p><Hash name="内容指纹" value={skill.content_sha256} /></details></article>)}</div>}
      <details className="text-xs text-slate-500"><summary className="cursor-pointer">本次目录快照</summary><p className="mt-2 break-all font-mono">{catalog.context_sha256}</p><p className="mt-1">范围：当前服务的注册与声明；不代表某次任务的临时工具绑定或执行认证。</p></details>
    </> : null}
  </div>;
}
