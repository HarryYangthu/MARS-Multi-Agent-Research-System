"use client";

import { useEffect, useState } from "react";
import { getExecutionConfig, getExecutionConfigStatus, saveExecutionConfig, type ExecutionConfigStatus } from "@/lib/api";

const INPUT = "mt-1 w-full rounded border border-mars-border bg-mars-panel px-3 py-2 text-sm";
const BUTTON = "rounded border border-mars-border px-3 py-2 text-sm hover:bg-mars-panel2 disabled:opacity-40";
const PRIMARY = "rounded border border-mars-border bg-mars-accent px-3 py-2 text-sm font-medium text-white hover:brightness-110 disabled:opacity-40";

type Remote = { host: string; port: string; user: string; key_path: string; known_hosts: string; remote_root: string; python: string; gpu_ids: string };
const EMPTY_REMOTE: Remote = { host: "", port: "22", user: "", key_path: "", known_hosts: "", remote_root: "", python: "python3", gpu_ids: "" };

const FIELD_LABELS: Record<keyof Remote, string> = {
  host: "GPU 主机", port: "SSH 端口", user: "SSH 用户", key_path: "SSH 私钥路径（本机绝对路径）",
  known_hosts: "known_hosts 路径（本机绝对路径）", remote_root: "远端工作根目录", python: "远端 Python 解释器", gpu_ids: "GPU 编号（可选，逗号分隔）",
};
const FINDINGS: Record<string, string> = { remote_runtime_not_enabled: "未启用远程执行环境", remote_dispatch_not_enabled: "远程派发尚未启用", remote_dispatch_auth_not_compatible: "当前远程认证方式与本页配置不一致", ssh_binary_missing: "本机缺少 SSH", scp_binary_missing: "本机缺少 SCP", ssh_key_path_not_file: "SSH 私钥文件不存在", known_hosts_path_not_file: "known_hosts 文件不存在" };
function findingLabel(value: string): string {
  if (value === "local_project_remote_runtime") return "此项目选择本地执行，但当前环境启用了远程执行，请先核对执行设置";
  if (value.startsWith("remote_dispatch_mismatch:")) return `实际执行环境的${FIELD_LABELS[value.split(":")[1] as keyof Remote] || "连接参数"}与此配置不一致，请在高级执行设置中核对`;
  return FINDINGS[value] || value;
}

export function ProjectGpuConfig({ project }: { project: string }): JSX.Element {
  const [device, setDevice] = useState<"local" | "remote_gpu">("local");
  const [remote, setRemote] = useState<Remote>(EMPTY_REMOTE);
  const [savedAt, setSavedAt] = useState("");
  const [status, setStatus] = useState<ExecutionConfigStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    void getExecutionConfig(project).then((config) => {
      if (!active) return;
      setDevice(config.device === "remote_gpu" ? "remote_gpu" : "local");
      if (config.remote_gpu) setRemote({ ...EMPTY_REMOTE, ...Object.fromEntries(Object.entries(config.remote_gpu).map(([k, v]) => [k, String(v)])) as unknown as Remote });
      setSavedAt(config.updated_at || "");
    }).catch(() => { if (active) setError("无法读取执行配置。"); });
    return () => { active = false; };
  }, [project]);

  async function save(): Promise<void> {
    setBusy(true); setError(""); setMessage("");
    try {
      const saved = await saveExecutionConfig(project, {
        device,
        remote_gpu: device === "remote_gpu" ? Object.fromEntries(Object.entries(remote).map(([k, v]) => [k, v.trim()])) : undefined,
      });
      setSavedAt(saved.updated_at || "");
      setMessage(device === "remote_gpu" ? "GPU 配置已保存；运行前会做就绪检查。" : "已保存：默认本地仿真。");
      setStatus(await getExecutionConfigStatus(project));
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "保存失败，请检查填写内容。");
    } finally { setBusy(false); }
  }

  async function check(): Promise<void> {
    setBusy(true); setError(""); setMessage("");
    try { setStatus(await getExecutionConfigStatus(project)); }
    catch { setError("就绪检查失败，请确认项目仍已接入。"); }
    finally { setBusy(false); }
  }

  return <section aria-label="GPU 执行配置" className="space-y-4 rounded-lg border border-mars-border p-4 text-sm">
    <div>
      <h4 className="font-semibold">执行资源</h4>
      <p className="mt-1 text-xs leading-5 text-slate-400">不选择则默认使用本地仿真；选择 GPU 后，运行前会校验远端 SSH 前置条件，未就绪会明确阻塞而不会悄悄退回本地。</p>
    </div>
    <div className="grid gap-2 sm:grid-cols-2">
      <label className={`cursor-pointer rounded-lg border p-3 ${device === "local" ? "border-indigo-400 bg-indigo-500/10" : "border-mars-border"}`}>
        <input aria-label="使用本地仿真" type="radio" name="device" className="mr-2" checked={device === "local"} onChange={() => setDevice("local")} />
        本地仿真（默认）<span className="mt-1 block pl-6 text-xs text-slate-400">在当前机器执行，与之前行为一致。</span>
      </label>
      <label className={`cursor-pointer rounded-lg border p-3 ${device === "remote_gpu" ? "border-indigo-400 bg-indigo-500/10" : "border-mars-border"}`}>
        <input aria-label="使用 GPU 资源" type="radio" name="device" className="mr-2" checked={device === "remote_gpu"} onChange={() => setDevice("remote_gpu")} />
        使用 GPU 资源<span className="mt-1 block pl-6 text-xs text-slate-400">通过 SSH 使用远端 GPU 机器执行仿真。</span>
      </label>
    </div>
    {device === "remote_gpu" ? <div className="grid gap-3 sm:grid-cols-2">
      {(Object.keys(FIELD_LABELS) as Array<keyof Remote>).map((key) => <label key={key} className="block">
        {FIELD_LABELS[key]}
        <input aria-label={FIELD_LABELS[key]} className={INPUT} value={remote[key]} disabled={busy}
          onChange={(event) => setRemote((value) => ({ ...value, [key]: event.target.value }))} />
      </label>)}
    </div> : null}
    <div className="flex flex-wrap gap-2">
      <button type="button" className={PRIMARY} disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "保存配置"}</button>
      <button type="button" className={BUTTON} disabled={busy} onClick={() => void check()}>检查就绪</button>
    </div>
    {status ? <div role="status" className="rounded border border-mars-border p-3 text-xs leading-5">
      <p className={status.ready ? "text-emerald-300" : "text-amber-300"}>{status.ready ? status.device === "remote_gpu" ? "✓ 前置配置已核对" : "✓ 使用本地环境" : "○ 配置待解决"} · 设备：{status.device === "remote_gpu" ? "远端 GPU" : "本地仿真"}</p>
      {status.device === "remote_gpu" ? <p className="mt-1 text-slate-400">本检查不连接服务器；SSH 连通性、GPU、代码和数据会在仿真启动前校验。保存配置不代表远端仿真已通过。</p> : null}
      {status.missing.length > 0 ? <p className="mt-1 text-amber-300">缺少：{status.missing.join("、")}</p> : null}
      {status.findings.length > 0 ? <ul className="mt-1 list-inside list-disc text-amber-300">{status.findings.map(finding => <li key={finding}>{findingLabel(finding)}</li>)}</ul> : null}
    </div> : null}
    {message ? <p role="status" className="text-emerald-200">{message}</p> : null}
    {error ? <p role="alert" className="text-amber-200">{error}</p> : null}
    {savedAt ? <p className="text-xs text-slate-500">上次保存：{new Date(savedAt).toLocaleString()}</p> : null}
  </section>;
}
