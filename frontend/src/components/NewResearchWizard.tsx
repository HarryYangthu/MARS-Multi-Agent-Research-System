"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { FrozenResearchImport } from "./FrozenResearchImport";
import {
  admissionMessage, getResearchDefaults, preflightResearch, prepareResearch,
  ResearchApiError, researchRequestError, type FrozenResearch, type ResearchIssue, type ResearchPreflight,
} from "@/lib/researchContracts";
import {
  BUDGET_FIELDS, COMMAND_LABELS, budgetDraft, buildResearchProject, emptyResearchDraft, validateBudget, validateResearchGoal,
  type BudgetDraft, type CommandDraft, type MetricDraft, type ResearchDraft,
} from "@/lib/researchWizard";
import { useResearchSubmission } from "@/lib/useResearchSubmission";
import { ResearchSaveRecovery } from "./ResearchSaveRecovery";
import { useProject } from "@/lib/project";

const STEPS = ["研究目标", "项目路径", "命令与指标", "预算与预检"];
const INPUT = "w-full min-w-0 rounded-md border border-mars-border bg-mars-bg px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-400/30 disabled:opacity-50";
const BUTTON = "rounded-md border border-mars-border px-4 py-2 text-sm text-slate-200 hover:bg-mars-panel2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-300 disabled:cursor-not-allowed disabled:opacity-40";
const PRIMARY = `${BUTTON} border-transparent bg-mars-accent font-medium text-white hover:brightness-110`;
const PANEL = "rounded-lg border border-mars-border bg-mars-panel p-4 sm:p-5";
type TextDraftKey = "name" | "goal" | "projectId" | "displayName" | "code" | "knowledge" | "data" | "output" | "baseline" | "allowed" | "protected" | "executable" | "connectionRef";

export function NewResearchWizard(): JSX.Element {
  const { projects } = useProject();
  const [draft, setDraft] = useState<ResearchDraft>(emptyResearchDraft);
  const [budget, setBudget] = useState<BudgetDraft>({});
  const [step, setStep] = useState(0);
  const [defaultState, setDefaultState] = useState<"loading" | "ready" | "error">("loading");
  const [defaultError, setDefaultError] = useState("");
  const [busy, setBusy] = useState<"preflight" | "freeze" | "save" | null>(null);
  const [issues, setIssues] = useState<ResearchIssue[]>([]);
  const [notice, setNotice] = useState("");
  const [preflight, setPreflight] = useState<ResearchPreflight | null>(null);
  const [frozen, setFrozen] = useState<FrozenResearch | null>(null);
  const submission = useResearchSubmission();
  const { created } = submission;
  const [advanced, setAdvanced] = useState(false);
  const mounted = useRef(false);
  const pending = useRef<AbortController | null>(null);
  const defaultsRequest = useRef<AbortController | null>(null);
  const revision = useRef(0);
  const feedback = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    mounted.current = true;
    void loadDefaults();
    return () => { mounted.current = false; pending.current?.abort(); defaultsRequest.current?.abort(); };
  }, []);

  async function loadDefaults(): Promise<void> {
    defaultsRequest.current?.abort();
    const controller = new AbortController();
    defaultsRequest.current = controller;
    setDefaultState("loading"); setDefaultError("");
    try {
      const result = budgetDraft(await getResearchDefaults(controller.signal));
      if (!mounted.current || controller.signal.aborted) return;
      setBudget(result); setDefaultState("ready");
    } catch (cause: unknown) {
      if (!mounted.current || controller.signal.aborted) return;
      setDefaultState("error");
      setDefaultError(researchRequestError(cause));
    }
  }
  function invalidate(): void {
    revision.current += 1;
    pending.current?.abort(); pending.current = null;
    setBusy(null); setPreflight(null); setFrozen(null); setIssues([]);
    setNotice("填写内容已更新，需要重新预检与冻结。");
  }
  function update<Key extends keyof ResearchDraft>(key: Key, value: ResearchDraft[Key]): void {
    invalidate(); setDraft((current) => ({ ...current, [key]: value }));
  }
  function command(index: number, key: keyof Omit<CommandDraft, "purpose">, value: string): void {
    update("commands", draft.commands.map((item, position) => position === index ? { ...item, [key]: value } : item));
  }
  function metric(index: number, value: Partial<MetricDraft>): void {
    update("metrics", draft.metrics.map((item, position) => position === index ? { ...item, ...value } : item));
  }
  function showIssues(next: ResearchIssue[], message: string): void {
    setIssues(next); setNotice(message);
    requestAnimationFrame(() => feedback.current?.focus());
  }
  function focusIssue(field: string): void {
    const next = field.startsWith("budget") ? 3 : field.startsWith("commands") || field.startsWith("metrics") || field.startsWith("execution") ? 2
      : field.startsWith("paths") || ["baseline_files", "allowed_paths", "protected_paths"].includes(field) ? 1 : 0;
    setStep(next);
    requestAnimationFrame(() => {
      let name = field;
      let target = document.getElementById(fieldId(name));
      while (!target && name.includes(".")) { name = name.slice(0, name.lastIndexOf(".")); target = document.getElementById(fieldId(name)); }
      if (target) {
        for (const ancestor of ancestors(target)) if (ancestor instanceof HTMLDetailsElement) ancestor.open = true;
        target.focus();
      }
    });
  }
  function checked() {
    const built = buildResearchProject(draft);
    const limits = validateBudget(budget);
    const all = [...validateResearchGoal(draft), ...built.issues, ...limits.issues];
    if (all.length || !built.project || !limits.budget) {
      showIssues(all, "请集中处理下列缺项；尚未向后端发送预检。");
      return null;
    }
    return { project: built.project, budget: limits.budget, goal: draft.goal.trim(), mode: draft.mode };
  }
  async function perform(action: "preflight" | "freeze" | "save"): Promise<void> {
    if (pending.current || created || submission.pending || submission.busy) return;
    const body = checked();
    if (!body || (action !== "preflight" && !preflight?.ready) || (action === "save" && !frozen)) return;
    if (action === "save") {
      await submission.save(draft.name.trim(), frozen, frozen!.task_sha256);
      return;
    }
    const controller = new AbortController();
    const ownRevision = revision.current;
    pending.current = controller;
    const active = (): boolean => mounted.current && !controller.signal.aborted && revision.current === ownRevision && pending.current === controller;
    setBusy(action); setIssues([]); setNotice("");
    if (action === "preflight") { setPreflight(null); setFrozen(null); }
    try {
      if (action === "preflight") {
        const result = await preflightResearch(body.project, controller.signal);
        if (!active()) return;
        setPreflight(result);
        if (result.ready && result.issues.length === 0) setNotice("后端文件预检通过。尚未执行命令、调用模型或验证实验依赖。");
        else showIssues(result.issues, "后端预检发现待处理项，修正后可再次检查。");
      } else if (action === "freeze") {
        const result = await prepareResearch(body, controller.signal);
        if (!active()) return;
        setFrozen(result); setNotice("研究计划已冻结，任务尚未创建。请确认摘要后保存待执行计划。");
      }
    } catch (cause: unknown) {
      if (!active()) return;
      if (action === "freeze") { setPreflight(null); setFrozen(null); }
      showIssues(cause instanceof ResearchApiError ? cause.issues : [], researchRequestError(cause));
    } finally {
      if (active()) { setBusy(null); pending.current = null; }
    }
  }

  const field = (key: TextDraftKey, label: string, hint = "", rows?: number, errorField = key as string): JSX.Element => (
    <TextField field={errorField} label={label} hint={hint} value={String(draft[key])} rows={rows}
      onChange={(value) => update(key, value)} issues={issues} />
  );
  const disabled = busy !== null || submission.busy !== null || submission.pending !== null || created !== null || advanced;
  const validBudget = validateBudget(budget).budget;

  if (created) return <section className={`${PANEL} space-y-5`} aria-labelledby="saved-research-title">
    <div><p className="text-sm text-emerald-300">已保存 · 尚未启动</p><h2 id="saved-research-title" className="mt-2 text-xl font-semibold">{created.task}</h2>
      <p className="mt-2 text-sm text-slate-400">计划、输入指纹与预算已交给同一研究服务保存。没有调用模型或执行实验。</p></div>
    <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-4">
      <h3 className="font-medium text-amber-100">当前执行限制</h3>
      <ul className="mt-2 space-y-2 text-sm text-amber-100/90">{created.execution_admission.blockers.map((item) => <li key={item.code}>{admissionMessage(item.code, item.message)}</li>)}</ul>
      {!created.execution_admission.blockers.length ? <p className="mt-2 text-sm">未收到具体阻断原因；本向导没有发出启动请求，请在任务详情核对。</p> : null}
    </div>
    <div className="flex flex-wrap gap-3"><Link className={PRIMARY} href={`/runs/${encodeURIComponent(created.run_id)}`}>查看已保存任务</Link><Link className={BUTTON} href="/runs">返回研究任务</Link></div>
    <details className="text-xs text-slate-500"><summary className="cursor-pointer">保存标识</summary><p className="mt-2 break-all">任务：{created.run_id}</p><p className="mt-1 break-all">计划：{created.task_sha256}</p></details>
  </section>;

  return <div className="space-y-5">
    <div className="rounded-lg border border-amber-500/25 bg-amber-500/5 px-4 py-3 text-sm leading-6 text-amber-100">
      当前可预检项目、冻结计划并保存待执行任务。执行适配与预算接线尚未全部完成，保存后不会自动开始研究。
    </div>
    {!advanced ? <ResearchSaveRecovery submission={submission} onRejectedReset={invalidate} /> : null}
    <nav aria-label="研究配置步骤" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {STEPS.map((label, index) => <button key={label} type="button" disabled={disabled} aria-current={step === index ? "step" : undefined}
        onClick={() => setStep(index)} className={`${BUTTON} text-left ${step === index ? "border-indigo-400/60 bg-indigo-400/10 text-indigo-100" : "text-slate-400"}`}>
        <span className="mr-2 text-xs opacity-60">{index + 1}</span>{label}</button>)}
    </nav>
    <fieldset disabled={disabled} className={`${PANEL} min-w-0 space-y-5`}>
      <legend className="sr-only">{STEPS[step]}</legend>
      <div><h2 className="text-lg font-semibold">{STEPS[step]}</h2><p className="mt-1 text-sm text-slate-400">{[
        "说明要研究什么、何时能判断达标。项目标识用于区分记录，不会自动套用同名项目的领域规则。",
        "引用已有文件夹即可。路径由本机后端检查；数据不上传、不复制到 MARS 仓库。",
        "声明将使用的真实命令与验收指标。预检只检查文件与可执行文件存在，不会运行这些命令。",
        "预算会随计划冻结。以下是上限设置，不是已消耗用量或完成时间承诺。",
      ][step]}</p></div>
      {step === 0 ? <>
        <div className="grid gap-4 sm:grid-cols-2">{field("name", "研究名称", "用于任务列表，最多 120 字", undefined, "name")}{field("displayName", "项目名称", "例如：表格回归研究", undefined, "display_name")}</div>
        {field("goal", "研究目标", "说明问题、基线、希望改善的指标和不能改变的条件。", 5)}
        <div className="grid gap-4 sm:grid-cols-2">{field("projectId", "项目标识", "英文、数字或下划线，例如 tabular_regression", undefined, "project_id")}
          <SelectField label="研究模式" value={draft.mode} onChange={(value) => update("mode", value as ResearchDraft["mode"])}>
            <option value="bounded_auto">有上限的自动研究（计划设置）</option><option value="manual">逐阶段人工审核（计划设置）</option></SelectField></div>
        {projects.length ? <div className="border-t border-mars-border pt-4"><label className="block text-sm text-slate-300" htmlFor="existing-project-source">从已接入项目填入名称和代码位置（可选）</label>
          <select id="existing-project-source" className={`${INPUT} mt-2`} value="" onChange={(event) => {
            const chosen = projects.find((item) => item.name === event.target.value);
            if (!chosen) return;
            invalidate(); setDraft((current) => ({ ...current, projectId: chosen.name, displayName: chosen.display_name || chosen.name, code: chosen.repo_path || chosen.folder_path || "" }));
          }}><option value="">请选择，其他合同项仍需明确填写</option>{projects.map((item) => <option key={item.name} value={item.name}>{item.display_name || item.name}</option>)}</select>
          <p className="mt-2 text-xs text-slate-500">这里只复制上述字段，不会自动识别命令、数据或指标。当前尚不支持跨次复用完整项目配置；离页后未保存的草稿不会保留。</p></div> : null}
      </> : null}
      {step === 1 ? <>
        <div className="grid gap-4 md:grid-cols-2">{field("code", "代码目录", "本机完整路径，例如 /path/to/project", undefined, "paths.code")}{field("output", "输出目录", "位于保护范围之外的完整路径", undefined, "paths.output")}</div>
        <div className="grid gap-4 md:grid-cols-2">{field("knowledge", "知识资料（可为空）", "完整路径，一行一项；尚未解析的格式会在后续接入时明确阻断。", 3, "paths.knowledge")}{field("data", "数据引用（可为空）", "完整文件或目录路径，一行一项；空白表示尚未声明数据。", 3, "paths.data")}</div>
        <div className="grid gap-4 md:grid-cols-2">{field("baseline", "必须保护的基线文件", "相对代码目录，一行一个文件，例如 configs/baseline.yaml", 3, "baseline_files")}{field("allowed", "允许修改的文件或目录", "相对代码目录，一行一项，例如 src；不使用通配符。", 3, "allowed_paths")}</div>
        {field("protected", "额外保护范围（可为空）", "相对路径，一行一项。基线和保护范围始终优先于允许修改范围。", 3, "protected_paths")}
      </> : null}
      {step === 2 ? <>
        <div className="grid gap-4 sm:grid-cols-2"><SelectField label="执行位置" value={draft.kind} onChange={(value) => update("kind", value as ResearchDraft["kind"])}><option value="local">本机</option><option value="ssh">SSH（环境预检尚未接通）</option></SelectField>
          <SelectField label="设备" value={draft.device} onChange={(value) => update("device", value as ResearchDraft["device"])}><option value="cpu">CPU</option><option value="gpu">GPU（能力预检尚未接通）</option></SelectField></div>
        {draft.kind === "ssh" ? field("connectionRef", "已保存 SSH 连接的引用", "这里只接受引用名称，不填写密码或私钥。当前远程合同预检会明确阻断。", undefined, "execution.connection_ref") : null}
        {field("executable", "共用可执行文件路径", "例如 /path/to/environment/bin/python；需要指定已有环境，不会自动安装。")}
        <div className="space-y-3">{draft.commands.map((item, index) => <details key={item.purpose} open className="rounded-md border border-mars-border p-4">
          <summary className="cursor-pointer font-medium">{COMMAND_LABELS[item.purpose]}命令</summary>
          <div className="mt-4 grid gap-4 md:grid-cols-2"><TextField field={`commands.${item.purpose}.arguments`} label="参数列表" hint="一行一个参数；空行忽略。含空格的参数保留在同一行，不写 shell 引号。例如：train.py、--epochs、3 分别占一行。" value={item.arguments} rows={4} onChange={(value) => command(index, "arguments", value)} issues={issues} />
            <TextField field={`commands.${item.purpose}.entrypoint_files`} label="入口与配置文件" hint="相对代码目录，一行一项；声明实际脚本以及命令使用的配置文件。" value={item.entries} rows={4} onChange={(value) => command(index, "entries", value)} issues={issues} />
            <TextField field={`commands.${item.purpose}.cwd`} label="工作目录" hint=". 表示代码根目录，也可填相对子目录。" value={item.cwd} onChange={(value) => command(index, "cwd", value)} issues={issues} />
            <TextField field={`commands.${item.purpose}.executable`} label="单独可执行文件（可选）" hint="留空使用上方共用环境；否则填完整路径。" value={item.executable} onChange={(value) => command(index, "executable", value)} issues={issues} /></div>
        </details>)}</div>
        <section className="space-y-3 border-t border-mars-border pt-5"><h3 className="font-medium">验收指标</h3><p className="text-xs text-slate-400">填写指标单位、优化方向、目标和允许误差。它们是验收要求，不是已测得结果。</p>
          {draft.metrics.map((item, index) => <div key={index} className="space-y-3 rounded-md border border-mars-border p-4"><div className="flex items-center justify-between"><p className="text-sm text-slate-400">指标 {index + 1}</p>
            <button type="button" disabled={draft.metrics.length === 1} className="text-xs text-slate-400 underline disabled:opacity-40" onClick={() => update("metrics", draft.metrics.filter((_, position) => position !== index))} aria-label={`移除指标 ${index + 1}`}>移除</button></div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5"><TextField field={`metrics.${index}.name`} label="名称" value={item.name} onChange={(value) => metric(index, { name: value })} issues={issues} />
              <TextField field={`metrics.${index}.unit`} label="单位" hint="无量纲：unitless" value={item.unit} onChange={(value) => metric(index, { unit: value })} issues={issues} />
              <SelectField label={`指标 ${index + 1} 的方向`} value={item.direction} onChange={(value) => metric(index, { direction: value as MetricDraft["direction"] })}><option value="minimize">越小越好</option><option value="maximize">越大越好</option></SelectField>
              <TextField field={`metrics.${index}.target`} label="目标值" value={item.target} numeric onChange={(value) => metric(index, { target: value })} issues={issues} />
              <TextField field={`metrics.${index}.tolerance`} label="允许误差" value={item.tolerance} numeric onChange={(value) => metric(index, { tolerance: value })} issues={issues} /></div></div>)}
          <button type="button" className={BUTTON} onClick={() => update("metrics", [...draft.metrics, { name: "", unit: "", direction: "minimize", target: "", tolerance: "0" }])}>添加指标</button>
        </section>
      </> : null}
      {step === 3 ? <>
        {defaultState === "loading" ? <p role="status" className="text-sm text-slate-400">正在读取后端预算默认值…</p> : null}
        {defaultState === "error" ? <div role="alert" className="space-y-2 text-sm text-amber-200"><p>默认预算加载失败：{defaultError}</p><button type="button" className={BUTTON} onClick={() => void loadDefaults()}>重新读取预算</button></div> : null}
        <fieldset disabled={defaultState !== "ready"} className="min-w-0 space-y-4"><legend className="sr-only">有限预算</legend>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{BUDGET_FIELDS.filter((item) => item.group === "总量").map((item) => <TextField key={item.key} field={`budget.${item.key}`} label={`${item.label}（${item.unit}）`} value={budget[item.key] ?? ""} numeric onChange={(value) => { invalidate(); setBudget((current) => ({ ...current, [item.key]: value })); }} issues={issues} />)}</div>
        <p className="text-xs leading-6 text-slate-400">计费输出包含供应商计入的推理 token；不保存推理内容。费用预算是上限设置，实际价格与费用可能未知，不构成精确收费或固定总成本保证。研究活动时间不等于包含等待的总历时。</p>
        <details className="rounded-md border border-mars-border p-4"><summary className="cursor-pointer text-sm">调整单次请求、调研与训练上限</summary>
          {(["单次请求", "调研与迭代", "训练"] as const).map((group) => <section key={group} className="mt-5"><h3 className="mb-3 text-sm font-medium">{group}</h3><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{BUDGET_FIELDS.filter((item) => item.group === group).map((item) => <TextField key={item.key} field={`budget.${item.key}`} label={`${item.label}（${item.unit}）`} value={budget[item.key] ?? ""} numeric onChange={(value) => { invalidate(); setBudget((current) => ({ ...current, [item.key]: value })); }} issues={issues} />)}</div></section>)}
        </details>
        </fieldset>
        <section className="space-y-2 border-t border-mars-border pt-4 text-sm"><h3 className="font-medium">本次计划摘要</h3><p className="break-words text-slate-300">{draft.name || "尚未命名"} · {draft.displayName || "尚未填写项目"} · {draft.mode === "manual" ? "逐阶段人工审核" : "有上限的自动研究"}</p>
          <p className="whitespace-pre-wrap break-words text-slate-400">{draft.goal || "尚未填写研究目标"}</p>
          {validBudget ? <p className="text-slate-400">最多检索 {validBudget.search_candidates} 篇、深读 {validBudget.deep_read_papers} 篇；形成 {validBudget.proposal_candidates} 个候选，实施 {validBudget.implemented_candidates} 个。实际步骤仍须满足准入条件。</p> : null}
        </section>
      </> : null}
    </fieldset>
    <div ref={feedback} tabIndex={-1} className="scroll-mt-4 outline-none" aria-live="polite">
      {notice ? <p role={issues.length ? "alert" : "status"} className={`rounded-md border px-4 py-3 text-sm leading-6 ${issues.length || submission.pending ? "border-amber-500/30 text-amber-200" : "border-mars-border text-slate-300"}`}>{notice}</p> : null}
      {issues.length ? <section className="mt-3 rounded-md border border-amber-500/30 p-4" aria-labelledby="research-issues-title"><h2 id="research-issues-title" className="font-medium text-amber-100">需要处理 {issues.length} 项</h2>
        <ul className="mt-3 space-y-2 text-sm">{issues.map((item, index) => <li key={`${item.field}-${index}`}><button type="button" onClick={() => focusIssue(item.field)} className="text-left text-amber-100 underline decoration-amber-500/30 underline-offset-4">{item.message}</button>{item.field ? <span className="ml-2 break-all text-xs text-slate-500">{item.field}</span> : null}</li>)}</ul></section> : null}
    </div>
    {frozen ? <section className={`${PANEL} space-y-3`}><h2 className="font-medium text-emerald-200">已冻结，可保存待执行</h2><p className="text-sm text-slate-400">修改任意项目、目标或预算字段都会使此计划失效。保存后仍需在任务详情查看执行阻断原因。</p><details className="text-xs text-slate-500"><summary className="cursor-pointer">计划指纹与输入数量</summary><p className="mt-2 break-all">{frozen.task_sha256}</p><p className="mt-1">已冻结 {frozen.task.input_fingerprints.length} 项基线／入口文件指纹。</p></details></section> : null}
    <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-mars-border pt-4">
      <button type="button" className={BUTTON} disabled={disabled || step === 0} onClick={() => setStep(step - 1)}>上一步</button>
      {step < 3 ? <button type="button" className={PRIMARY} disabled={disabled} onClick={() => setStep(step + 1)}>下一步：{STEPS[step + 1]}</button> : <div className="flex flex-wrap gap-2">
        <button type="button" className={BUTTON} disabled={disabled || defaultState !== "ready"} onClick={() => void perform("preflight")}>{busy === "preflight" ? "正在检查真实文件…" : "检查项目与缺项"}</button>
        {!frozen ? <button type="button" className={PRIMARY} disabled={disabled || !preflight?.ready || Boolean(preflight.issues.length)} onClick={() => void perform("freeze")}>{busy === "freeze" ? "正在冻结计划…" : "冻结研究计划"}</button>
          : <button type="button" className={PRIMARY} disabled={disabled} onClick={() => void perform("save")}>{submission.busy === "saving" ? "正在保存…" : "保存待执行计划"}</button>}
      </div>}
    </footer>
    <p className="text-xs text-slate-500">离开本页会取消等待和后续请求；服务器已经受理的保存可能继续完成。不会自动重试保存。</p>
    <details className="border-t border-mars-border pt-4" open={advanced} onToggle={(event) => setAdvanced(event.currentTarget.open)}><summary className="cursor-pointer text-sm text-slate-400">高级接入与旧流程</summary>
      {advanced ? <div className="mt-4 space-y-4"><FrozenResearchImport submission={submission} onRejectedReset={invalidate} /><Link href="/runs/new?mode=legacy" className="text-sm text-slate-400 underline">使用旧项目／PIMC 阶段表单（未使用本通用合同）</Link></div> : null}
    </details>
  </div>;
}

function fieldId(field: string): string { return `research-${field.replaceAll(".", "-")}`; }
function* ancestors(element: HTMLElement): Generator<HTMLElement> {
  let parent = element.parentElement;
  while (parent) { yield parent; parent = parent.parentElement; }
}
function TextField({ field, label, hint = "", value, onChange, rows, numeric = false, issues }: {
  field: string; label: string; hint?: string; value: string; onChange: (value: string) => void; rows?: number; numeric?: boolean; issues: ResearchIssue[];
}): JSX.Element {
  const error = issues.find((item) => item.field === field || item.field.startsWith(`${field}.`));
  const id = fieldId(field), detail = `${id}-description`;
  return <div className="min-w-0"><label htmlFor={id} className="mb-2 block text-sm text-slate-300">{label}</label>
    {rows ? <textarea id={id} className={INPUT} value={value} rows={rows} spellCheck={false} onChange={(event) => onChange(event.target.value)} aria-invalid={Boolean(error)} aria-describedby={detail} />
      : <input id={id} className={INPUT} value={value} inputMode={numeric ? "decimal" : undefined} onChange={(event) => onChange(event.target.value)} aria-invalid={Boolean(error)} aria-describedby={detail} />}
    <div id={detail} className="mt-1 text-xs leading-5">{hint ? <p className="text-slate-500">{hint}</p> : null}{error ? <p className="text-amber-200">{error.message}</p> : null}</div></div>;
}
function SelectField({ label, value, onChange, children }: { label: string; value: string; onChange: (value: string) => void; children: React.ReactNode }): JSX.Element {
  return <label className="block min-w-0 text-sm text-slate-300">{label}<select value={value} onChange={(event) => onChange(event.target.value)} className={`${INPUT} mt-2`}>{children}</select></label>;
}
