import type { ResearchBudget, ResearchIssue, ResearchProject } from "./researchContracts";

export type BudgetField = { key: keyof ResearchBudget; label: string; group: "总量" | "单次请求" | "调研与迭代" | "训练"; unit: string; zero?: boolean; decimal?: boolean };
export const BUDGET_FIELDS: BudgetField[] = [
  { key: "model_requests", label: "模型请求", group: "总量", unit: "次" },
  { key: "tool_executions", label: "工具执行", group: "总量", unit: "次" },
  { key: "research_activity_seconds", label: "研究活动时间", group: "总量", unit: "秒" },
  { key: "input_tokens", label: "累计输入", group: "总量", unit: "token" },
  { key: "billed_output_tokens", label: "累计计费输出", group: "总量", unit: "token" },
  { key: "model_cost_cny", label: "模型费用预算", group: "总量", unit: "元", decimal: true },
  { key: "request_input_tokens", label: "单次输入", group: "单次请求", unit: "token" },
  { key: "request_output_tokens", label: "普通单次输出", group: "单次请求", unit: "token" },
  { key: "coding_output_tokens", label: "代码生成输出", group: "单次请求", unit: "token" },
  { key: "search_candidates", label: "检索候选资料", group: "调研与迭代", unit: "篇" },
  { key: "deep_read_papers", label: "深入阅读", group: "调研与迭代", unit: "篇" },
  { key: "concurrent_readers", label: "并发 Reader", group: "调研与迭代", unit: "个" },
  { key: "proposal_candidates", label: "方案候选", group: "调研与迭代", unit: "个" },
  { key: "implemented_candidates", label: "实施候选", group: "调研与迭代", unit: "个" },
  { key: "debate_rounds", label: "辩论轮数", group: "调研与迭代", unit: "轮" },
  { key: "automatic_iterations", label: "自动修复／演化", group: "调研与迭代", unit: "次", zero: true },
  { key: "operation_retries", label: "单操作额外重试", group: "调研与迭代", unit: "次", zero: true },
  { key: "repeated_error_limit", label: "重复错误停止阈值", group: "调研与迭代", unit: "次" },
  { key: "training_job_seconds", label: "单作业时限", group: "训练", unit: "秒" },
  { key: "concurrent_training_jobs", label: "并发训练作业", group: "训练", unit: "个" },
  { key: "max_gpus", label: "最多 GPU", group: "训练", unit: "张", zero: true },
  { key: "training_process_seconds", label: "累计训练进程时间", group: "训练", unit: "秒" },
  { key: "gpu_seconds", label: "累计 GPU 时间", group: "训练", unit: "GPU 秒" },
];
export type CommandDraft = { purpose: "check" | "train" | "evaluate"; executable: string; arguments: string; cwd: string; entries: string };
export type MetricDraft = { name: string; unit: string; direction: "minimize" | "maximize"; target: string; tolerance: string };
export type ResearchDraft = {
  name: string; goal: string; mode: "bounded_auto" | "manual"; projectId: string; displayName: string;
  code: string; knowledge: string; data: string; output: string; baseline: string; allowed: string; protected: string;
  executable: string; commands: CommandDraft[]; metrics: MetricDraft[];
  kind: "local" | "ssh"; device: "cpu" | "gpu"; connectionRef: string;
};
export type BudgetDraft = Partial<Record<keyof ResearchBudget, string>>;
export const COMMAND_LABELS = { check: "检查", train: "训练", evaluate: "评估" } as const;
export function emptyResearchDraft(): ResearchDraft {
  return { name: "", goal: "", mode: "bounded_auto", projectId: "", displayName: "", code: "", knowledge: "", data: "", output: "", baseline: "", allowed: "", protected: "",
    executable: "", commands: (["check", "train", "evaluate"] as const).map((purpose) => ({ purpose, executable: "", arguments: "", cwd: ".", entries: "" })),
    metrics: [{ name: "", unit: "", direction: "minimize", target: "", tolerance: "0" }], kind: "local", device: "cpu", connectionRef: "" };
}
export function lines(value: string): string[] { return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean); }
export function budgetDraft(defaults: ResearchBudget): BudgetDraft {
  const result: BudgetDraft = {};
  for (const { key } of BUDGET_FIELDS) {
    if (typeof defaults[key] !== "number" || !Number.isFinite(defaults[key])) throw new Error("后端预算字段不完整，不能使用未确认默认值。");
    result[key] = String(defaults[key]);
  }
  return result;
}
export function validateBudget(draft: BudgetDraft): { budget: ResearchBudget | null; issues: ResearchIssue[] } {
  const issues: ResearchIssue[] = [];
  const value: Partial<ResearchBudget> = {};
  for (const field of BUDGET_FIELDS) {
    const raw = draft[field.key]?.trim() || "";
    const number = Number(raw);
    if (!raw || !Number.isFinite(number) || number < 0 || (!field.zero && number === 0) || (!field.decimal && !Number.isSafeInteger(number))) {
      issues.push({ field: `budget.${field.key}`, code: "invalid_budget", message: `${field.label}需要${field.zero ? "非负" : "正"}${field.decimal ? "有限数值" : "整数"}` });
    } else value[field.key] = number;
  }
  const pairs: [keyof ResearchBudget, keyof ResearchBudget][] = [["deep_read_papers", "search_candidates"], ["concurrent_readers", "deep_read_papers"], ["implemented_candidates", "proposal_candidates"], ["request_input_tokens", "input_tokens"], ["request_output_tokens", "billed_output_tokens"], ["coding_output_tokens", "billed_output_tokens"], ["training_job_seconds", "training_process_seconds"]];
  for (const [small, large] of pairs) if (typeof value[small] === "number" && typeof value[large] === "number" && value[small]! > value[large]!) issues.push({ field: `budget.${small}`, code: "budget_overflow", message: `${budgetLabel(small)}不能大于${budgetLabel(large)}` });
  if ((value.repeated_error_limit ?? 0) > 2) issues.push({ field: "budget.repeated_error_limit", code: "repeated_error_limit", message: "重复错误停止阈值不能超过 2 次" });
  if ((value.operation_retries ?? 0) > 2) issues.push({ field: "budget.operation_retries", code: "retry_limit", message: "单操作额外重试不能超过 2 次" });
  return { budget: issues.length ? null : value as ResearchBudget, issues };
}
function budgetLabel(key: keyof ResearchBudget): string { return BUDGET_FIELDS.find((item) => item.key === key)?.label || key; }
function absolute(value: string): boolean { return value.startsWith("/") || /^[A-Za-z]:[\\/]/.test(value); }
function relative(value: string): boolean { return !!value && !/[\\:*?\[\]\u0000]/.test(value) && !value.startsWith("/") && value.split("/").every((part) => part !== "" && part !== "." && part !== ".."); }
export function buildResearchProject(draft: ResearchDraft): { project: ResearchProject | null; issues: ResearchIssue[] } {
  const issues: ResearchIssue[] = [];
  const issue = (field: string, message: string): void => { issues.push({ field, code: "invalid_field", message }); };
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(draft.projectId)) issue("project_id", "项目标识只能使用字母、数字、下划线、点和连字符，且以字母或数字开头");
  if (!draft.displayName.trim()) issue("display_name", "请填写项目名称");
  for (const key of ["code", "output"] as const) if (!absolute(draft[key].trim())) issue(`paths.${key}`, `${key === "code" ? "代码" : "输出"}位置需要本机完整路径`);
  for (const key of ["knowledge", "data"] as const) for (const [index, path] of lines(draft[key]).entries()) if (!absolute(path)) issue(`paths.${key}.${index}`, "每项输入需要本机完整路径，一行一个路径");
  for (const key of ["baseline", "allowed", "protected"] as const) {
    const paths = lines(draft[key]);
    if (key !== "protected" && !paths.length) issue(key === "baseline" ? "baseline_files" : "allowed_paths", key === "baseline" ? "请声明至少一个基线文件" : "请声明至少一个允许修改的文件或目录");
    if (paths.some((path) => !relative(path))) issue(key === "baseline" ? "baseline_files" : `${key}_paths`, "使用相对代码根目录的文件/目录路径，不含 ..、通配符或尾部斜杠");
  }
  const commands = draft.commands.map((command) => {
    const executable = (command.executable || draft.executable).trim();
    const prefix = `commands.${command.purpose}`;
    if (!absolute(executable)) issue(`${prefix}.executable`, `${COMMAND_LABELS[command.purpose]}命令需要执行环境内可执行文件的完整路径`);
    const entries = lines(command.entries);
    if (!entries.length || entries.some((entry) => !relative(entry))) issue(`${prefix}.entrypoint_files`, `${COMMAND_LABELS[command.purpose]}命令需声明入口或配置文件的相对路径`);
    if (command.cwd !== "." && !relative(command.cwd)) issue(`${prefix}.cwd`, "工作目录使用 . 或代码根目录内的相对目录");
    return { name: command.purpose, purpose: command.purpose, executable, arguments: lines(command.arguments), cwd: command.cwd, entrypoint_files: entries };
  });
  const names = new Set<string>();
  const metrics = draft.metrics.map((metric, index) => {
    const target = Number(metric.target), tolerance = Number(metric.tolerance);
    if (!metric.name.trim() || names.has(metric.name.trim())) issue(`metrics.${index}.name`, "指标名称必填且不能重复");
    names.add(metric.name.trim());
    if (!metric.unit.trim()) issue(`metrics.${index}.unit`, "请填写指标单位，无量纲可填写 unitless");
    if (!metric.target.trim() || !Number.isFinite(target)) issue(`metrics.${index}.target`, "目标值必须为有限数值");
    if (!metric.tolerance.trim() || !Number.isFinite(tolerance) || tolerance < 0) issue(`metrics.${index}.tolerance`, "允许误差必须为非负有限数值");
    return { name: metric.name.trim(), unit: metric.unit.trim(), direction: metric.direction, target, tolerance };
  });
  if (!metrics.length) issue("metrics", "请声明至少一个验收指标");
  if (draft.kind === "ssh" && !draft.connectionRef.trim()) issue("execution.connection_ref", "SSH 需要已保存连接的引用名称；不要填写密码或私钥");
  const project: ResearchProject = { schema_id: "research_project.v1", project_id: draft.projectId, display_name: draft.displayName.trim(),
    paths: { code: draft.code.trim(), output: draft.output.trim(), knowledge: lines(draft.knowledge), data: lines(draft.data) },
    commands, metrics, baseline_files: lines(draft.baseline), allowed_paths: lines(draft.allowed), protected_paths: lines(draft.protected),
    execution: { kind: draft.kind, device: draft.device, connection_ref: draft.kind === "ssh" ? draft.connectionRef.trim() : null } };
  return { project: issues.length ? null : project, issues };
}
export function validateResearchGoal(draft: ResearchDraft): ResearchIssue[] {
  const issues: ResearchIssue[] = [];
  if (!draft.name.trim() || draft.name.trim().length > 120) issues.push({ field: "name", code: "invalid_name", message: "研究名称需要 1–120 个字符" });
  if (!draft.goal.trim()) issues.push({ field: "goal", code: "missing_goal", message: "请描述研究目标及判定方法" });
  return issues;
}
